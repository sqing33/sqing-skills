#!/usr/bin/env bash
# pi-analyze.sh — stage 2: 让 pi (外部模型) 对 plan 做独立诊断
# 用法: bin/pi-analyze.sh --plan <plan.md> [--work-dir <dir>] [--out <file>]

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/_lib.sh"

_lib_require jq pi

# ============================================================================
# 参数
# ============================================================================

PLAN=""
WORK_DIR="$PI_REVIEW_WORK_DIR"
OUT=""  # 默认 ${work_dir}/stage2-issues.json

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan)        PLAN="$2"; shift 2 ;;
    --work-dir)    WORK_DIR="$2"; shift 2 ;;
    --out)         OUT="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
用法: pi-analyze.sh --plan <plan.md> [--work-dir <dir>] [--out <file>]

stage 2: pi 独立诊断 plan, 输出 issues, 不动 plan。

输出:
  ${work_dir}/stage2-issues.json   默认
  stdout: 同一份 JSON
  exit 0: 成功
  exit 1: 参数错误
  exit 2: pi 不可用 (重试 3 次仍失败)
  exit 3: 输出校验失败
EOF
      exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ -z "$PLAN" ]] && die "--plan is required"
[[ -f "$PLAN" ]] || die "plan not found: $PLAN"

OUT="${OUT:-$WORK_DIR/stage2-issues.json}"
mkdir -p "$WORK_DIR"

# ============================================================================
# 准备
# ============================================================================

_log "stage 2: analyze plan=$PLAN provider=$PI_REVIEW_PROVIDER model=$PI_REVIEW_MODEL"

# 把 plan 喂给 pi 的 prompt
PROMPT_FILE=$(mktemp)
{
  echo "## Plan to review"
  echo
  echo '```markdown'
  cat "$PLAN"
  echo '```'
  echo
  echo "## Your task"
  echo
  echo "Read the plan above. Output ONLY a JSON object matching the schema in your system prompt."
  echo "Do NOT modify the plan. Do NOT propose patches. Just report issues."
  echo
  echo "If the plan is solid, output: {\"verdict\":\"pass\",\"summary\":\"...\",\"issues\":[]}"
  echo "If there are real problems, list them in issues[] with concrete location references."
} > "$PROMPT_FILE"
trap 'rm -f "$PROMPT_FILE"' EXIT

# ============================================================================
# 调用 pi
# ============================================================================

ANALYZE_PROMPT="$PI_REVIEW_PROMPTS_DIR/analyze.md"
[[ -f "$ANALYZE_PROMPT" ]] || die "missing prompt: $ANALYZE_PROMPT"
BASE_PROMPT="$PI_REVIEW_PROMPTS_DIR/reviewer-base.md"
[[ -f "$BASE_PROMPT" ]] || die "missing prompt: $BASE_PROMPT"

RAW=$(_pi_run "$ANALYZE_PROMPT" "$BASE_PROMPT" -- "$(cat "$PROMPT_FILE")") || {
  _log "pi 3 次重试后仍失败, 标记 pi_skipped"
  _audit "stage2-analyze" "skipped" "true" 0 '{}'
  die "pi unavailable after $PI_REVIEW_MAX_RETRIES retries" 2
}

# ============================================================================
# 校验
# ============================================================================

if ! _validate analyze "$RAW"; then
  _log "schema 校验失败, 原始输出前 200 字符:"
  printf '%.200s\n' "$RAW" >&2
  _audit "stage2-analyze" "invalid" "false" 0 '{"reason":"schema_validate_failed"}'
  die "pi output failed schema validation" 3
fi

# ============================================================================
# 写盘 + audit
# ============================================================================

_atomic_write "$OUT" "$RAW"
SUMMARY=$(echo "$RAW" | jq -r '.summary')
VERDICT=$(echo "$RAW" | jq -r '.verdict')
COUNT=$(echo "$RAW" | jq '.issues | length')
BLOCKS=$(_block_count <<<"$RAW")

_log "stage 2 done: verdict=$VERDICT issues=$COUNT blocks=$BLOCKS summary=$SUMMARY"
_audit "stage2-analyze" "$VERDICT" "false" "$COUNT" "{\"blocks\":$BLOCKS}"

# stdout 也输出, 方便 pipe
echo "$RAW"

[[ "$VERDICT" != "fail" && $BLOCKS -eq 0 ]] && exit 0
exit 0  # 即便有 BLOCK 也 exit 0, 由 Mcode 决定下一步 (走 stage 3 修)
