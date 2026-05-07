#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SCRIPT_PATH = new URL(import.meta.url).pathname;
const SKILL_DIR = path.resolve(path.dirname(SCRIPT_PATH), "..");
const STATE_DIR = path.join(SKILL_DIR, ".state");
const LOG_DIR = path.join(SKILL_DIR, ".logs");
const RESULT_DIR = path.join(SKILL_DIR, ".results");
const PROJECT_DIR = path.join(SKILL_DIR, ".projects");
const CONFIG_FILE = path.join(SKILL_DIR, "config.toml");
const CONFIG_EXAMPLE = path.join(SKILL_DIR, "config.example.toml");

const RESULT_BEGIN = "<<<GIT_FINALIZER_RESULT";
const RESULT_END = ">>>";
const TERMINAL_STATUSES = new Set(["done", "blocked", "failed", "committed"]);

class CmdError extends Error {}

function ensureDirs() {
  for (const dir of [STATE_DIR, LOG_DIR, RESULT_DIR, PROJECT_DIR]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function isoNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function runIdNow() {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace("T", "-")
    .replace(/\.\d{3}Z$/, "");
  return `${stamp}-${crypto.randomInt(0, 0x10000).toString(16).padStart(4, "0")}`;
}

function shellQuote(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_/:=.,@%+-]+$/.test(text)) return text;
  return `'${text.replaceAll("'", "'\\''")}'`;
}

