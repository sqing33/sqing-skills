#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

class CmdError extends Error {}

const SCRIPT_FILE = fileURLToPath(import.meta.url);
const SRC_DIR = path.dirname(SCRIPT_FILE);
const SKILL_DIR = path.dirname(SRC_DIR);
const SKILL_NAME = path.basename(SKILL_DIR);
const IS_OPENSPEC = SKILL_NAME.includes("openspec");
const REPO_ROOT = path.resolve(SKILL_DIR, "../../..");
const ORCH_PLAN_FILE = path.join(REPO_ROOT, "ORCH_PLAN.md");
const WORKTREE_ROOT = path.join(REPO_ROOT, ".worktree-tmux-orch");
const STATE_DIR = path.join(SKILL_DIR, ".state");
const LOG_DIR = path.join(SKILL_DIR, ".logs");
const RESULT_DIR = path.join(SKILL_DIR, ".results");
const REPORT_DIR = path.join(SKILL_DIR, ".reports");
const CONFIG_FILE = path.join(SKILL_DIR, "config.toml");
const DEFAULT_VERIFY_CMD = IS_OPENSPEC ? "-" : "openspec validate --all";
const MAX_RUNNING_WORKERS = IS_OPENSPEC ? 1 : Number(process.env.TMUX_ORCH_MAX_RUNNING_WORKERS || "8");
const OPENSPEC_NPX_CMD = "npx -y @studyzy/openspec-cn";
const SUMMARY_MARKER_BEGIN = "<<<ORCH_SUMMARY";
const SUMMARY_MARKER_END = ">>>";
const SUMMARY_FIELDS = ["status", "summary", "key_changes", "verify", "risks", "next_steps"];
const WORKER_TERMINAL = new Set(["done", "failed", "blocked"]);
const TABLE_COLUMNS = [
  "run_id",
  "mode",
  "worker_id",
  "task_title",
  "task_scope",
  "ownership",
  "strategy",
  "base_branch",
  "worker_branch",
  "worktree_path",
  "verify_cmd",
  "status",
  "session_id",
  "result_ref",
  "notes",
];

