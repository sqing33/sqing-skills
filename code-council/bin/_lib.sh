#!/usr/bin/env bash
# _lib.sh — code-council skill 共享函数
# 所有 bin/*.sh 通过 `source "$(dirname "$0")/_lib.sh"` 引入

set -euo pipefail

# ============================================================================
# 配置
# 优先级: 环境变量 > .env > 硬编码默认
# ============================================================================

# 先定 prompt / lib 路径 (下面要用)
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 加载 .env (如果存在)
# 只在变量未设置时才覆盖 (env var 优先)
# 支持:
#   - 空行
#   - 行首 # 整行注释
#   - KEY=VALUE (inline 注释剥到 # 之前)
__config_file="${_LIB_DIR%/bin}/.env"
if [[ -f "$__config_file" ]]; then
  while IFS='=' read -r __key __value; do
    # 跳空行
    [[ -z "$__key" ]] && continue
    # 跳行首注释
    [[ "$__key" =~ ^[[:space:]]*# ]] && continue
    # 剥 inline 注释 (value 里的 #... 到行尾, 不影响 KEY 里的 #)
    __value="${__value%%#*}"
    # 去前后空白
    __key=$(printf '%s' "$__key" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
    __value=$(printf '%s' "$__value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
    # 跳过 KEY 是空 (例如行只有 "= value")
    [[ -z "$__key" ]] && continue
    # 展开 ${HOME} / $HOME (config 里写起来方便)
    # 不 eval, 避免命令注入; 只支持有限的变量引用
    __value="${__value//\$\{HOME\}/$HOME}"
    __value="${__value//\$HOME/$HOME}"
    # 只在 env 未设时填入
    if [[ -z "${!__key:-}" ]]; then
      export "$__key=$__value"
    fi
  done < "$__config_file"
  unset __key __value __config_file
fi

# 必填: provider / model, 缺了就死
# 用 inline echo + exit (避免在 _log/die 定义前调用)
__pi_review_die() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ -z "${CODE_COUNCIL_PROVIDER:-}" ]]; then
  __pi_review_die "CODE_COUNCIL_PROVIDER is not set. Edit ${_LIB_DIR%/bin}/.env or export it."
fi
if [[ -z "${CODE_COUNCIL_MODEL:-}" ]]; then
  __pi_review_die "CODE_COUNCIL_MODEL is not set. Edit ${_LIB_DIR%/bin}/.env or export it."
fi

# 选填 (有合理默认, 不是"硬编码主模型")
: "${CODE_COUNCIL_THINKING:=high}"
: "${CODE_COUNCIL_MAX_RETRIES:=3}"
: "${CODE_COUNCIL_RETRY_BACKOFF_SEC:=3}"
: "${CODE_COUNCIL_AUDIT_LOG:=${HOME}/.minimax/agents/mavis/code-council-audit.logl}"
: "${CODE_COUNCIL_PROMPTS_DIR:=${_LIB_DIR%/bin}/prompts}"
: "${CODE_COUNCIL_SCHEMA:=$_LIB_DIR/_schema.json}"
: "${CODE_COUNCIL_WORK_DIR:=.code-council}"
: "${CODE_COUNCIL_TOOLS:=}"

# ============================================================================
# 工具检查
# ============================================================================

_lib_require() {
  local missing=()
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: missing required tools: ${missing[*]}" >&2
    return 1
  fi
}

# ============================================================================
# 日志
# ============================================================================

_log() {
  echo "[$(date +'%H:%M:%S')] $*" >&2
}

die() {
  echo "ERROR: $*" >&2
  exit "${2:-1}"
}

# ============================================================================
# audit log
# ============================================================================

# 用法: _audit <stage> <verdict> <pi_skipped> <issues_count> <extra_json>
_audit() {
  local stage="$1" verdict="$2" skipped="$3" count="$4" extra="${5:-}"
  local ts
  ts="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  local line
  line=$(printf '{"ts":"%s","stage":"%s","provider":"%s","model":"%s","verdict":"%s","pi_skipped":%s,"issues":%s,"extra":%s}\n' \
    "$ts" "$stage" "$CODE_COUNCIL_PROVIDER" "$CODE_COUNCIL_MODEL" \
    "$verdict" "$skipped" "$count" "${extra:-null}")
  mkdir -p "$(dirname "$CODE_COUNCIL_AUDIT_LOG")" 2>/dev/null || true
  echo "$line" >> "$CODE_COUNCIL_AUDIT_LOG" 2>/dev/null || _log "audit log write failed: $CODE_COUNCIL_AUDIT_LOG"
}

# ============================================================================
# pi 调用
# ============================================================================

# 用法: _pi_run <sys_prompt_1> [sys_prompt_2 ...] -- <task_text>
# 所有 -- 之前的参数都作为 system prompt 按顺序追加
# 输出: 最终 JSON 字符串 (到 stdout), 错误日志到 stderr
_pi_run() {
  local system_args=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    system_args+=(--append-system-prompt "$1")
    shift
  done
  [[ "${1:-}" == "--" ]] && shift
  local task="$*"

  if [[ ${#system_args[@]} -eq 0 ]]; then
    die "_pi_run: at least one system prompt is required before --"
  fi

  local attempt=0
  while [[ $attempt -lt $CODE_COUNCIL_MAX_RETRIES ]]; do
    attempt=$((attempt + 1))
    _log "pi attempt $attempt/$CODE_COUNCIL_MAX_RETRIES"

    local raw
    local tool_args=()
    if [[ -n "$CODE_COUNCIL_TOOLS" ]]; then
      tool_args+=(--tools "$CODE_COUNCIL_TOOLS")
    fi
    # set -u 下空数组要这样展开
    raw=$(pi -p \
      --provider "$CODE_COUNCIL_PROVIDER" \
      --model "$CODE_COUNCIL_MODEL" \
      --thinking "$CODE_COUNCIL_THINKING" \
      --mode json \
      --no-builtin-tools \
      ${tool_args[@]+"${tool_args[@]}"} \
      ${system_args[@]+"${system_args[@]}"} \
      "$task" 2>/dev/null) || {
        _log "pi invocation failed (attempt $attempt)"
        sleep $((CODE_COUNCIL_RETRY_BACKOFF_SEC * attempt))
        continue
      }

    local final_text
    final_text=$(printf '%s\n' "$raw" | \
      jq -rs 'map(select(.type == "agent_end")) | last | .messages[-1].content[] | select(.type == "text") | .text' 2>/dev/null) || {
        _log "jq parse failed (attempt $attempt)"
        sleep $((CODE_COUNCIL_RETRY_BACKOFF_SEC * attempt))
        continue
      }
    _log "extracted final_text: ${#final_text} bytes"

    [[ -z "$final_text" ]] && {
      _log "empty final text (attempt $attempt)"
      sleep $((CODE_COUNCIL_RETRY_BACKOFF_SEC * attempt))
      continue
    }

    # 剥 markdown fence (```json ... ``` 或 ``` ... ```)
    # 策略: 用 python 或 sed 删除 fence 行, 保留其他行原样
    final_text=$(printf '%s' "$final_text" | \
      awk '
        /^[[:space:]]*```/ { next }
        { print }
      ' | \
      sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | \
      tr -d '\n' || true)

    # 校验
    if echo "$final_text" | jq -e . >/dev/null 2>&1; then
      # normalize: severity 大写, action 小写, agreement id 保留
      echo "$final_text" | jq '
        def norm_sev: if . == null then . else (if type == "string" then ascii_upcase else . end) end;
        .issues |= map(
          if .severity then .severity |= norm_sev else . end
        )
        | (if .agreement_with_first_pass then
            .agreement_with_first_pass |= with_entries(
              if .value | type == "array" then
                .value |= map(if .severity then .severity |= norm_sev else . end)
              else . end
            )
          else . end)
      '
      return 0
    fi

    _log "final text not valid JSON (attempt $attempt): $(printf '%.200s' "$final_text")"
    # 失败时把 raw 也 dump 一行到 stderr 方便 debug
    _log "raw output (first 500 bytes):"
    printf '%.500s\n' "$raw" >&2
    sleep $((CODE_COUNCIL_RETRY_BACKOFF_SEC * attempt))
  done

  return 1
}

# ============================================================================
# JSON 解析 / 校验
# ============================================================================

# 校验 pi 输出对应当前 stage 的 schema
# 用法: _validate <stage> <json_string>
# stage: analyze | fix-plan | review
_validate() {
  local stage="$1" json="$2"
  local validator
  validator=$(command -v check-jsonschema || command -v ajv || true)

  if [[ -z "$validator" ]]; then
    # 没装 check-jsonschema, 退到宽松校验: 检查必填字段
    _log "schema validator not found, falling back to loose validation"
    echo "$json" | jq -e '
      .verdict and .summary and (.issues | type == "array")
      and (.issues | all(.id and .severity and .category and .description))
    ' >/dev/null
    return $?
  fi

  # 严格校验
  echo "$json" | "$validator" --schema "$CODE_COUNCIL_SCHEMA" >/dev/null
}

# 从 issues 数组里过滤 escalate (未应用)
_escalate_unapplied() {
  jq -c '[.issues[] | select(.action == "escalate" and .applied != true)] | map({id, severity, description, options, pi_recommendation, pi_reasoning})'
}

# 统计 BLOCK 数 (含 unfixed)
_block_count() {
  jq '[.issues[] | select(.severity == "BLOCK")] | length'
}

# ============================================================================
# 文件写
# ============================================================================

# 原子写: 写临时文件 + rename
_atomic_write() {
  local target="$1"
  local content="$2"
  local dir
  dir=$(dirname "$target")
  mkdir -p "$dir"
  local tmp
  tmp=$(mktemp "${dir}/.tmp.XXXXXX")
  printf '%s' "$content" > "$tmp"
  mv "$tmp" "$target"
}

# ============================================================================
# 用法
# ============================================================================

_lib_usage() {
  cat <<'EOF'
_lib.sh — internal, do not call directly.
EOF
}
