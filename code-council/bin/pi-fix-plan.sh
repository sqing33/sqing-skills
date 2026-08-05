#!/usr/bin/env bash
# pi-fix-plan.sh — stage 3: pi 自修 plan + 升级判断
# 两轮可重入: 第一轮只修不决; 第二轮带 --user-decisions 落地最终版

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/_lib.sh"

_lib_require jq pi

# ============================================================================
# 参数
# ============================================================================

ISSUES=""
PLAN=""
USER_DECISIONS=""  # JSON 字符串, e.g. '{"ESC-1":"B","ESC-2":"A"}'
WORK_DIR="$PI_REVIEW_WORK_DIR"
OUT_ISSUES=""   # 默认 ${work_dir}/stage3-issues.json
OUT_PLAN=""     # 默认 ${work_dir}/plan.final.md (有 user_decisions 时) 或 plan.partial.md (无)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issues)         ISSUES="$2"; shift 2 ;;
    --plan)           PLAN="$2"; shift 2 ;;
    --user-decisions) USER_DECISIONS="$2"; shift 2 ;;
    --work-dir)       WORK_DIR="$2"; shift 2 ;;
    --out-issues)     OUT_ISSUES="$2"; shift 2 ;;
    --out-plan)       OUT_PLAN="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
用法: pi-fix-plan.sh --issues <stage2.json> --plan <plan.md> [options]

stage 3: pi 拿 issues 自修 plan, escalate 项抛回 Mcode → 用户。

选项:
  --user-decisions '<json>'   第二轮调用, 喂入用户对 ESC-N 的选择
                              例: '{"ESC-1":"B","ESC-2":"A"}'

输出:
  ${work_dir}/stage3-issues.json
  ${work_dir}/plan.partial.md   (无 user_decisions)
  ${work_dir}/plan.final.md     (有 user_decisions)
  stdout: stage3 issues JSON
  exit 0: 成功
  exit 1: 参数错误
  exit 2: pi 不可用
  exit 3: schema 校验失败
  exit 4: user_decisions 里有未在 issues 出现的 ESC id
EOF
      exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ -z "$ISSUES" ]] && die "--issues is required"
[[ -z "$PLAN" ]] && die "--plan is required"
[[ -f "$ISSUES" ]] || die "issues file not found: $ISSUES"
[[ -f "$PLAN" ]] || die "plan not found: $PLAN"

mkdir -p "$WORK_DIR"

# ============================================================================
# 校验 user_decisions
# ============================================================================

if [[ -n "$USER_DECISIONS" ]]; then
  echo "$USER_DECISIONS" | jq -e 'type == "object"' >/dev/null \
    || die "--user-decisions must be a JSON object"

  # 检查每个 key 是否在 issues 里存在
  for esc_id in $(echo "$USER_DECISIONS" | jq -r 'keys[]'); do
    if ! jq -e --arg id "$esc_id" '[.issues[] | select(.id == $id)] | length > 0' "$ISSUES" >/dev/null; then
      die "user_decisions references unknown ESC id: $esc_id"
    fi
  done
fi

# ============================================================================
# 路径
# ============================================================================

if [[ -n "$USER_DECISIONS" ]]; then
  OUT_ISSUES="${OUT_ISSUES:-$WORK_DIR/stage3-final-issues.json}"
  OUT_PLAN="${OUT_PLAN:-$WORK_DIR/plan.final.md}"
  STAGE_TAG="stage3-fix-final"
else
  OUT_ISSUES="${OUT_ISSUES:-$WORK_DIR/stage3-issues.json}"
  OUT_PLAN="${OUT_PLAN:-$WORK_DIR/plan.partial.md}"
  STAGE_TAG="stage3-fix-partial"
fi

# ============================================================================
# 准备
# ============================================================================

_log "$STAGE_TAG: plan=$PLAN issues=$ISSUES decisions=${USER_DECISIONS:-<none>}"

PROMPT_FILE=$(mktemp)
{
  echo "## Current plan"
  echo
  echo '```markdown'
  cat "$PLAN"
  echo '```'
  echo
  echo "## Stage 2 issues to address"
  echo
  echo '```json'
  cat "$ISSUES"
  echo '```'
  echo
  if [[ -n "$USER_DECISIONS" ]]; then
    echo "## User decisions (apply these escalates)"
    echo
    echo '```json'
    echo "$USER_DECISIONS"
    echo '```'
    echo
    echo "Apply the user-chosen options to updated_plan. For escalates NOT in this list, leave them as action=escalate, applied=false."
  else
    echo "## Your task"
    echo
    echo "For each issue, decide action=fix | escalate | note."
    echo "Apply fix-action changes directly to updated_plan (atomic, minimal)."
    echo "For escalate-action issues, you MUST include options[], pi_recommendation, pi_reasoning in the issue."
  fi
  echo
  echo "Output ONLY the JSON object described in your system prompt."
} > "$PROMPT_FILE"
trap 'rm -f "$PROMPT_FILE"' EXIT

# ============================================================================
# 调用 pi
# ============================================================================

FIX_PROMPT="$PI_REVIEW_PROMPTS_DIR/fix-plan.md"
[[ -f "$FIX_PROMPT" ]] || die "missing prompt: $FIX_PROMPT"
BASE_PROMPT="$PI_REVIEW_PROMPTS_DIR/reviewer-base.md"
[[ -f "$BASE_PROMPT" ]] || die "missing prompt: $BASE_PROMPT"

RAW=$(_pi_run "$FIX_PROMPT" "$BASE_PROMPT" -- "$(cat "$PROMPT_FILE")") || {
  _log "pi 不可用"
  _audit "$STAGE_TAG" "skipped" "true" 0 '{}'
  die "pi unavailable" 2
}

# ============================================================================
# 校验
# ============================================================================

if ! _validate fix-plan "$RAW"; then
  _log "schema 校验失败"
  printf '%.200s\n' "$RAW" >&2
  _audit "$STAGE_TAG" "invalid" "false" 0 '{"reason":"schema_validate_failed"}'
  die "pi output failed schema validation" 3
fi

# updated_plan 必须存在
UPDATED_PLAN=$(echo "$RAW" | jq -r '.updated_plan // empty')
[[ -z "$UPDATED_PLAN" ]] && die "pi output missing updated_plan"

# ============================================================================
# 写盘 + audit
# ============================================================================

_atomic_write "$OUT_ISSUES" "$RAW"
_atomic_write "$OUT_PLAN" "$UPDATED_PLAN"

VERDICT=$(echo "$RAW" | jq -r '.verdict')
COUNT=$(echo "$RAW" | jq '.issues | length')
ESC_UNAPPLIED=$(echo "$RAW" | _escalate_unapplied | jq 'length')
CHANGE_COUNT=$(echo "$RAW" | jq '.change_log | length // 0')

_log "$STAGE_TAG done: verdict=$VERDICT issues=$COUNT changes=$CHANGE_COUNT unfixed_escalates=$ESC_UNAPPLIED"
_audit "$STAGE_TAG" "$VERDICT" "false" "$COUNT" "{\"changes\":$CHANGE_COUNT,\"unfixed_escalates\":$ESC_UNAPPLIED}"

# stdout 输出 issues
echo "$RAW"

# 第一轮还有 unfixed escalates → exit 10 (信号, 不是错误)
if [[ -z "$USER_DECISIONS" && $ESC_UNAPPLIED -gt 0 ]]; then
  _log "first round done, $ESC_UNAPPLIED escalates need user input"
  exit 10
fi

exit 0