function ensureDirs() {
  for (const dir of [STATE_DIR, LOG_DIR, RESULT_DIR, REPORT_DIR, WORKTREE_ROOT]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function isoNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

function runIdNow() {
  const d = new Date();
  const stamp = [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0"),
    "-",
    String(d.getHours()).padStart(2, "0"),
    String(d.getMinutes()).padStart(2, "0"),
    String(d.getSeconds()).padStart(2, "0"),
  ].join("");
  return `${stamp}-${Math.floor(Math.random() * 0x10000).toString(16).padStart(4, "0")}`;
}

function rel(p) {
  const abs = path.resolve(p);
  const relative = path.relative(REPO_ROOT, abs);
  return relative && !relative.startsWith("..") && !path.isAbsolute(relative) ? relative : abs;
}

function shellQuote(value) {
  const s = String(value ?? "");
  if (s === "") return "''";
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

function sh(args, options = {}) {
  const proc = spawnSync(args[0], args.slice(1), {
    cwd: options.cwd || REPO_ROOT,
    encoding: "utf8",
    env: options.env || process.env,
  });
  const stdout = proc.stdout || "";
  const stderr = proc.stderr || "";
  const status = proc.status ?? 1;
  if (options.check !== false && status !== 0) {
    throw new CmdError(
      `command failed (${status}): ${args.join(" ")}\nstdout:\n${stdout}\nstderr:\n${stderr}`,
    );
  }
  return { status, stdout, stderr };
}

function shBash(command, options = {}) {
  return sh(["bash", "-lc", command], options);
}

function stripTomlComment(line) {
  let out = "";
  let inString = false;
  let escaped = false;
  for (const ch of line) {
    if (ch === "\\" && inString && !escaped) {
      escaped = true;
      out += ch;
      continue;
    }
    if (ch === '"' && !escaped) {
      inString = !inString;
      out += ch;
      continue;
    }
    if (ch === "#" && !inString) break;
    out += ch;
    escaped = false;
  }
  return out.trim();
}

function parseTomlScalar(raw) {
  const value = raw.trim();
  if (!value) return "";
  if (value.startsWith('"') && value.endsWith('"')) {
    return value.slice(1, -1).replace(/\\"/g, '"').replace(/\\n/g, "\n").replace(/\\\\/g, "\\");
  }
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+$/.test(value)) return Number(value);
  return value;
}

function loadSimpleToml(file) {
  const data = {};
  if (!fs.existsSync(file)) return data;
  let current = data;
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
  lines.forEach((raw, index) => {
    const line = stripTomlComment(raw);
    if (!line) return;
    if (line.startsWith("[") && line.endsWith("]")) {
      current = data;
      for (const part of line.slice(1, -1).split(".")) {
        const key = part.trim();
        if (!key) throw new CmdError(`invalid TOML section at ${file}:${index + 1}`);
        if (!current[key]) current[key] = {};
        if (typeof current[key] !== "object") throw new CmdError(`TOML section conflicts at ${file}:${index + 1}`);
        current = current[key];
      }
      return;
    }
    const eq = line.indexOf("=");
    if (eq < 0) throw new CmdError(`invalid TOML assignment at ${file}:${index + 1}`);
    current[line.slice(0, eq).trim()] = parseTomlScalar(line.slice(eq + 1));
  });
  return data;
}

function loadConfig() {
  const raw = loadSimpleToml(CONFIG_FILE);
  const selectedModel = String(raw.selected_model || "default").trim() || "default";
  const profilesRaw = raw.profiles && typeof raw.profiles === "object" ? raw.profiles : {};
  const profiles = {};
  for (const [name, item] of Object.entries(profilesRaw)) {
    if (!item || typeof item !== "object") throw new CmdError(`invalid profile ${name} in ${CONFIG_FILE}`);
    profiles[name] = {
      name,
      base_url: String(item.base_url || "").trim(),
      api_key: String(item.api_key || "").trim(),
      model: String(item.model || "").trim(),
    };
  }
  if (Object.keys(profiles).length === 0) profiles.default = { name: "default", base_url: "", api_key: "", model: "" };
  if (!profiles[selectedModel]) throw new CmdError(`selected_model ${selectedModel} not found in ${CONFIG_FILE}`);
  return { selectedModel, profiles };
}

function activeProfile() {
  const { selectedModel, profiles } = loadConfig();
  return profiles[selectedModel];
}

function statePath(runId) {
  return path.join(STATE_DIR, `${runId}.json`);
}

function loadState(runId) {
  const file = statePath(runId);
  if (!fs.existsSync(file)) throw new CmdError(`run state not found: ${runId}`);
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function saveState(state) {
  state.updated_at = isoNow();
  fs.writeFileSync(statePath(state.run_id), `${JSON.stringify(state, null, 2)}\n`);
}

function appendEvent(state, kind, detail) {
  if (!Array.isArray(state.events)) state.events = [];
  state.events.push({ ts: isoNow(), kind, detail });
}

function gitRepoRoot() {
  const proc = sh(["git", "rev-parse", "--show-toplevel"], { check: false });
  return proc.status === 0 ? proc.stdout.trim() : "";
}

function requireGitRepo(commandName) {
  const root = gitRepoRoot();
  if (!root) throw new CmdError(`${commandName} requires a Git worktree, but ${REPO_ROOT} is not a Git repository.`);
  return root;
}

function gitCurrentBranch() {
  requireGitRepo("draft");
  const proc = sh(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], { check: false });
  const out = proc.stdout.trim();
  if (proc.status === 0 && out) return out;
  throw new CmdError("could not determine the current branch. Ensure the repository is on a local branch.");
}

function branchExists(branch) {
  return sh(["git", "show-ref", "--verify", "--quiet", `refs/heads/${branch}`], { check: false }).status === 0;
}

function ensureToolExists(tool) {
  return sh(["bash", "-lc", `command -v ${shellQuote(tool)} >/dev/null 2>&1`], { check: false }).status === 0;
}

function workerRuntimeEnvVar(runtime) {
  return `TMUX_ORCH_${runtime.toUpperCase()}_CMD`;
}

function ensureRuntimeAvailable(runtime) {
  if (process.env[workerRuntimeEnvVar(runtime)]) return;
  if (!ensureToolExists(runtime)) throw new CmdError(`worker runtime not found: ${runtime} (or set ${workerRuntimeEnvVar(runtime)})`);
}

function slugify(text, fallback = "item", limit = 28) {
  const value = String(text || "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
  return (value || fallback).slice(0, limit).replace(/-+$/g, "") || fallback;
}

function splitGoalTasks(goal) {
  const lines = String(goal)
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^[-*]\s+/, "").replace(/^\d+[.)]\s+/, ""))
    .filter(Boolean);
  return lines.length > 1 ? lines : [goal.trim()];
}

function parseTaskEntry(task) {
  const parts = task.split(/\s+-\s+|\s+--\s+|：|:/, 2).map((v) => v.trim()).filter(Boolean);
  if (parts.length >= 2) return { title: parts[0], scope: parts[1] };
  return { title: task.trim().slice(0, 80) || "Task", scope: task.trim() || "Implement requested change" };
}

function decideWorkers(tasks, explicitWorkers) {
  if (IS_OPENSPEC) return 1;
  if (explicitWorkers) return Math.max(1, Math.min(Number(explicitWorkers), 32));
  return Math.max(1, Math.min(tasks.length, 8));
}

function defaultWorkerRow({ runId, workerId, taskTitle, taskScope, baseBranch, strategy = "-" }) {
  const suffix = workerId === "merge" ? "integrate" : `${workerId}-${slugify(taskTitle)}`;
  return {
    run_id: runId,
    mode: "split-task",
    worker_id: workerId,
    task_title: taskTitle,
    task_scope: taskScope,
    ownership: taskScope,
    strategy,
    base_branch: baseBranch,
    worker_branch: `orchestrator/${runId}/${suffix}`,
    worktree_path: `.worktree-tmux-orch/${runId}/${suffix}`,
    verify_cmd: DEFAULT_VERIFY_CMD,
    status: "planned",
    session_id: "-",
    result_ref: "-",
    notes: "-",
  };
}

function buildWorkerRows({ runId, goal, tasks, workers, baseBranch }) {
  const rows = [];
  for (let i = 0; i < workers; i += 1) {
    const task = tasks[i] || goal;
    const parsed = parseTaskEntry(task);
    const workerId = `w${String(i + 1).padStart(2, "0")}`;
    rows.push(defaultWorkerRow({
      runId,
      workerId,
      taskTitle: IS_OPENSPEC ? "Implement OpenSpec change" : parsed.title,
      taskScope: IS_OPENSPEC ? goal : parsed.scope,
      baseBranch,
      strategy: IS_OPENSPEC ? "openspec-single-worker" : "parallel-worker",
    }));
  }
  return rows;
}

function escapeCell(value) {
  return String(value ?? "-").replace(/\n/g, "<br>").replace(/\|/g, "\\|");
}

function renderPlanMarkdown(state) {
  const lines = [];
  lines.push(`# Tmux Claude Workers Plan: ${state.run_id}`, "");
  lines.push(`- goal: ${state.goal}`);
  lines.push(`- mode: ${state.mode}`);
  lines.push(`- execution_kind: ${state.execution_kind}`);
  lines.push(`- worker_runtime: ${state.worker_runtime}`);
  lines.push(`- worker_profile: ${state.worker_profile || "default"}`);
  lines.push(`- base_branch: ${state.base_branch}`);
  lines.push(`- session_name: ${state.session_name}`);
  if (IS_OPENSPEC && state.openspec_context?.change_name) lines.push(`- openspec_change: ${state.openspec_context.change_name}`);
  lines.push("", "## Workers", "");
  lines.push(`| ${TABLE_COLUMNS.join(" | ")} |`);
  lines.push(`| ${TABLE_COLUMNS.map(() => "---").join(" | ")} |`);
  for (const row of state.workers || []) {
    lines.push(`| ${TABLE_COLUMNS.map((col) => escapeCell(row[col] ?? "-")).join(" | ")} |`);
  }
  lines.push("");
  return `${lines.join("\n")}\n`;
}

function writePlan(state) {
  fs.writeFileSync(ORCH_PLAN_FILE, renderPlanMarkdown(state));
}

function workerPaths(runId, workerId) {
  return {
    prompt: path.join(STATE_DIR, runId, `${workerId}.prompt.txt`),
    script: path.join(STATE_DIR, runId, `${workerId}.run.sh`),
    log: path.join(LOG_DIR, runId, `${workerId}.log`),
    debug: path.join(LOG_DIR, runId, `${workerId}.debug.log`),
    done: path.join(LOG_DIR, runId, `${workerId}.done`),
    message: path.join(RESULT_DIR, runId, `${workerId}.md`),
  };
}

function appendNote(notes, message) {
  if (!notes || notes === "-") return message;
  if (notes.split(";").map((v) => v.trim()).includes(message)) return notes;
  return `${notes}; ${message}`;
}

function buildClaudeEnv(profile) {
  const env = {};
  if (profile.base_url) env.ANTHROPIC_BASE_URL = profile.base_url;
  if (profile.api_key) {
    env.ANTHROPIC_API_KEY = profile.api_key;
    env.ANTHROPIC_AUTH_TOKEN = profile.api_key;
  }
  if (profile.model) {
    env.ANTHROPIC_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_HAIKU_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_OPUS_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_SONNET_MODEL = profile.model;
  }
  return env;
}

function envPrefix(profile) {
  return Object.entries(buildClaudeEnv(profile)).map(([k, v]) => `${k}=${shellQuote(v)}`).join(" ");
}

function buildWorkerCommand(profile) {
  const custom = String(process.env.TMUX_ORCH_CLAUDE_CMD || "").trim();
  if (custom) return custom;
  const parts = [];
  const prefix = envPrefix(profile);
  if (prefix) parts.push(prefix);
  parts.push("claude --bare --no-session-persistence -p --dangerously-skip-permissions --permission-mode bypassPermissions --output-format text --debug-file \"$DEBUG_FILE\"");
  if (profile.model) parts.push(`--model ${shellQuote(profile.model)}`);
  parts.push("\"$PROMPT_TEXT\" >\"$MSG_FILE\" 2>>\"$LOG_FILE\"");
  return parts.join(" ");
}

function resolveOpenSpecCommand() {
  if (process.env.TMUX_ORCH_OPENSPEC_CMD) return process.env.TMUX_ORCH_OPENSPEC_CMD.trim();
  if (ensureToolExists("openspec-cn")) return "openspec-cn";
  if (ensureToolExists("openspec")) return "openspec";
  return OPENSPEC_NPX_CMD;
}

function openspecInitialized() {
  return fs.existsSync(path.join(REPO_ROOT, "openspec")) && fs.existsSync(path.join(REPO_ROOT, "AGENTS.md"));
}

function loadOpenSpecJson(args) {
  const cmd = `${resolveOpenSpecCommand()} ${args.map(shellQuote).join(" ")}`;
  const proc = shBash(cmd, { check: false });
  if (proc.status !== 0) throw new CmdError(shortenText((proc.stderr || proc.stdout || `${cmd} failed`).replace(/\s+/g, " "), 400));
  try {
    return JSON.parse(proc.stdout);
  } catch (error) {
    throw new CmdError(`invalid OpenSpec JSON output for ${args.join(" ")}: ${error.message}`);
  }
}

function selectOpenSpecChange() {
  if (process.env.TMUX_ORCH_OPENSPEC_CHANGE) return process.env.TMUX_ORCH_OPENSPEC_CHANGE.trim();
  const data = loadOpenSpecJson(["list", "--json"]);
  const changes = Array.isArray(data.changes) ? data.changes : [];
  if (changes.length === 1 && changes[0]?.name) return String(changes[0].name);
  if (changes.length === 0) throw new CmdError("no active OpenSpec change found; create one before running tmux-claude-openspec-worker");
  const names = changes.map((item) => item?.name).filter(Boolean).join(", ");
  throw new CmdError(`multiple active OpenSpec changes found; set TMUX_ORCH_OPENSPEC_CHANGE to one of: ${names}`);
}

function validateOpenSpecParent() {
  const cmd = resolveOpenSpecCommand();
  if (!openspecInitialized()) return { ok: false, detail: `OpenSpec not initialized; run \`${cmd} init --tools codex,claude\` first` };
  const proc = shBash(`${cmd} validate --all --strict --no-interactive`, { check: false });
  const stdout = proc.stdout.replace(/\s+/g, " ").trim();
  const stderr = proc.stderr.replace(/\s+/g, " ").trim();
  if (proc.status === 0) return { ok: true, detail: shortenText(stdout || "OpenSpec validate ok", 240) };
  return { ok: false, detail: shortenText(stderr || stdout || `${cmd} validate failed`, 400) };
}

function buildOpenSpecContext() {
  const changeName = selectOpenSpecChange();
  return {
    change_name: changeName,
    status: loadOpenSpecJson(["status", "--change", changeName, "--json"]),
    apply: loadOpenSpecJson(["instructions", "apply", "--change", changeName, "--json"]),
  };
}

function workerPrompt(state, row) {
  const profile = activeProfile();
  if (IS_OPENSPEC) {
    const ctx = state.openspec_context || {};
    const apply = ctx.apply || {};
    const status = ctx.status || {};
    const contextFiles = apply.contextFiles && typeof apply.contextFiles === "object" ? apply.contextFiles : {};
    const contextLines = Object.entries(contextFiles).filter(([, v]) => v).map(([k, v]) => `- ${k}: ${v}`);
    const tasks = Array.isArray(apply.tasks) ? apply.tasks.slice(0, 12).map((item) => {
      if (item && typeof item === "object") return `- [${item.status || "-"}] ${item.title || item.text || item.description || "-"}`;
      return `- ${item}`;
    }) : [];
    return `
你是单线程 worker ${row.worker_id}，上层主控代理负责 OpenSpec，实际实现全部由你完成。

全局目标:
${state.goal}

你的任务:
- task_title: ${row.task_title}
- task_scope: ${row.task_scope}
- ownership: ${row.ownership || "-"}
- branch: ${row.worker_branch}
- verify_cmd: ${row.verify_cmd || "-"}
- openspec_change: ${ctx.change_name || "-"}
- worker_profile: ${profile.name}
- worker_model: ${profile.model || "(claude default)"}

父代理准备好的 OpenSpec 上下文:
- schema: ${status.schemaName || "-"}
- apply_state: ${apply.state || "-"}
- instruction: ${apply.instruction || "-"}
- context_files:
${(contextLines.length ? contextLines : ["- contextFiles 未提供；请阅读 openspec/changes/<change>/ 下的产出物"]).join("\n")}
- tasks:
${(tasks.length ? tasks : ["- tasks 列表为空；按 OpenSpec 产出物和 task_scope 实现"]).join("\n")}

约束:
1) 先读取 OpenSpec 上下文，再开始写代码。
2) 以 OpenSpec 产出物为实现契约；如实现与 OpenSpec 冲突，停止并说明冲突点。
3) 仅在当前分支内工作，不要切到其他分支。
4) 你负责代码生成、代码实现和项目级验证；不要创建、归档或重写 OpenSpec 工作流。
5) 如 verify_cmd 不存在或无法运行，明确写出原因，不要编造结果。
6) 不要输出 Markdown 本地文件链接，不要使用 file:// URI；引用文件时只写纯文本路径。

最终请在回复末尾附加结构化摘要，严格使用以下格式，每个字段单行：
${SUMMARY_MARKER_BEGIN}
status: done|blocked|failed
summary: 一句话总结
key_changes: 主要改动；可用分号分隔多点
verify: 验证结果；可用分号分隔多点
risks: 风险与后续建议；可用分号分隔多点
next_steps: 建议下一步；可用分号分隔多点
${SUMMARY_MARKER_END}
`.trim() + "\n";
  }

  return `
你是并行 worker ${row.worker_id}，上层主控代理只负责规划与任务拆分，实际实现全部由你完成。

全局目标:
${state.goal}

你的任务:
- task_title: ${row.task_title}
- task_scope: ${row.task_scope}
- ownership: ${row.ownership || "-"}
- branch: ${row.worker_branch}
- verify_cmd: ${row.verify_cmd || DEFAULT_VERIFY_CMD}
- worker_profile: ${profile.name}
- worker_model: ${profile.model || "(claude default)"}

约束:
1) 只处理当前 worker 的 task_scope，不要自行扩展到其他子任务。
2) 仅在当前分支内工作，不要切到其他分支。
3) 可以直接修改代码、文档和测试；完成后运行 verify_cmd。
4) 如 verify_cmd 不存在或无法运行，明确写出原因，不要编造结果。
5) 不要输出 Markdown 本地文件链接，不要使用 file:// URI；引用文件时只写纯文本路径。

最终请在回复末尾附加结构化摘要，严格使用以下格式，每个字段单行：
${SUMMARY_MARKER_BEGIN}
status: done|blocked|failed
summary: 一句话总结
key_changes: 主要改动；可用分号分隔多点
verify: 验证结果；可用分号分隔多点
risks: 风险与后续建议；可用分号分隔多点
next_steps: 建议下一步；可用分号分隔多点
${SUMMARY_MARKER_END}
`.trim() + "\n";
}

function integrationPrompt(state, row, sourceRows) {
  const profile = activeProfile();
  const branches = sourceRows.map((item) => `- ${item.worker_id}: branch=${item.worker_branch}; ownership=${item.ownership || "-"}; summary=${item.task_title || "-"}`).join("\n");
  return `
你是整合 worker ${row.worker_id}。上层主控代理只负责规划，代码整合也由你完成。

全局目标:
${state.goal}

待整合分支:
${branches}

当前整合分支: ${row.worker_branch}
verify_cmd: ${row.verify_cmd || DEFAULT_VERIFY_CMD}
worker_profile: ${profile.name}
worker_model: ${profile.model || "(claude default)"}

约束:
1) 将已完成的子 worker 分支整合到当前分支，不要让父代理手动整合。
2) 优先保留各 worker 的 ownership 边界；只有在整合需要时才修改交界处。
3) 如发生冲突，请在当前分支内解决，并在总结中写清冲突点与处理方式。
4) 完成整合后执行 verify_cmd；如无法运行，明确说明原因。
5) 不要输出 Markdown 本地文件链接，不要使用 file:// URI；引用文件时只写纯文本路径。

最终请在回复末尾附加结构化摘要，严格使用以下格式，每个字段单行：
${SUMMARY_MARKER_BEGIN}
status: done|blocked|failed
summary: 一句话总结
key_changes: 主要改动；可用分号分隔多点
verify: 验证结果；可用分号分隔多点
risks: 风险与后续建议；可用分号分隔多点
next_steps: 建议下一步；可用分号分隔多点
${SUMMARY_MARKER_END}
`.trim() + "\n";
}

function writeWorkerFiles(state, row, promptText) {
  const paths = workerPaths(state.run_id, row.worker_id);
  for (const file of Object.values(paths)) fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(paths.prompt, promptText);
  const profile = activeProfile();
  const cmd = buildWorkerCommand(profile);
  const worktreeAbs = path.resolve(REPO_ROOT, row.worktree_path);
  const script = `#!/usr/bin/env bash
set -u
WORKTREE=${shellQuote(worktreeAbs)}
PROMPT_FILE=${shellQuote(paths.prompt)}
LOG_FILE=${shellQuote(paths.log)}
DEBUG_FILE=${shellQuote(paths.debug)}
DONE_FILE=${shellQuote(paths.done)}
MSG_FILE=${shellQuote(paths.message)}
WORKER_PROFILE=${shellQuote(profile.name)}
WORKER_TIMEOUT_SEC="\${TMUX_ORCH_CLAUDE_TIMEOUT_SEC:-5400}"

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$DEBUG_FILE")" "$(dirname "$DONE_FILE")" "$(dirname "$MSG_FILE")"
rm -f "$DONE_FILE" "$MSG_FILE"
done_written=0
write_done() {
  local code="$1"
  if [ "$done_written" -eq 1 ]; then return; fi
  echo "$code" >"$DONE_FILE"
  done_written=1
}
handle_interrupt() {
  write_done 130
  exit 0
}
trap 'write_done $?' EXIT
trap handle_interrupt INT TERM HUP

cd "$WORKTREE"
PROMPT_TEXT="$(cat "$PROMPT_FILE")"
echo "[tmux-orch] runtime=claude profile=$WORKER_PROFILE" >>"$LOG_FILE"
echo "[tmux-orch] debug_file=$DEBUG_FILE timeout_sec=$WORKER_TIMEOUT_SEC" >>"$LOG_FILE"
${cmd}
rc=$?
write_done "$rc"
exit 0
`;
  fs.writeFileSync(paths.script, script, { mode: 0o755 });
  fs.chmodSync(paths.script, 0o755);
  return paths;
}

function ensureWorkerWorktree(row) {
  if (!branchExists(row.base_branch)) throw new CmdError(`branch not found: ${row.base_branch}`);
  const wtPath = path.resolve(REPO_ROOT, row.worktree_path);
  if (fs.existsSync(wtPath) && !fs.existsSync(path.join(wtPath, ".git"))) {
    throw new CmdError(`worktree path exists but is not git worktree: ${wtPath}`);
  }
  if (!fs.existsSync(wtPath)) {
    fs.mkdirSync(path.dirname(wtPath), { recursive: true });
    if (branchExists(row.worker_branch)) sh(["git", "worktree", "add", wtPath, row.worker_branch]);
    else sh(["git", "worktree", "add", "-b", row.worker_branch, wtPath, row.base_branch]);
  }
  return wtPath;
}

function tmuxHasSession(sessionName) {
  return sh(["tmux", "has-session", "-t", sessionName], { check: false }).status === 0;
}

function tmuxNewSession(sessionName) {
  sh(["tmux", "new-session", "-d", "-s", sessionName, "-n", "workers"]);
  const pane = sh(["tmux", "display-message", "-p", "-t", `${sessionName}:0.0`, "#{pane_id}"]).stdout.trim();
  if (!pane) throw new CmdError("failed to create tmux pane");
  return pane;
}

function tmuxNewPane(sessionName) {
  const pane = sh(["tmux", "split-window", "-d", "-t", `${sessionName}:0`, "-P", "-F", "#{pane_id}"]).stdout.trim();
  sh(["tmux", "select-layout", "-t", `${sessionName}:0`, "tiled"], { check: false });
  return pane;
}

function tmuxPaneExists(paneId) {
  if (!paneId) return false;
  return sh(["tmux", "list-panes", "-a", "-F", "#{pane_id}"], { check: false }).stdout.split(/\r?\n/).includes(paneId);
}

function tmuxSend(paneId, command) {
  sh(["tmux", "send-keys", "-t", paneId, command, "C-m"]);
}

function tmuxCtrlC(paneId) {
  sh(["tmux", "send-keys", "-t", paneId, "C-c"], { check: false });
}

function tmuxKillSession(sessionName) {
  sh(["tmux", "kill-session", "-t", sessionName], { check: false });
}

function workerExitStatus(code) {
  if (code === 0) return "done";
  if (code === 130) return "blocked";
  return "failed";
}

function parseSummary(content) {
  const start = content.indexOf(SUMMARY_MARKER_BEGIN);
  if (start < 0) return {};
  const end = content.indexOf(SUMMARY_MARKER_END, start);
  const block = content.slice(start + SUMMARY_MARKER_BEGIN.length, end >= 0 ? end : undefined);
  const result = {};
  for (const raw of block.split(/\r?\n/)) {
    const idx = raw.indexOf(":");
    if (idx <= 0) continue;
    const key = raw.slice(0, idx).trim();
    if (SUMMARY_FIELDS.includes(key)) result[key] = raw.slice(idx + 1).trim();
  }
  return result;
}

function refreshWorkerStatuses(state) {
  const sessionName = state.session_name || "";
  const sessionAlive = sessionName && tmuxHasSession(sessionName);
  for (const row of state.workers || []) {
    const doneFile = row.done_file && row.done_file !== "-" ? row.done_file : "";
    const paneId = row.pane_id || "";
    if (doneFile && fs.existsSync(doneFile)) {
      const code = Number(fs.readFileSync(doneFile, "utf8").trim() || "1");
      row.status = workerExitStatus(Number.isFinite(code) ? code : 1);
      row.notes = appendNote(row.notes, `exit=${Number.isFinite(code) ? code : 1}`);
    }
    const msg = row.result_ref && row.result_ref !== "-" ? path.resolve(REPO_ROOT, row.result_ref) : "";
    if (msg && fs.existsSync(msg)) {
      const content = fs.readFileSync(msg, "utf8");
      if (content.includes(SUMMARY_MARKER_BEGIN)) {
        const archive = path.join(RESULT_DIR, state.run_id, `${row.worker_id}.md`);
        fs.mkdirSync(path.dirname(archive), { recursive: true });
        fs.writeFileSync(archive, content);
        row.result_ref = rel(archive);
        if (row.status === "running") row.status = "done";
      }
    }
    if (row.status === "running" && (!sessionAlive || !paneId || !tmuxPaneExists(paneId))) {
      row.status = "blocked";
      row.notes = appendNote(row.notes, "worker_interrupted");
    }
  }
  const rows = state.workers || [];
  if (rows.length && rows.every((row) => WORKER_TERMINAL.has(row.status)) && sessionAlive) {
    tmuxKillSession(sessionName);
    appendEvent(state, "session_closed_auto", { session: sessionName });
  }
}

function startWorker(state, row, paneId, promptText) {
  const paths = writeWorkerFiles(state, row, promptText);
  tmuxSend(paneId, `bash ${shellQuote(paths.script)}`);
  row.pane_id = paneId;
  row.status = "running";
  row.session_id = "last";
  row.result_ref = rel(paths.message);
  row.prompt_file = paths.prompt;
  row.script_file = paths.script;
  row.log_file = paths.log;
  row.done_file = paths.done;
  row.notes = appendNote(row.notes, `pane=${paneId}`);
}

function workerStatusCounter(rows) {
  const counts = {};
  for (const row of rows || []) counts[row.status || "unknown"] = (counts[row.status || "unknown"] || 0) + 1;
  return counts;
}

function shortenText(value, limit = 160) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function loadWorkerResult(row) {
  if (!row.result_ref || row.result_ref === "-") return { content: "", summary: {} };
  const file = path.resolve(REPO_ROOT, row.result_ref);
  if (!fs.existsSync(file)) return { content: "", summary: {} };
  const content = fs.readFileSync(file, "utf8");
  return { content, summary: parseSummary(content) };
}

function buildInspectReport(state) {
  const lines = [`# Inspect: ${state.run_id}`, "", `- goal: ${state.goal}`, `- mode: ${state.mode}`, `- execution_kind: ${state.execution_kind}`, ""];
  for (const row of state.workers || []) {
    lines.push(`## ${row.worker_id}`, "", `- status: \`${row.status}\``, `- branch: \`${row.worker_branch}\``, `- worktree: \`${row.worktree_path}\``, `- result: \`${row.result_ref || "-"}\``, "");
    const { content, summary } = loadWorkerResult(row);
    if (Object.keys(summary).length) {
      for (const field of SUMMARY_FIELDS) if (summary[field]) lines.push(`- ${field}: ${summary[field]}`);
    } else if (content) {
      lines.push("```text", content.slice(0, 1800), content.length > 1800 ? "... (truncated)" : "", "```");
    } else {
      lines.push("- result: (empty)");
    }
    lines.push("");
  }
  return `${lines.join("\n")}\n`;
}

function cmdDoctor() {
  ensureDirs();
  const missing = ["git", "tmux", "claude"].filter((tool) => !ensureToolExists(tool));
  const profile = activeProfile();
  console.log(`repo_root=${REPO_ROOT}`);
  console.log(`orch_plan=${ORCH_PLAN_FILE}`);
  console.log(`state_dir=${STATE_DIR}`);
  console.log(`config_file=${CONFIG_FILE}`);
  console.log(`git_repo=${gitRepoRoot() || "none"}`);
  console.log(`missing_tools=${missing.length ? missing.join(", ") : "none"}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${profile.name}`);
  console.log(`worker_model=${profile.model || "(claude default)"}`);
  console.log(`worker_base_url=${profile.base_url || "(default)"}`);
  if (IS_OPENSPEC) {
    const openspec = validateOpenSpecParent();
    console.log(`openspec_cmd=${resolveOpenSpecCommand()}`);
    console.log(`openspec_initialized=${openspecInitialized() ? "yes" : "no"}`);
    console.log(`openspec_status=${openspec.ok ? "ok" : "failed"}`);
    console.log(`openspec_detail=${openspec.detail}`);
    console.log(`max_parallel_workers=${MAX_RUNNING_WORKERS}`);
  }
  console.log(`quickstart=draft -> run -> status -> inspect -> ${IS_OPENSPEC ? "" : "integrate -> "}close`);
  return missing.length ? 1 : 0;
}

function cmdDraft(args) {
  const goal = requireArg(args, "--goal").trim();
  if (!goal) throw new CmdError("--goal must not be empty");
  const runId = getArg(args, "--run") || runIdNow();
  const tasks = splitGoalTasks(goal);
  const workers = decideWorkers(tasks, getArg(args, "--workers"));
  const baseBranch = gitCurrentBranch();
  const rows = buildWorkerRows({ runId, goal, tasks, workers, baseBranch });
  const profile = activeProfile();
  const state = {
    run_id: runId,
    goal,
    mode: "split-task",
    execution_kind: "modify",
    worker_runtime: "claude",
    worker_profile: profile.name,
    execution_policy: IS_OPENSPEC ? "direct" : "review_first",
    base_branch: baseBranch,
    session_name: `orch-${runId}`.slice(0, 40),
    created_at: isoNow(),
    updated_at: isoNow(),
    workers: rows,
    events: [],
    openspec_context: IS_OPENSPEC ? {} : undefined,
    synth_branch: "",
    synth_worktree: "",
    synth_report: "",
    synth_status: "-",
  };
  appendEvent(state, "draft", { worker_count: rows.length, worker_profile: profile.name, execution_policy: state.execution_policy });
  saveState(state);
  writePlan(state);
  console.log(`run_id=${runId}`);
  console.log("mode=split-task");
  console.log("execution_kind=modify");
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${profile.name}`);
  console.log(`workers=${rows.length}`);
  console.log(`execution_policy=${state.execution_policy}`);
  console.log(`plan=${ORCH_PLAN_FILE}`);
  return 0;
}

function cmdRun(args) {
  const runId = requireArg(args, "--run");
  const state = loadState(runId);
  refreshWorkerStatuses(state);
  ensureRuntimeAvailable("claude");
  if (IS_OPENSPEC) {
    const check = validateOpenSpecParent();
    if (!check.ok) throw new CmdError(`parent OpenSpec preflight failed: ${check.detail}`);
    state.openspec_context = buildOpenSpecContext();
  }
  const sessionName = state.session_name || `orch-${state.run_id}`.slice(0, 40);
  state.session_name = sessionName;
  const reuse = hasFlag(args, "--reuse-session");
  if (tmuxHasSession(sessionName) && !reuse) throw new CmdError(`tmux session already exists: ${sessionName} (use --reuse-session or close first)`);
  let firstPane = "";
  if (!tmuxHasSession(sessionName)) firstPane = tmuxNewSession(sessionName);
  let launched = 0;
  let running = (state.workers || []).filter((row) => row.status === "running").length;
  for (const row of state.workers || []) {
    if (launched + running >= MAX_RUNNING_WORKERS) break;
    if (row.status === "done" || row.status === "running") continue;
    ensureWorkerWorktree(row);
    const paneId = firstPane || (row.pane_id && tmuxPaneExists(row.pane_id) ? row.pane_id : tmuxNewPane(sessionName));
    firstPane = "";
    startWorker(state, row, paneId, workerPrompt(state, row));
    launched += 1;
  }
  appendEvent(state, "run", { session_name: sessionName, launched, max_parallel_workers: MAX_RUNNING_WORKERS });
  saveState(state);
  writePlan(state);
  console.log(`run_id=${state.run_id}`);
  console.log(`session=${sessionName}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${state.worker_profile || "default"}`);
  if (IS_OPENSPEC) console.log(`max_parallel_workers=${MAX_RUNNING_WORKERS}`);
  console.log(`launched=${launched}`);
  return 0;
}

function cmdStatus(args) {
  const runId = requireArg(args, "--run");
  const state = loadState(runId);
  refreshWorkerStatuses(state);
  const counts = workerStatusCounter(state.workers || []);
  appendEvent(state, "status", { counts });
  saveState(state);
  writePlan(state);
  if (hasFlag(args, "--json")) {
    console.log(JSON.stringify({ run_id: runId, status: counts, workers: state.workers || [] }, null, 2));
    return 0;
  }
  console.log(`run_id=${runId}`);
  console.log(`execution_kind=${state.execution_kind || "modify"}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${state.worker_profile || "default"}`);
  console.log(`session=${state.session_name || "-"}`);
  for (const key of Object.keys(counts).sort()) console.log(`- ${key}: ${counts[key]}`);
  for (const row of state.workers || []) console.log(`  * ${row.worker_id}: ${row.status} (${row.worker_branch} -> ${row.result_ref})`);
  return 0;
}

function cmdInspect(args) {
  const state = loadState(requireArg(args, "--run"));
  refreshWorkerStatuses(state);
  saveState(state);
  writePlan(state);
  console.log(buildInspectReport(state));
  return 0;
}

function cmdIntegrate(args) {
  if (IS_OPENSPEC) throw new CmdError("integrate is not available for tmux-claude-openspec-worker");
  const state = loadState(requireArg(args, "--run"));
  refreshWorkerStatuses(state);
  ensureRuntimeAvailable("claude");
  const doneRows = (state.workers || []).filter((row) => row.status === "done" && row.worker_id !== "merge" && row.worker_branch && row.worker_branch !== "-");
  if (!doneRows.length) throw new CmdError("no done worker branches available for child integration");
  let mergeRow = (state.workers || []).find((row) => row.worker_id === "merge");
  if (!mergeRow) {
    mergeRow = defaultWorkerRow({
      runId: state.run_id,
      workerId: "merge",
      taskTitle: "Integrate child worker branches",
      taskScope: "Integrate completed child worker branches into one branch",
      baseBranch: state.base_branch,
      strategy: "integration",
    });
    mergeRow.worker_branch = `orchestrator/${state.run_id}/integrate`;
    mergeRow.worktree_path = `.worktree-tmux-orch/${state.run_id}/integrate`;
    mergeRow.ownership = "integration";
    state.workers.push(mergeRow);
  }
  if (mergeRow.status === "running") throw new CmdError("integration worker is already running");
  ensureWorkerWorktree(mergeRow);
  const sessionName = state.session_name || `orch-${state.run_id}`.slice(0, 40);
  state.session_name = sessionName;
  const paneId = tmuxHasSession(sessionName) ? tmuxNewPane(sessionName) : tmuxNewSession(sessionName);
  startWorker(state, mergeRow, paneId, integrationPrompt(state, mergeRow, doneRows));
  appendEvent(state, "integrate", { session_name: sessionName, source_workers: doneRows.map((row) => row.worker_id) });
  saveState(state);
  writePlan(state);
  console.log(`run_id=${state.run_id}`);
  console.log(`session=${sessionName}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${state.worker_profile || "default"}`);
  console.log("integration_worker=merge");
  console.log(`source_workers=${doneRows.length}`);
  return 0;
}

function cmdClose(args) {
  const state = loadState(requireArg(args, "--run"));
  const sessionName = state.session_name || "";
  if (sessionName && tmuxHasSession(sessionName)) {
    let interrupted = 0;
    for (const row of state.workers || []) {
      if (row.status !== "running") continue;
      if (row.pane_id && tmuxPaneExists(row.pane_id)) {
        tmuxCtrlC(row.pane_id);
        interrupted += 1;
      }
    }
    tmuxKillSession(sessionName);
    refreshWorkerStatuses(state);
    appendEvent(state, "session_closed_manual", { session: sessionName, interrupted_workers: interrupted });
    saveState(state);
    writePlan(state);
    console.log(`closed_session=${sessionName}`);
    return 0;
  }
  console.log("no_active_session");
  return 0;
}

function getArg(args, name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : "";
}

function requireArg(args, name) {
  const value = getArg(args, name);
  if (!value) throw new CmdError(`${name} is required`);
  return value;
}

function hasFlag(args, name) {
  return args.includes(name);
}

function usage() {
  const commands = IS_OPENSPEC
    ? "doctor | draft --goal <goal> [--workers 1] [--run id] | run --run id | status --run id [--json] | inspect --run id | close --run id"
    : "doctor | draft --goal <goal> [--workers n] [--run id] | run --run id | status --run id [--json] | inspect --run id | integrate --run id | close --run id";
  return `${SKILL_NAME}: ${commands}`;
}

function main(argv) {
  ensureDirs();
  const [command, ...args] = argv;
  if (!command || command === "-h" || command === "--help") {
    console.log(usage());
    return 0;
  }
  switch (command) {
    case "doctor": return cmdDoctor(args);
    case "draft": return cmdDraft(args);
    case "run": return cmdRun(args);
    case "status": return cmdStatus(args);
    case "inspect": return cmdInspect(args);
    case "integrate": return cmdIntegrate(args);
    case "close": return cmdClose(args);
    default: throw new CmdError(`unknown command: ${command}\n${usage()}`);
  }
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (error) {
  if (error instanceof CmdError) {
    console.error(error.message);
    process.exitCode = 1;
  } else {
    console.error(error?.stack || String(error));
    process.exitCode = 1;
  }
}