function sh(args, options = {}) {
  const proc = spawnSync(args[0], args.slice(1), {
    cwd: options.cwd || process.cwd(),
    encoding: "utf8",
    env: options.env || process.env,
  });
  const result = {
    status: proc.status ?? 1,
    stdout: proc.stdout || "",
    stderr: proc.stderr || "",
  };
  if (options.check !== false && result.status !== 0) {
    throw new CmdError(
      `command failed (${result.status}): ${args.map(shellQuote).join(" ")}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    );
  }
  return result;
}

function commandExists(name) {
  return sh(["bash", "-lc", `command -v ${shellQuote(name)} >/dev/null`], { check: false }).status === 0;
}

function repoRoot() {
  const proc = sh(["git", "rev-parse", "--show-toplevel"], { check: false });
  if (proc.status !== 0) throw new CmdError("git-finalizer must be run from inside a git repository");
  return path.resolve(proc.stdout.trim());
}

function currentBranch(root) {
  const proc = sh(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], { cwd: root, check: false });
  const branch = proc.stdout.trim();
  if (proc.status === 0 && branch) return branch;
  throw new CmdError("detached HEAD is not supported for finalize");
}

function gitDir(root) {
  const out = sh(["git", "rev-parse", "--git-dir"], { cwd: root }).stdout.trim();
  return path.isAbsolute(out) ? out : path.join(root, out);
}

function ensureNoGitOperation(root) {
  const gd = gitDir(root);
  const blockers = ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"];
  const active = blockers.filter((name) => fs.existsSync(path.join(gd, name)));
  if (active.length) throw new CmdError(`refusing to finalize while git operation is active: ${active.join(", ")}`);
}

function projectKey(root) {
  return crypto.createHash("sha256").update(root).digest("hex").slice(0, 16);
}

function projectStatePath(root) {
  return path.join(PROJECT_DIR, `${projectKey(root)}.json`);
}

function sessionName(root, runId) {
  const slug = path.basename(root).replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-|-$/g, "") || "repo";
  return `git-finalizer-${slug.slice(0, 24)}-${runId.slice(-6)}`;
}

function statePath(runId) {
  return path.join(STATE_DIR, `${runId}.json`);
}

function readJson(file, fallback = {}) {
  if (!fs.existsSync(file)) return fallback;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`);
}

function loadState(runId) {
  const file = statePath(runId);
  if (!fs.existsSync(file)) throw new CmdError(`run state not found: ${runId}`);
  return readJson(file);
}

function saveState(state) {
  ensureDirs();
  state.updated_at = isoNow();
  writeJson(statePath(state.run_id), state);
}

function appendEvent(state, kind, detail) {
  state.events ||= [];
  state.events.push({ ts: isoNow(), kind, detail });
}

function stripTomlComment(line) {
  let inString = false;
  let escaped = false;
  let out = "";
  for (const ch of line) {
    if (ch === "\\" && inString && !escaped) {
      escaped = true;
      out += ch;
      continue;
    }
    if (ch === '"' && !escaped) {
      inString = !inString;
      out += ch;
      escaped = false;
      continue;
    }
    if (ch === "#" && !inString) break;
    out += ch;
    escaped = false;
  }
  return out.trim();
}

function parseTomlValue(raw) {
  const value = raw.trim();
  if (value.startsWith('"') && value.endsWith('"')) return value.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  if (value === "true") return true;
  if (value === "false") return false;
  return value;
}

function loadSimpleToml(file) {
  const target = fs.existsSync(file) ? file : CONFIG_EXAMPLE;
  const data = {};
  if (!fs.existsSync(target)) return data;
  let current = data;
  for (const raw of fs.readFileSync(target, "utf8").split(/\r?\n/)) {
    const line = stripTomlComment(raw);
    if (!line) continue;
    if (line.startsWith("[") && line.endsWith("]")) {
      current = data;
      for (const part of line.slice(1, -1).split(".")) {
        const key = part.trim();
        current[key] ||= {};
        current = current[key];
      }
      continue;
    }
    const eq = line.indexOf("=");
    if (eq < 0) throw new CmdError(`invalid TOML line in ${target}: ${raw}`);
    current[line.slice(0, eq).trim()] = parseTomlValue(line.slice(eq + 1));
  }
  return data;
}

function loadConfig() {
  const raw = loadSimpleToml(CONFIG_FILE);
  const selected = String(raw.selected_model || "minimax").trim();
  const profiles = raw.profiles || {};
  if (!profiles[selected]) throw new CmdError(`selected_model ${JSON.stringify(selected)} not found in config profiles`);
  return {
    selected,
    autoFinalize: raw.auto_finalize_after_run === true,
    persistClaudeSession: raw.persist_claude_session !== false,
    useApiKeyHelper: raw.use_api_key_helper !== false,
    profiles,
  };
}

function activeProfile() {
  const { selected, profiles } = loadConfig();
  return { name: selected, ...profiles[selected] };
}

function claudeEnv(profile, includeApiKey = true) {
  const env = {};
  if (profile.base_url) env.ANTHROPIC_BASE_URL = profile.base_url;
  if (includeApiKey && profile.api_key) {
    env.ANTHROPIC_API_KEY = profile.api_key;
    env.ANTHROPIC_AUTH_TOKEN = profile.api_key;
  }
  if (profile.model) {
    env.ANTHROPIC_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_HAIKU_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_SONNET_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_OPUS_MODEL = profile.model;
  }
  return env;
}

function loadProjectState(root) {
  return readJson(projectStatePath(root), { repo_root: root, repo_key: projectKey(root), claude_session_id: "" });
}

function saveProjectState(root, data) {
  ensureDirs();
  writeJson(projectStatePath(root), { ...data, repo_root: root, repo_key: projectKey(root), updated_at: isoNow() });
}

function clearProjectClaudeSession(root) {
  saveProjectState(root, { ...loadProjectState(root), claude_session_id: "" });
}

function gitStatusPorcelain(root) {
  return sh(["git", "status", "--porcelain=v1", "-z"], { cwd: root }).stdout;
}

function untrackedFiles(root) {
  return sh(["git", "ls-files", "--others", "--exclude-standard", "-z"], { cwd: root }).stdout.split("\0").filter(Boolean);
}

function hasDiff(root) {
  return gitStatusPorcelain(root).replaceAll("\0", "").trim().length > 0;
}

function diffHash(root) {
  const h = crypto.createHash("sha256");
  h.update("status\0");
  h.update(gitStatusPorcelain(root), "utf8");
  h.update("diff\0");
  h.update(sh(["git", "diff", "--binary", "HEAD", "--"], { cwd: root, check: false }).stdout, "utf8");
  for (const rel of untrackedFiles(root).sort()) {
    h.update("untracked\0");
    h.update(rel);
    const file = path.join(root, rel);
    if (fs.existsSync(file) && fs.statSync(file).isFile()) {
      h.update(crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"));
    }
  }
  return h.digest("hex");
}

function defaultTestCmd(root) {
  if (fs.existsSync(path.join(root, "package.json"))) {
    if (fs.existsSync(path.join(root, "pnpm-lock.yaml"))) return "pnpm test";
    if (fs.existsSync(path.join(root, "yarn.lock"))) return "yarn test";
    return "npm test";
  }
  if (fs.existsSync(path.join(root, "Cargo.toml"))) return "cargo test";
  if (fs.existsSync(path.join(root, "go.mod"))) return "go test ./...";
  if (fs.existsSync(path.join(root, "pyproject.toml"))) return "pytest";
  return "-";
}

function recentCommits(root) {
  return sh(["git", "log", "-5", "--pretty=format:%s"], { cwd: root, check: false }).stdout.trim() || "-";
}

function changedFilesSummary(root) {
  return sh(["git", "status", "--short"], { cwd: root, check: false }).stdout.trim() || "-";
}

function workerPaths(runId) {
  return {
    prompt: path.join(STATE_DIR, runId, "worker.prompt.txt"),
    script: path.join(STATE_DIR, runId, "worker.run.sh"),
    raw: path.join(RESULT_DIR, runId, "claude.raw.json"),
    message: path.join(RESULT_DIR, runId, "worker.md"),
    result: path.join(RESULT_DIR, runId, "result.json"),
    log: path.join(LOG_DIR, runId, "worker.log"),
    debug: path.join(LOG_DIR, runId, "worker.debug.log"),
    done: path.join(LOG_DIR, runId, "worker.done"),
    keyHelper: path.join(STATE_DIR, runId, "api-key-helper.sh"),
  };
}

function buildPrompt(state, root) {
  return `你是 git-finalizer 的高速 Claude Code worker。强模型或主 agent 已经完成代码实现，你只负责测试、审查 git diff、生成提交信息。不要修改源码，不要修复问题。

本轮任务:
- run_id: ${state.run_id}
- repo_root: ${root}
- goal: ${state.goal}
- test_cmd: ${state.test_cmd}
- reviewed_diff_hash: ${state.draft_diff_hash}
- branch: ${state.branch || "-"}

你需要执行:
1) 阅读 \`git status --short\`、\`git diff --stat\`、\`git diff --cached --stat\`、必要的 \`git diff\` 细节，以及未跟踪文件列表。
2) 阅读最近提交风格: \`git log -5 --pretty=format:%s\`。
3) 如果 test_cmd 不是 \`-\`，运行该测试/检查命令。
4) 判断当前改动是否可以提交。测试失败、明显风险、无法理解 diff、或命令无法运行时，返回 \`blocked\` 或 \`failed\`，不要提交。
5) 生成符合以下规范的 commit_subject 和 commit_body。

提交信息规范:
- commit_subject 使用格式: <type>(<scope>): <中文标题>
- type 保留英文 Conventional Commits 类型，只能使用 feat、fix、refactor、test、docs、style、chore、perf、revert
- scope 使用最小有意义模块名；没有明确模块时可以省略括号，使用 <type>: <中文标题>
- 中文标题必须用中文概括主要改动，不要写英文句子，不要以句号结尾
- commit_body 必须使用中文 bullet list，每行一个具体修改内容，每行以 "- " 开头
- commit_body 除代码标识符、文件名、命令、模型名等必要技术字面量外，不要使用英文
- commit_body 只写修改内容，不写动机长文，不写“由 AI 生成”
- 极小改动可写 commit_body: -，否则默认写 2 到 6 条中文 bullet

严格约束:
- 不要编辑、格式化、重写或删除任何项目文件。
- 不要执行 git add、git commit、git reset、git checkout、git restore。
- 可以运行只读 git 命令和 test_cmd。
- 如果测试失败，只报告原因和建议修复方向。
- diff_hash 字段必须原样返回: ${state.draft_diff_hash}

最终回复末尾必须包含这个结构化块，每个字段单行:
${RESULT_BEGIN}
status: done|blocked|failed
summary: 一句话总结
tests: 测试命令与结果
changed_files: 主要文件；用分号分隔
risk_notes: 风险或 none
commit_subject: <type>(<scope>): <中文标题>
commit_body: 中文 bullet list；没有则写 -
diff_hash: ${state.draft_diff_hash}
${RESULT_END}
`;
}

function isRecoverableResumeError(text) {
  const lower = String(text || "").toLowerCase();
  if (lower.includes("already in use")) return false;
  return [
    "no conversation found",
    "conversation not found",
    "session not found",
    "session id not found",
    "session does not exist",
    "session file not found",
    "could not find session",
    "cannot find session",
    "failed to resume",
    "invalid session",
    "corrupt",
    "corrupted",
  ].some((needle) => lower.includes(needle));
}

function buildClaudeCommand(root, state, paths, options = {}) {
  const profile = activeProfile();
  const { persistClaudeSession, useApiKeyHelper } = loadConfig();
  const envParts = Object.entries(claudeEnv(profile, !useApiKeyHelper)).map(([key, value]) => `${key}=${shellQuote(value)}`);
  const custom = (process.env.GIT_FINALIZER_CLAUDE_CMD || "").trim();
  if (custom) return [...envParts, custom].join(" ");
  const args = [
    "claude",
    "--bare",
    "-p",
    "--dangerously-skip-permissions",
    "--permission-mode",
    "bypassPermissions",
    "--output-format",
    persistClaudeSession ? "json" : "text",
    "--debug-file",
    paths.debug,
  ];
  if (useApiKeyHelper && profile.api_key) {
    args.push("--settings", JSON.stringify({ apiKeyHelper: paths.keyHelper }));
  }
  if (profile.model) args.push("--model", profile.model);
  if (persistClaudeSession) {
    const sessionId = options.ignoreSavedSession ? "" : String(loadProjectState(root).claude_session_id || "").trim();
    if (sessionId) args.push("--resume", sessionId);
  } else {
    args.splice(2, 0, "--no-session-persistence");
  }
  args.push(fs.readFileSync(paths.prompt, "utf8"));
  return [...envParts, args.map(shellQuote).join(" ")].join(" ");
}

function writeWorkerScript(state, root) {
  const paths = workerPaths(state.run_id);
  for (const file of Object.values(paths)) fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(paths.prompt, buildPrompt(state, root));
  const profile = activeProfile();
  const { useApiKeyHelper } = loadConfig();
  if (useApiKeyHelper && profile.api_key) {
    fs.writeFileSync(paths.keyHelper, `#!/usr/bin/env bash\nprintf '%s' ${shellQuote(profile.api_key)}\n`, { mode: 0o700 });
  }
  const cmd = buildClaudeCommand(root, state, paths);
  const retryCmd = buildClaudeCommand(root, state, paths, { ignoreSavedSession: true });
  const savedSessionId = String(loadProjectState(root).claude_session_id || "").trim();
  const { persistClaudeSession } = loadConfig();
  const shouldAllowResumeRetry = persistClaudeSession && savedSessionId;
  const script = `#!/usr/bin/env bash
set -u
cd ${shellQuote(root)}
LOG_FILE=${shellQuote(paths.log)}
RAW_FILE=${shellQuote(paths.raw)}
DONE_FILE=${shellQuote(paths.done)}
ERR_FILE=${shellQuote(paths.raw)}.stderr
RETRY_ERR_FILE=${shellQuote(paths.raw)}.retry.stderr
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$LOG_FILE"
${cmd} >"$RAW_FILE" 2>"$ERR_FILE"
rc=$?
cat "$ERR_FILE" >>"$LOG_FILE"
if [ "$rc" -ne 0 ] && [ ${shouldAllowResumeRetry ? "1" : "0"} -eq 1 ]; then
  if node ${shellQuote(SCRIPT_PATH)} __is-recoverable-resume-error --file "$ERR_FILE"; then
    echo "resume_session_recoverable=true" >>"$LOG_FILE"
    node ${shellQuote(SCRIPT_PATH)} __clear-session --repo-root ${shellQuote(root)} >>"$LOG_FILE" 2>&1
    ${retryCmd} >"$RAW_FILE" 2>"$RETRY_ERR_FILE"
    rc=$?
    cat "$RETRY_ERR_FILE" >>"$LOG_FILE"
  fi
fi
node ${shellQuote(SCRIPT_PATH)} __finish --run ${shellQuote(state.run_id)} --exit-code "$rc" >>"$LOG_FILE" 2>&1
echo "$rc" >"$DONE_FILE"
exit "$rc"
`;
  fs.writeFileSync(paths.script, script, { mode: 0o755 });
  state.prompt_file = paths.prompt;
  state.script_file = paths.script;
  state.log_file = paths.log;
  state.result_file = paths.result;
}

function parseResultBlock(text) {
  const start = text.lastIndexOf(RESULT_BEGIN);
  if (start < 0) return {};
  const end = text.indexOf(RESULT_END, start);
  if (end < 0) return {};
  const out = {};
  for (const line of text.slice(start + RESULT_BEGIN.length, end).trim().split(/\r?\n/)) {
    const idx = line.indexOf(":");
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
}

function extractClaudeTextAndSession(raw) {
  try {
    const data = JSON.parse(raw);
    const sessionId = String(data.session_id || data.sessionId || "").trim();
    for (const key of ["result", "content", "message", "text"]) {
      if (typeof data[key] === "string") return { text: data[key], sessionId };
    }
    return { text: JSON.stringify(data, null, 2), sessionId };
  } catch {
    return { text: raw, sessionId: "" };
  }
}

function refreshStatus(state) {
  const paths = workerPaths(state.run_id);
  if (TERMINAL_STATUSES.has(state.status)) return;
  if (fs.existsSync(paths.done)) {
    const code = Number(fs.readFileSync(paths.done, "utf8").trim() || "1");
    const result = fs.existsSync(paths.result) ? readJson(paths.result) : {};
    const status = result.parsed?.status;
    state.status = TERMINAL_STATUSES.has(status) ? status : code === 0 ? "done" : "failed";
    appendEvent(state, "worker_done", { exit_code: code, status: state.status });
    saveState(state);
    return;
  }
  if (state.tmux_session) {
    state.status = sh(["tmux", "has-session", "-t", state.tmux_session], { check: false }).status === 0 ? "running" : state.status || "planned";
  }
}

function cmdDoctor() {
  ensureDirs();
  const root = repoRoot();
  const { selected, autoFinalize, persistClaudeSession, useApiKeyHelper, profiles } = loadConfig();
  const project = loadProjectState(root);
  console.log(`repo_root=${root}`);
  console.log(`repo_key=${projectKey(root)}`);
  console.log(`branch=${currentBranch(root)}`);
  console.log(`has_diff=${hasDiff(root)}`);
  console.log(`diff_hash=${diffHash(root)}`);
  console.log("git=ok");
  console.log(`tmux=${commandExists("tmux") ? "ok" : "missing"}`);
  console.log(`claude=${commandExists("claude") ? "ok" : "missing"}`);
  console.log(`node=${process.version}`);
  console.log(`selected_model=${selected}`);
  console.log(`auto_finalize_after_run=${autoFinalize}`);
  console.log(`persist_claude_session=${persistClaudeSession}`);
  console.log(`use_api_key_helper=${useApiKeyHelper}`);
  console.log(`profiles=${Object.keys(profiles).sort().join(",")}`);
  console.log(`project_claude_session_id=${project.claude_session_id || "-"}`);
  const help = commandExists("claude") ? sh(["claude", "--help"], { check: false }) : { stdout: "", stderr: "" };
  console.log(`claude_resume_support=${(help.stdout + help.stderr).includes("--resume") || (help.stdout + help.stderr).includes("-r")}`);
}

function cmdDraft(args) {
  ensureDirs();
  const root = repoRoot();
  ensureNoGitOperation(root);
  const runId = args.run || runIdNow();
  if (fs.existsSync(statePath(runId))) throw new CmdError(`run state already exists: ${runId}`);
  const state = {
    run_id: runId,
    goal: args.goal,
    repo_root: root,
    repo_key: projectKey(root),
    branch: currentBranch(root),
    test_cmd: args.testCmd ?? defaultTestCmd(root),
    draft_diff_hash: diffHash(root),
    status: "planned",
    created_at: isoNow(),
    changed_files_at_draft: changedFilesSummary(root),
    recent_commits: recentCommits(root),
    events: [],
  };
  appendEvent(state, "draft", { test_cmd: state.test_cmd });
  saveState(state);
  console.log(`run_id=${runId}`);
  console.log(`test_cmd=${state.test_cmd}`);
  console.log(`diff_hash=${state.draft_diff_hash}`);
  console.log(`state_file=${statePath(runId)}`);
}

function cmdRun(args) {
  ensureDirs();
  const state = loadState(args.run);
  const root = path.resolve(state.repo_root);
  refreshStatus(state);
  if (state.status === "running" || TERMINAL_STATUSES.has(state.status)) {
    console.log(`run_id=${args.run}`);
    console.log(`status=${state.status}`);
    if (state.tmux_session) console.log(`tmux_session=${state.tmux_session}`);
    return;
  }
  if (!commandExists("tmux")) throw new CmdError("tmux is required");
  if (!commandExists("claude") && !(process.env.GIT_FINALIZER_CLAUDE_CMD || "").trim()) {
    throw new CmdError("claude CLI is required, or set GIT_FINALIZER_CLAUDE_CMD");
  }
  writeWorkerScript(state, root);
  const name = sessionName(root, state.run_id);
  state.tmux_session = name;
  state.status = "running";
  appendEvent(state, "run", { tmux_session: name });
  saveState(state);
  try {
    sh(["tmux", "new-session", "-d", "-s", name, `bash ${shellQuote(state.script_file)}`], { cwd: root });
  } catch (error) {
    state.status = "failed";
    appendEvent(state, "run_failed", { tmux_session: name, error: error.message });
    saveState(state);
    throw error;
  }
  console.log(`run_id=${args.run}`);
  console.log("status=running");
  console.log(`tmux_session=${name}`);
}

function cmdFinish(args) {
  ensureDirs();
  const state = loadState(args.run);
  const root = path.resolve(state.repo_root);
  const paths = workerPaths(args.run);
  const raw = fs.existsSync(paths.raw) ? fs.readFileSync(paths.raw, "utf8") : "";
  const { text, sessionId } = extractClaudeTextAndSession(raw);
  fs.mkdirSync(path.dirname(paths.message), { recursive: true });
  fs.writeFileSync(paths.message, text);
  const parsed = parseResultBlock(text);
  writeJson(paths.result, { run_id: args.run, exit_code: args.exitCode, parsed, message_file: paths.message, raw_file: paths.raw, finished_at: isoNow() });
  if (sessionId) saveProjectState(root, { ...loadProjectState(root), claude_session_id: sessionId });
  state.status = TERMINAL_STATUSES.has(parsed.status) ? parsed.status : args.exitCode === 0 ? "done" : "failed";
  state.worker_exit_code = args.exitCode;
  state.parsed_result = parsed;
  appendEvent(state, "finish", { exit_code: args.exitCode, status: state.status, session_saved: Boolean(sessionId) });
  saveState(state);
  const { autoFinalize } = loadConfig();
  if (autoFinalize && state.status === "done") {
    try {
      cmdFinalize({ run: args.run });
    } catch (error) {
      const latest = loadState(args.run);
      latest.status = "blocked";
      appendEvent(latest, "auto_finalize_blocked", { error: error.message });
      saveState(latest);
      console.log(`auto_finalize_blocked=${error.message}`);
    }
  }
}

function cmdStatus(args) {
  const state = loadState(args.run);
  refreshStatus(state);
  if (args.json) {
    console.log(JSON.stringify(state, null, 2));
    return;
  }
  console.log(`run_id=${args.run}`);
  console.log(`status=${state.status}`);
  console.log(`repo_root=${state.repo_root}`);
  console.log(`test_cmd=${state.test_cmd}`);
  console.log(`tmux_session=${state.tmux_session || "-"}`);
  console.log(`result_file=${state.result_file || "-"}`);
}

function cmdInspect(args) {
  const state = loadState(args.run);
  refreshStatus(state);
  const paths = workerPaths(args.run);
  const result = fs.existsSync(paths.result) ? readJson(paths.result) : {};
  const parsed = state.parsed_result || result.parsed || {};
  console.log(`run_id=${args.run}`);
  console.log(`status=${state.status}`);
  console.log(`repo_root=${state.repo_root}`);
  console.log(`draft_diff_hash=${state.draft_diff_hash}`);
  console.log(`current_diff_hash=${fs.existsSync(state.repo_root) ? diffHash(state.repo_root) : "-"}`);
  console.log(`test_cmd=${state.test_cmd}`);
  console.log("\nchanged_files_at_draft:");
  console.log(state.changed_files_at_draft || "-");
  console.log("\nworker_result:");
  if (Object.keys(parsed).length) {
    for (const key of ["status", "summary", "tests", "changed_files", "risk_notes", "commit_subject", "commit_body", "diff_hash"]) {
      console.log(`- ${key}: ${parsed[key] || "-"}`);
    }
  } else {
    console.log("- no structured worker result yet");
  }
  console.log("");
  console.log(`message_file=${fs.existsSync(paths.message) ? paths.message : "-"}`);
  console.log(`log_file=${fs.existsSync(paths.log) ? paths.log : "-"}`);
}

function cleanCommitMessage(subject, body) {
  const cleanSubject = String(subject || "").split(/\s+/).join(" ").trim().slice(0, 100).trim();
  if (!cleanSubject) throw new CmdError("worker did not provide commit_subject");
  const cleanBody = String(body || "").trim();
  if (cleanBody && cleanBody !== "-") {
    const invalid = cleanBody.split(/\r?\n/).filter((line) => line.trim() && !line.startsWith("- "));
    if (invalid.length) throw new CmdError("commit_body must be a bullet list with each line starting with '- '");
  }
  return cleanSubject + (cleanBody && cleanBody !== "-" ? `\n\n${cleanBody}` : "");
}

function cmdFinalize(args) {
  const state = loadState(args.run);
  refreshStatus(state);
  const root = path.resolve(state.repo_root);
  currentBranch(root);
  ensureNoGitOperation(root);
  if (!hasDiff(root)) throw new CmdError("no git diff to commit");
  const paths = workerPaths(args.run);
  const result = fs.existsSync(paths.result) ? readJson(paths.result) : {};
  const parsed = state.parsed_result || result.parsed || {};
  if (parsed.status !== "done" || state.status !== "done") {
    throw new CmdError(`worker did not pass validation: state=${state.status} result=${parsed.status}`);
  }
  const reviewed = parsed.diff_hash || state.draft_diff_hash;
  const current = diffHash(root);
  if (current !== reviewed) throw new CmdError(`diff changed after review; reviewed=${reviewed} current=${current}`);
  const message = cleanCommitMessage(parsed.commit_subject, parsed.commit_body);
  const msgFile = path.join(STATE_DIR, args.run, "commit-message.txt");
  fs.mkdirSync(path.dirname(msgFile), { recursive: true });
  fs.writeFileSync(msgFile, `${message}\n`);
  sh(["git", "add", "-A"], { cwd: root });
  sh(["git", "commit", "-F", msgFile], { cwd: root });
  state.status = "committed";
  state.commit_message_file = msgFile;
  state.commit_hash = sh(["git", "rev-parse", "--short", "HEAD"], { cwd: root }).stdout.trim();
  appendEvent(state, "commit", { commit_hash: state.commit_hash });
  saveState(state);
  console.log("status=committed");
  console.log(`commit_hash=${state.commit_hash}`);
}

function cmdClose(args) {
  const state = loadState(args.run);
  const name = state.tmux_session || "";
  if (!name) {
    console.log("tmux_session=-");
    console.log("closed=false");
    return;
  }
  if (sh(["tmux", "has-session", "-t", name], { check: false }).status === 0) {
    sh(["tmux", "kill-session", "-t", name], { check: false });
    appendEvent(state, "close", { tmux_session: name });
    saveState(state);
    console.log(`tmux_session=${name}`);
    console.log("closed=true");
  } else {
    console.log(`tmux_session=${name}`);
    console.log("closed=false");
  }
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const args = { command };
  for (let i = 0; i < rest.length; i += 1) {
    const item = rest[i];
    if (item === "--json") {
      args.json = true;
    } else if (item === "--goal") {
      args.goal = rest[++i];
    } else if (item === "--test-cmd") {
      args.testCmd = rest[++i];
    } else if (item === "--run") {
      args.run = rest[++i];
    } else if (item === "--exit-code") {
      args.exitCode = Number(rest[++i]);
    } else if (item === "--file") {
      args.file = rest[++i];
    } else if (item === "--repo-root") {
      args.repoRoot = rest[++i];
    } else {
      throw new CmdError(`unknown argument: ${item}`);
    }
  }
  return args;
}

function usage() {
  console.log(`usage: git-finalizer <command> [args]

commands:
  doctor
  draft --goal <summary> [--test-cmd <command>] [--run <run_id>]
  run --run <run_id>
  status --run <run_id> [--json]
  inspect --run <run_id>
  finalize --run <run_id>
  close --run <run_id>`);
}

function requireArg(args, key) {
  if (!args[key]) throw new CmdError(`missing required argument: --${key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`)}`);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.command || args.command === "-h" || args.command === "--help") {
    usage();
    return;
  }
  switch (args.command) {
    case "doctor":
      cmdDoctor(args);
      break;
    case "draft":
      requireArg(args, "goal");
      cmdDraft(args);
      break;
    case "run":
      requireArg(args, "run");
      cmdRun(args);
      break;
    case "status":
      requireArg(args, "run");
      cmdStatus(args);
      break;
    case "inspect":
      requireArg(args, "run");
      cmdInspect(args);
      break;
    case "finalize":
      requireArg(args, "run");
      cmdFinalize(args);
      break;
    case "close":
      requireArg(args, "run");
      cmdClose(args);
      break;
    case "__finish":
      requireArg(args, "run");
      if (!Number.isFinite(args.exitCode)) throw new CmdError("missing required argument: --exit-code");
      cmdFinish(args);
      break;
    case "__is-recoverable-resume-error":
      requireArg(args, "file");
      process.exit(isRecoverableResumeError(fs.existsSync(args.file) ? fs.readFileSync(args.file, "utf8") : "") ? 0 : 1);
      break;
    case "__clear-session":
      requireArg(args, "repoRoot");
      clearProjectClaudeSession(path.resolve(args.repoRoot));
      console.log("project_claude_session_id=-");
      break;
    default:
      throw new CmdError(`unknown command: ${args.command}`);
  }
}

try {
  main();
} catch (error) {
  if (error instanceof CmdError) {
    console.error(`error: ${error.message}`);
    process.exit(1);
  }
  throw error;
}
