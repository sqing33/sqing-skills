#!/usr/bin/env bash
# pi-review.sh — stage 8: pi 二次审查 (reviewer-of-reviewer)
# 输入: Mcode 的 first-pass-review + diff + plan.final
# 输出: 自己的 issues + agreement_with_first_pass + plan_coverage

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# stage 7 需要让 pi 读源码
export PI_REVIEW_TOOLS="read,grep,glob"

source "$HERE/_lib.sh"

_lib_require jq pi

# ============================================================================
# 参数
# ============================================================================

FIRST_PASS=""
DIFF=""
PLAN=""
WORK_DIR="$PI_REVIEW_WORK_DIR"
OUT=""  # 默认 ${work_dir}/stage8-review.json
CONTEXT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --first-pass)  FIRST_PASS="$2"; shift 2 ;;
    --diff)        DIFF="$2"; shift 2 ;;
    --plan)        PLAN="$2"; shift 2 ;;
    --work-dir)    WORK_DIR="$2"; shift 2 ;;
    --out)         OUT="$2"; shift 2 ;;
    --context)     CONTEXT_DIR="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
用法: pi-review.sh --first-pass <md> --diff <patch> --plan <md> [options]

stage 8: pi 二次审查。

必填:
  --first-pass  Mcode 自己写的 first-pass-review.md
  --diff        实际代码改动 (unified diff 或 patch)
  --plan        最终 plan.final.md (用于 plan_alignment 检查)

选填:
  --context <dir>   额外源码上下文目录, pi 可 read/grep/glob
  --out <file>      输出 JSON 路径, 默认 ${work_dir}/stage8-review.json

输出:
  stdout + ${out}: JSON 含 issues[], agreement_with_first_pass, plan_coverage
  exit 0: 成功
  exit 1: 参数错误
  exit 2: pi 不可用
  exit 3: schema 校验失败
EOF
      exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ -z "$FIRST_PASS" ]] && die "--first-pass is required"
[[ -z "$DIFF" ]] && die "--diff is required"
[[ -z "$PLAN" ]] && die "--plan is required"
[[ -f "$FIRST_PASS" ]] || die "first-pass not found: $FIRST_PASS"
[[ -f "$DIFF" ]] || die "diff not found: $DIFF"
[[ -f "$PLAN" ]] || die "plan not found: $PLAN"

OUT="${OUT:-$WORK_DIR/stage8-review.json}"
mkdir -p "$WORK_DIR"

# ============================================================================
# 准备
# ============================================================================

_log "stage 8: review first_pass=$FIRST_PASS diff=$DIFF plan=$PLAN"

PROMPT_FILE=$(mktemp)
{
  echo "## Plan (final)"
  echo
  echo '```markdown'
  cat "$PLAN"
  echo '```'
  echo
  echo "## Diff (unified)"
  echo
  echo '```diff'
  cat "$DIFF"
  echo '```'
  echo
  echo "## Mcode's first-pass review"
  echo
  echo '```markdown'
  cat "$FIRST_PASS"
  echo '```'
  if [[ -n "$CONTEXT_DIR" && -d "$CONTEXT_DIR" ]]; then
    echo
    echo "## Source context directory"
    echo
    echo "Additional source files are in: $CONTEXT_DIR"
    echo "Use read/grep/glob tools to inspect them as needed."
  fi
  echo
  echo "## Your task"
  echo
  echo "1. Independently review the diff (do not be biased by first-pass)."
  echo "2. For each first-pass issue, classify as confirmed | disputed."
  echo "3. Report new issues you found (added) with full schema."
  echo "4. Check plan_coverage: covered / partial / missing sections."
  echo "Output ONLY the JSON object per your system prompt."
} > "$PROMPT_FILE"
trap 'rm -f "$PROMPT_FILE"' EXIT

# ============================================================================
# 调用 pi
# ============================================================================

REVIEW_PROMPT="$PI_REVIEW_PROMPTS_DIR/review-code.md"
[[ -f "$REVIEW_PROMPT" ]] || die "missing prompt: $REVIEW_PROMPT"
BASE_PROMPT="$PI_REVIEW_PROMPTS_DIR/reviewer-base.md"
[[ -f "$BASE_PROMPT" ]] || die "missing prompt: $BASE_PROMPT"

RAW=$(_pi_run "$REVIEW_PROMPT" "$BASE_PROMPT" -- "$(cat "$PROMPT_FILE")") || {
  _log "pi 不可用"
  _audit "stage8-review" "skipped" "true" 0 '{}'
  die "pi unavailable" 2
}

# ============================================================================
# 校验
# ============================================================================

if ! _validate review "$RAW"; then
  _log "schema 校验失败"
  printf '%.200s\n' "$RAW" >&2
  _audit "stage8-review" "invalid" "false" 0 '{"reason":"schema_validate_failed"}'
  die "pi output failed schema validation" 3
fi

# ============================================================================
# 写盘 + audit
# ============================================================================

_atomic_write "$OUT" "$RAW"
VERDICT=$(echo "$RAW" | jq -r '.verdict')
COUNT=$(echo "$RAW" | jq '.issues | length')
BLOCKS=$(_block_count <<<"$RAW")
ADDED=$(echo "$RAW" | jq '.agreement_with_first_pass.added // [] | length')

_log "stage 8 done: verdict=$VERDICT issues=$COUNT blocks=$BLOCKS new_vs_firstpass=$ADDED"
_audit "stage8-review" "$VERDICT" "false" "$COUNT" "{\"blocks\":$BLOCKS,\"new_vs_firstpass\":$ADDED}"

# stdout
echo "$RAW"

# 有 BLOCK 才非 0 退出 (让 Mcode 必须看)
if [[ $BLOCKS -gt 0 ]]; then
  exit 20
fi
exit 0
