#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found; skipping smoke test"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  if [ -n "${RUN_ID:-}" ]; then
    (cd "$TMP_DIR/repo" && bash "$SKILL_DIR/scripts/tmux-orch.sh" close --run "$RUN_ID" >/dev/null 2>&1 || true)
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

BIN_DIR="$TMP_DIR/bin"
REPO="$TMP_DIR/repo"
mkdir -p "$BIN_DIR" "$REPO"

cat >"$BIN_DIR/openspec-cn" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *"validate --all"*) echo "ok";;
  *"status"*) echo '{"schemaName":"test","state":"ready"}';;
  *"show"*) echo '{"name":"parallel-test","files":["proposal.md","tasks.md"]}';;
  *"instructions apply"*) echo '{"state":"ready","instruction":"apply test change","contextFiles":{"tasks.md":"openspec/changes/parallel-test/tasks.md"},"tasks":[{"status":"pending","title":"run worker"}]}';;
  *"list --json"*) echo '{"changes":[{"name":"parallel-test"}]}';;
  *) echo "{}";;
esac
EOF
chmod +x "$BIN_DIR/openspec-cn"

cat >"$BIN_DIR/claude" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'worker completed\n<<<ORCH_SUMMARY\nstatus: done\nsummary: fake worker done\nkey_changes: none\nverify: fake verify passed\nrisks: none\nnext_steps: inspect\n>>>\n'
EOF
chmod +x "$BIN_DIR/claude"

export PATH="$BIN_DIR:$PATH"
export TMUX_ORCH_OPENSPEC_CMD="$BIN_DIR/openspec-cn"
export TMUX_ORCH_CLAUDE_CMD="$BIN_DIR/claude \"\$PROMPT_TEXT\" >\"\$MSG_FILE\" 2>>\"\$LOG_FILE\""
cd "$REPO"
git init -q
git config user.email smoke@example.com
git config user.name "Smoke Test"
mkdir -p openspec/changes/parallel-test/specs/api
printf '# test\n' > AGENTS.md
printf '# proposal\n' > openspec/changes/parallel-test/proposal.md
printf '# design\n' > openspec/changes/parallel-test/design.md
printf '# tasks\n' > openspec/changes/parallel-test/tasks.md
cat > openspec/changes/parallel-test/tmux-orch.json <<'EOF'
{
  "schema_version": "tmux-openspec-parallel/v1",
  "change": "parallel-test",
  "goal": "Smoke test",
  "worker_count": 2,
  "verify_cmd": "openspec-cn validate --all --strict --no-interactive",
  "workers": [
    {
      "id": "w01",
      "title": "One",
      "scope": "First independent worker",
      "ownership": ["src/one/**"],
      "acceptance": ["worker one completes"]
    },
    {
      "id": "w02",
      "title": "Two",
      "scope": "Second independent worker",
      "ownership": ["src/two/**"],
      "acceptance": ["worker two completes"]
    }
  ]
}
EOF
git add .
git commit -qm init

bash "$SKILL_DIR/scripts/tmux-orch.sh" doctor >/dev/null
RUN_OUTPUT="$(bash "$SKILL_DIR/scripts/tmux-orch.sh" draft --goal "Smoke test" --change parallel-test --workers 2)"
RUN_ID="$(printf '%s\n' "$RUN_OUTPUT" | awk -F= '/^run_id=/{print $2}')"
bash "$SKILL_DIR/scripts/tmux-orch.sh" run --run "$RUN_ID"
if grep -R "ANTHROPIC_API_KEY\\|ANTHROPIC_AUTH_TOKEN" ".tmux-orch/state/$RUN_ID" >/dev/null 2>&1; then
  echo "worker state leaked Claude auth environment names"
  exit 1
fi

WAIT_JSON="$(bash "$SKILL_DIR/scripts/tmux-orch.sh" wait --run "$RUN_ID" --until all-done --timeout 30 --interval 1 --json)"
printf '%s\n' "$WAIT_JSON" | grep '"done": 2'
bash "$SKILL_DIR/scripts/tmux-orch.sh" integrate --run "$RUN_ID"
INTEGRATE_WAIT_JSON="$(bash "$SKILL_DIR/scripts/tmux-orch.sh" wait --run "$RUN_ID" --until integrated --timeout 30 --interval 1 --json)"
printf '%s\n' "$INTEGRATE_WAIT_JSON" | grep '"reason": "integrated"'
echo "smoke test completed integration for run $RUN_ID"
