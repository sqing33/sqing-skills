#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

class CmdError extends Error {}

const SCRIPT_FILE = fileURLToPath(import.meta.url);
const SRC_DIR = path.dirname(SCRIPT_FILE);
const SKILL_DIR = path.dirname(SRC_DIR);
const SKILL_NAME = path.basename(SKILL_DIR);
const INVOCATION_CWD = process.cwd();
const DISCOVERED_REPO_ROOT = discoverRepoRoot(INVOKE_CWD());
const REPO_ROOT = DISCOVERED_REPO_ROOT || INVOCATION_CWD;
const ORCH_PLAN_FILE = path.join(REPO_ROOT, "ORCH_PLAN.md");
const ORCH_DIR = path.join(REPO_ROOT, ".tmux-orch");
const WORKTREE_ROOT = path.join(REPO_ROOT, ".worktree-tmux-orch");
const STATE_DIR = path.join(ORCH_DIR, "state");
const LOG_DIR = path.join(ORCH_DIR, "logs");
const RESULT_DIR = path.join(ORCH_DIR, "results");
const REPORT_DIR = path.join(ORCH_DIR, "reports");
const CONFIG_FILE = process.env.TMUX_ORCH_CONFIG
  ? path.resolve(INVOKE_CWD(), process.env.TMUX_ORCH_CONFIG)
  : path.join(SKILL_DIR, "config.toml");
const WORKER_LIMIT = 10;
const MAX_RUNNING_WORKERS = Math.max(1, Math.min(Number(process.env.TMUX_ORCH_MAX_RUNNING_WORKERS || "8"), WORKER_LIMIT));
const OPENSPEC_NPX_CMD = "npx -y @studyzy/openspec-cn";
const MATRIX_SCHEMA = "tmux-openspec-parallel/v1";
const SUMMARY_MARKER_BEGIN = "<<<ORCH_SUMMARY";
const SUMMARY_MARKER_END = ">>>";
const SUMMARY_FIELDS = ["status", "summary", "key_changes", "verify", "risks", "next_steps", "commit"];
const COMMIT_TYPES = new Set(["feat", "fix", "refactor", "docs", "test", "chore", "style", "perf"]);
const WORKER_TERMINAL = new Set(["done", "failed", "blocked"]);
const TABLE_COLUMNS = [
  "run_id",
  "stage",
  "worker_id",
  "task_title",
  "task_scope",
  "ownership",
  "depends_on",
  "acceptance",
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

function INVOKE_CWD() {
  return INVOCATION_CWD || process.cwd();
}

function discoverRepoRoot(cwd) {
  const proc = spawnSync("git", ["rev-parse", "--show-toplevel"], {
    cwd,
    encoding: "utf8",
  });
  return proc.status === 0 ? proc.stdout.trim() : "";
}

function ensureDirs() {
  for (const dir of [ORCH_DIR, STATE_DIR, LOG_DIR, RESULT_DIR, REPORT_DIR, WORKTREE_ROOT]) {
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
    const baseUrl = String(item.base_url || "").trim();
    profiles[name] = {
      name,
      base_url: baseUrl,
      api_key: String(item.api_key || "").trim(),
      model: String(item.model || "").trim(),
      auth_env: normalizeAuthEnv(item.auth_env || (baseUrl ? "auth_token" : "api_key"), `profiles.${name}.auth_env`),
    };
  }
  if (Object.keys(profiles).length === 0) profiles.default = { name: "default", base_url: "", api_key: "", model: "", auth_env: "api_key" };
  if (!profiles[selectedModel]) throw new CmdError(`selected_model ${selectedModel} not found in ${CONFIG_FILE}`);
  return { selectedModel, profiles };
}

function normalizeAuthEnv(value, field = "auth_env") {
  const authEnv = String(value || "").trim() || "api_key";
  if (!["api_key", "auth_token", "both"].includes(authEnv)) {
    throw new CmdError(`${field} must be one of: api_key, auth_token, both`);
  }
  return authEnv;
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
  return DISCOVERED_REPO_ROOT || "";
}

function requireGitRepo(commandName) {
  if (!DISCOVERED_REPO_ROOT) {
    throw new CmdError(`${commandName} requires a Git worktree, but ${INVOKE_CWD()} is not inside a Git repository.`);
  }
  return DISCOVERED_REPO_ROOT;
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

function slugify(text, fallback = "item", limit = 32) {
  const value = String(text || "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
  return (value || fallback).slice(0, limit).replace(/-+$/g, "") || fallback;
}

function shortenText(value, limit = 160) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
}

function truncateJson(value, limit = 4000) {
  const text = JSON.stringify(value ?? {}, null, 2);
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n... truncated ...`;
}

function defaultVerifyCmd() {
  return `${resolveOpenSpecCommand()} validate --all --strict --no-interactive`;
}

function resolveOpenSpecCommand() {
  if (process.env.TMUX_ORCH_OPENSPEC_CMD) return process.env.TMUX_ORCH_OPENSPEC_CMD.trim();
  if (ensureToolExists("openspec-cn")) return "openspec-cn";
  if (ensureToolExists("openspec")) return "openspec";
  return OPENSPEC_NPX_CMD;
}

function openspecInitialized() {
  return fs.existsSync(path.join(REPO_ROOT, "openspec"));
}

function loadOpenSpecJson(args) {
  const cmd = `${resolveOpenSpecCommand()} ${args.map(shellQuote).join(" ")}`;
  const proc = shBash(cmd, { check: false });
  if (proc.status !== 0) throw new CmdError(shortenText((proc.stderr || proc.stdout || `${cmd} failed`).replace(/\s+/g, " "), 600));
  try {
    return JSON.parse(proc.stdout);
  } catch (error) {
    throw new CmdError(`invalid OpenSpec JSON output for ${args.join(" ")}: ${error.message}`);
  }
}

function selectChangeName({ explicit = "", env = process.env.TMUX_ORCH_OPENSPEC_CHANGE || "", listData = null } = {}) {
  const chosen = String(explicit || env || "").trim();
  if (chosen) return chosen;
  const changes = Array.isArray(listData?.changes) ? listData.changes : [];
  if (changes.length === 1 && changes[0]?.name) return String(changes[0].name);
  if (changes.length === 0) throw new CmdError("no active OpenSpec change found; pass --change <name> or set TMUX_ORCH_OPENSPEC_CHANGE");
  const names = changes.map((item) => item?.name).filter(Boolean).join(", ");
  throw new CmdError(`multiple active OpenSpec changes found; pass --change with one of: ${names}`);
}

function selectOpenSpecChange(explicit) {
  if (explicit || process.env.TMUX_ORCH_OPENSPEC_CHANGE) return selectChangeName({ explicit });
  return selectChangeName({ listData: loadOpenSpecJson(["list", "--json"]) });
}

function validateOpenSpecParent() {
  const cmd = resolveOpenSpecCommand();
  if (!openspecInitialized()) return { ok: false, detail: `OpenSpec not initialized in ${REPO_ROOT}; run \`${cmd} init --tools codex,claude\` first` };
  const proc = shBash(`${cmd} validate --all --strict --no-interactive`, { check: false });
  const stdout = proc.stdout.replace(/\s+/g, " ").trim();
  const stderr = proc.stderr.replace(/\s+/g, " ").trim();
  if (proc.status === 0) return { ok: true, detail: shortenText(stdout || "OpenSpec validate ok", 240) };
  return { ok: false, detail: shortenText(stderr || stdout || `${cmd} validate failed`, 600) };
}

function buildOpenSpecContext(changeName) {
  return {
    change_name: changeName,
    change_path: `openspec/changes/${changeName}`,
    status: loadOpenSpecJson(["status", "--change", changeName, "--json"]),
    show: loadOpenSpecJson(["show", "--json", "--type", "change", "--no-interactive", changeName]),
    apply: loadOpenSpecJson(["instructions", "apply", "--change", changeName, "--json"]),
  };
}

function matrixDefaultPath(changeName) {
  return path.join(REPO_ROOT, "openspec", "changes", changeName, "tmux-orch.json");
}

function resolveMatrixPath(matrixArg, changeName) {
  if (!matrixArg) return matrixDefaultPath(changeName);
  return path.isAbsolute(matrixArg) ? matrixArg : path.resolve(REPO_ROOT, matrixArg);
}

function normalizeStringList(value, field, required = false) {
  if (value == null) {
    if (required) throw new CmdError(`${field} is required`);
    return [];
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  const text = String(value).trim();
  if (!text && required) throw new CmdError(`${field} is required`);
  return text ? [text] : [];
}

function normalizeWorkerId(raw, index) {
  const fallback = `w${String(index + 1).padStart(2, "0")}`;
  const id = String(raw || fallback).trim();
  if (!/^[A-Za-z0-9._-]+$/.test(id)) throw new CmdError(`invalid worker id "${id}"; use letters, digits, dot, underscore, or hyphen`);
  return id;
}

function normalizeWorkerCount(raw, field) {
  if (raw == null || raw === "") return 0;
  const count = Number(raw);
  if (!Number.isInteger(count) || count < 1 || count > WORKER_LIMIT) {
    throw new CmdError(`${field} must be an integer from 1 to ${WORKER_LIMIT}`);
  }
  return count;
}

function normalizeCommitType(value, field = "commit.type") {
  const type = String(value || "feat").trim();
  if (!COMMIT_TYPES.has(type)) throw new CmdError(`${field} must be one of: ${Array.from(COMMIT_TYPES).join(", ")}`);
  return type;
}

function normalizeCommitScope(value, field = "commit.scope") {
  const scope = String(value || "worker").trim();
  if (!/^[A-Za-z0-9._-]+$/.test(scope)) throw new CmdError(`${field} must use letters, digits, dot, underscore, or hyphen`);
  return scope;
}

function isForbiddenCommitMessage(message) {
  const text = String(message || "").trim();
  return /^(update|fix|wip)$/i.test(text)
    || /完成\s+w[0-9._-]*\s+工作结果/i.test(text)
    || /ai changes|codex update|AI\s*自动修改/i.test(text);
}

function isValidCommitMessage(message) {
  const text = String(message || "").trim();
  return /^(feat|fix|refactor|docs|test|chore|style|perf)\([A-Za-z0-9._-]+\): .+/.test(text)
    && !isForbiddenCommitMessage(text);
}

function normalizeCommitDescription(value, fallback, field = "commit.description") {
  const description = String(value || fallback || "").replace(/\s+/g, " ").trim();
  if (!description) throw new CmdError(`${field} is required`);
  if (/[。.!]$/.test(description)) return description.slice(0, -1).trim();
  return description;
}

function fallbackCommitDescription(row, type = commitTypeFor(row)) {
  const title = String(row.task_title || row.title || row.task_scope || row.scope || "worker slice").replace(/\s+/g, " ").trim();
  if (row.worker_id === "merge") return `整合 ${row.run_id || "worker"} 分支`;
  if (type === "test") return `增加 ${title} 测试和文档`;
  if (type === "docs") return `更新 ${title} 文档`;
  if (type === "fix") return `修复 ${title}`;
  if (type === "refactor") return `重构 ${title}`;
  if (type === "chore") return `更新 ${title}`;
  return `实现 ${title}`;
}

function normalizeCommitSpec(raw, row) {
  const base = {
    worker_id: row.id || row.worker_id,
    task_title: row.title || row.task_title,
    task_scope: row.scope || row.task_scope,
    ownership_list: row.ownership || row.ownership_list || [],
    run_id: row.run_id || "",
  };
  if (raw == null || raw === "") {
    const type = commitTypeFor(base);
    const scope = commitScopeFor(base);
    const description = fallbackCommitDescription(base, type);
    return { type, scope, description, message: `${type}(${scope}): ${description}` };
  }
  if (typeof raw === "string") {
    const message = raw.trim();
    if (!isValidCommitMessage(message)) throw new CmdError(`commit message must match <type>(<scope>): <中文描述> and describe actual changes: ${message}`);
    const match = message.match(/^([a-z]+)\(([^)]+)\): (.+)$/);
    return { type: match[1], scope: match[2], description: match[3], message };
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new CmdError("commit must be a string or object");
  const type = normalizeCommitType(raw.type || commitTypeFor(base));
  const scope = normalizeCommitScope(raw.scope || commitScopeFor(base));
  const description = normalizeCommitDescription(raw.description, fallbackCommitDescription(base, type));
  const message = `${type}(${scope}): ${description}`;
  if (!isValidCommitMessage(message)) throw new CmdError(`invalid commit message: ${message}`);
  return { type, scope, description, message };
}

function parseOrchMatrixData(data, { changeName, goal, expectedWorkers = 0 } = {}) {
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new CmdError("tmux-orch matrix must be a JSON object");
  const schema = String(data.schema_version || "").trim();
  if (schema !== MATRIX_SCHEMA) throw new CmdError(`tmux-orch matrix schema_version must be ${MATRIX_SCHEMA}`);
  const matrixChange = String(data.change || "").trim();
  if (!matrixChange) throw new CmdError("tmux-orch matrix change is required");
  if (changeName && matrixChange !== changeName) throw new CmdError(`matrix change (${matrixChange}) does not match selected change (${changeName})`);
  if (!Array.isArray(data.workers) || data.workers.length === 0) throw new CmdError("tmux-orch matrix workers must be a non-empty array");
  if (data.workers.length > WORKER_LIMIT) throw new CmdError(`tmux-orch matrix workers must not exceed ${WORKER_LIMIT}`);
  const matrixWorkerCount = normalizeWorkerCount(data.worker_count, "tmux-orch matrix worker_count");
  const explicitWorkerCount = normalizeWorkerCount(expectedWorkers, "--workers");
  for (const [source, count] of [["worker_count", matrixWorkerCount], ["--workers", explicitWorkerCount]]) {
    if (count && count !== data.workers.length) {
      throw new CmdError(`${source} (${count}) must match workers.length (${data.workers.length})`);
    }
  }

  const globalVerify = String(data.verify_cmd || defaultVerifyCmd()).trim() || defaultVerifyCmd();
  const seen = new Set();
  const workers = data.workers.map((raw, index) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new CmdError(`workers[${index}] must be an object`);
    const id = normalizeWorkerId(raw.id, index);
    if (seen.has(id)) throw new CmdError(`duplicate worker id: ${id}`);
    seen.add(id);
    const title = String(raw.title || `Worker ${id}`).trim();
    const scope = String(raw.scope || title).trim();
    const ownership = normalizeStringList(raw.ownership, `workers[${index}].ownership`, true);
    const dependsOn = normalizeStringList(raw.depends_on, `workers[${index}].depends_on`);
    const acceptance = normalizeStringList(raw.acceptance, `workers[${index}].acceptance`);
    const commit = normalizeCommitSpec(raw.commit, { id, title, scope, ownership });
    return {
      id,
      title,
      scope,
      ownership,
      depends_on: dependsOn,
      acceptance,
      verify_cmd: String(raw.verify_cmd || globalVerify).trim() || globalVerify,
      commit,
    };
  });

  for (const worker of workers) {
    for (const dep of worker.depends_on) {
      if (!seen.has(dep)) throw new CmdError(`worker ${worker.id} depends on unknown worker ${dep}`);
      if (dep === worker.id) throw new CmdError(`worker ${worker.id} cannot depend on itself`);
    }
  }

  return {
    schema_version: schema,
    change: matrixChange,
    goal: String(data.goal || goal || "").trim(),
    worker_count: data.workers.length,
    verify_cmd: globalVerify,
    workers,
  };
}

function loadOrchMatrix(file, options) {
  if (!fs.existsSync(file)) {
    throw new CmdError(`tmux-orch matrix not found: ${file}. Create openspec/changes/<change>/tmux-orch.json first.`);
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    throw new CmdError(`invalid tmux-orch matrix JSON at ${file}: ${error.message}`);
  }
  return parseOrchMatrixData(data, options);
}

function defaultWorkerRow({ runId, entry, baseBranch }) {
  const suffix = `${entry.id}-${slugify(entry.title)}`;
  const row = {
    run_id: runId,
    stage: "exec",
    mode: "openspec-parallel",
    worker_id: entry.id,
    task_title: entry.title,
    task_scope: entry.scope,
    ownership: entry.ownership.join(", "),
    ownership_list: entry.ownership,
    depends_on: entry.depends_on.length ? entry.depends_on.join(", ") : "-",
    depends_on_list: entry.depends_on,
    acceptance: entry.acceptance.length ? entry.acceptance.join("; ") : "-",
    acceptance_list: entry.acceptance,
    strategy: "openspec-parallel-worker",
    base_branch: baseBranch,
    worker_branch: `orchestrator/${runId}/${suffix}`,
    worktree_path: `.worktree-tmux-orch/${runId}/${suffix}`,
    verify_cmd: entry.verify_cmd,
    status: "planned",
    session_id: "-",
    result_ref: "-",
    notes: "-",
    matrix_entry: entry,
  };
  row.commit_message = workerCommitMessage(row);
  return row;
}

function buildWorkerRows({ runId, matrix, baseBranch }) {
  return matrix.workers.map((entry) => defaultWorkerRow({ runId, entry, baseBranch }));
}

function escapeCell(value) {
  if (Array.isArray(value)) return escapeCell(value.join(", "));
  return String(value ?? "-").replace(/\n/g, "<br>").replace(/\|/g, "\\|");
}

function renderPlanMarkdown(state) {
  const lines = [];
  lines.push(`# Tmux OpenSpec Parallel Workers Plan: ${state.run_id}`, "");
  lines.push(`- goal: ${state.goal}`);
  lines.push(`- mode: ${state.mode}`);
  lines.push(`- stage: ${state.stage || "draft"}`);
  lines.push(`- execution_kind: ${state.execution_kind}`);
  lines.push(`- worker_runtime: ${state.worker_runtime}`);
  lines.push(`- worker_profile: ${state.worker_profile || "default"}`);
  lines.push(`- openspec_change: ${state.change_name}`);
  lines.push(`- matrix: ${state.matrix_ref}`);
  lines.push(`- worker_count: ${state.worker_count || (state.workers || []).filter((row) => row.worker_id !== "merge").length}`);
  lines.push(`- max_parallel_workers: ${MAX_RUNNING_WORKERS}`);
  lines.push(`- full_verify_cmd: ${state.full_verify_cmd}`);
  lines.push(`- base_branch: ${state.base_branch}`);
  lines.push(`- session_name: ${state.session_name}`);
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

function commitScopeFor(row) {
  const ownership = Array.isArray(row.ownership_list) ? row.ownership_list : [];
  if (row.worker_id === "merge" || ownership.includes("integration")) return "integrate";
  if (ownership.some((item) => /^bin\//.test(item) || /^cli\//.test(item))) return "cli";
  if (ownership.some((item) => /^test(\/|\b)/.test(item))) return "test";
  if (ownership.some((item) => /^docs?\//.test(item) || item.toLowerCase().includes("readme"))) return "docs";
  if (ownership.some((item) => /^src\//.test(item))) return "src";
  if (ownership.some((item) => /^server\//.test(item))) return "server";
  if (ownership.some((item) => /^client\//.test(item) || /^app\//.test(item) || /^ui\//.test(item))) return "ui";
  return "worker";
}

function commitTypeFor(row) {
  const ownership = Array.isArray(row.ownership_list) ? row.ownership_list : [];
  if (row.worker_id === "merge" || ownership.includes("integration")) return "chore";
  if (ownership.some((item) => /^test(\/|\b)/.test(item))) return "test";
  if (ownership.length && ownership.every((item) => /^docs?\//.test(item) || item.toLowerCase().includes("readme"))) return "docs";
  return "feat";
}

function workerCommitMessage(row) {
  const matrixCommit = row.matrix_entry?.commit || row.commit;
  if (matrixCommit?.message && isValidCommitMessage(matrixCommit.message)) return matrixCommit.message;
  if (row.worker_id === "merge") return `chore(integrate): 整合 ${row.run_id} worker 分支`;
  const type = commitTypeFor(row);
  const scope = commitScopeFor(row);
  return `${type}(${scope}): ${fallbackCommitDescription(row, type)}`;
}

function buildClaudeEnv(profile) {
  const env = {};
  if (profile.base_url) env.ANTHROPIC_BASE_URL = profile.base_url;
  if (profile.api_key) {
    const authEnv = normalizeAuthEnv(profile.auth_env || (profile.base_url ? "auth_token" : "api_key"));
    if (authEnv === "api_key" || authEnv === "both") env.ANTHROPIC_API_KEY = profile.api_key;
    if (authEnv === "auth_token" || authEnv === "both") env.ANTHROPIC_AUTH_TOKEN = profile.api_key;
  }
  if (profile.model) {
    env.ANTHROPIC_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_HAIKU_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_OPUS_MODEL = profile.model;
    env.ANTHROPIC_DEFAULT_SONNET_MODEL = profile.model;
  }
  return env;
}

function buildWorkerCommand(profile) {
  const custom = String(process.env.TMUX_ORCH_CLAUDE_CMD || "").trim();
  if (custom) return custom;
  const parts = [];
  parts.push("claude --bare --no-session-persistence -p --dangerously-skip-permissions --permission-mode bypassPermissions --output-format text --debug-file \"$DEBUG_FILE\"");
  if (profile.model) parts.push(`--model ${shellQuote(profile.model)}`);
  parts.push("\"$PROMPT_TEXT\" >\"$MSG_FILE\" 2>>\"$LOG_FILE\"");
  return parts.join(" ");
}

function cmdInternalEnv(args) {
  const runtime = String(args[0] || "claude").trim();
  if (runtime !== "claude") throw new CmdError(`unsupported internal env runtime: ${runtime}`);
  for (const [key, value] of Object.entries(buildClaudeEnv(activeProfile()))) {
    console.log(`export ${key}=${shellQuote(value)}`);
  }
  return 0;
}

function openSpecPromptBlock(state) {
  const ctx = state.openspec_context || {};
  const apply = ctx.apply || {};
  const contextFiles = apply.contextFiles && typeof apply.contextFiles === "object" ? apply.contextFiles : {};
  const contextLines = Object.entries(contextFiles).filter(([, v]) => v).map(([k, v]) => `- ${k}: ${v}`);
  const tasks = Array.isArray(apply.tasks) ? apply.tasks.slice(0, 20).map((item) => {
    if (item && typeof item === "object") return `- [${item.status || "-"}] ${item.title || item.text || item.description || "-"}`;
    return `- ${item}`;
  }) : [];
  return `
- change: ${ctx.change_name || state.change_name || "-"}
- change_path: ${ctx.change_path || `openspec/changes/${state.change_name}`}
- apply_state: ${apply.state || "-"}
- instruction: ${apply.instruction || "-"}
- context_files:
${(contextLines.length ? contextLines : ["- Read proposal.md, design.md, tasks.md, and specs/** under the change path."]).join("\n")}
- apply_tasks:
${(tasks.length ? tasks : ["- Use tasks.md and this worker's matrix entry as the task source."]).join("\n")}
- status_json:
${truncateJson(ctx.status || {}, 2400)}
- show_json:
${truncateJson(ctx.show || {}, 3600)}
`.trim();
}

function summaryProtocol() {
  return `
Append this exact structured summary at the end of your final response:
${SUMMARY_MARKER_BEGIN}
status: done|blocked|failed
summary: one sentence
key_changes: semicolon-separated key changes
verify: verification commands and outcomes
risks: risks or follow-up notes
next_steps: next recommended action
commit: <type>(<scope>): <中文描述实际修改内容>
${SUMMARY_MARKER_END}
`.trim();
}

function workerPrompt(state, row) {
  return `
You are tmux implementation worker ${row.worker_id}. The parent agent owns OpenSpec planning; you only implement your assigned slice.

Global goal:
${state.goal}

OpenSpec context:
${openSpecPromptBlock(state)}

Worker matrix entry:
${JSON.stringify(row.matrix_entry || {}, null, 2)}

Execution assignment:
- branch: ${row.worker_branch}
- worktree_path: ${row.worktree_path}
- ownership: ${(row.ownership_list || []).join(", ") || "-"}
- depends_on: ${(row.depends_on_list || []).join(", ") || "-"}
- acceptance: ${(row.acceptance_list || []).join("; ") || "-"}
- verify_cmd: ${row.verify_cmd || state.full_verify_cmd || defaultVerifyCmd()}

Rules:
1. Read the OpenSpec change files before editing implementation code.
2. Treat OpenSpec as the implementation contract. If code and OpenSpec conflict, stop and report the conflict.
3. Edit only files covered by ownership unless a minimal boundary edit is required; explain any boundary edit.
4. Do not edit OpenSpec workflow artifacts unless they are explicitly listed in ownership.
5. Do not switch branches and do not run tmux orchestration commands.
6. Run verify_cmd when feasible. If it cannot run, state the exact reason.
7. Do not output Markdown local file links or file:// URIs; use plain paths.
8. Do not create manual git commits. The orchestrator commits successful worker changes after checking status and ownership.
9. In the summary commit field, write the real change, not a generic worker label. Good: feat(cli): 增加任务流命令参数校验; bad: feat(cli): 完成 w02 工作结果.

${summaryProtocol()}
`.trim() + "\n";
}

function integrationPrompt(state, row, sourceRows) {
  const branches = sourceRows.map((item) => `- ${item.worker_id}: branch=${item.worker_branch}; ownership=${item.ownership || "-"}; summary=${item.task_title || "-"}`).join("\n");
  return `
You are tmux integration worker ${row.worker_id}. The parent agent owns orchestration; you merge completed worker branches and run final verification.

Global goal:
${state.goal}

OpenSpec context:
${openSpecPromptBlock(state)}

Completed worker branches to integrate:
${branches}

Integration assignment:
- branch: ${row.worker_branch}
- worktree_path: ${row.worktree_path}
- verify_cmd: ${row.verify_cmd || state.full_verify_cmd || defaultVerifyCmd()}

Rules:
1. Merge the completed worker branches into the integration branch.
2. Preserve worker ownership boundaries where possible; make boundary fixes only when integration requires them.
3. Resolve conflicts in this worktree and document conflict decisions.
4. Do not archive the OpenSpec change.
5. Run verify_cmd when feasible. If it cannot run, state the exact reason.
6. Do not output Markdown local file links or file:// URIs; use plain paths.
7. Do not create manual git commits. The orchestrator commits successful integration changes after checking status.
8. In the summary commit field, write the real integration change, not a generic worker label.

${summaryProtocol()}
`.trim() + "\n";
}

function writeWorkerFiles(state, row, promptText) {
  const paths = workerPaths(state.run_id, row.worker_id);
  for (const file of Object.values(paths)) fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(paths.prompt, promptText);
  const profile = activeProfile();
  const cmd = buildWorkerCommand(profile);
  const worktreeAbs = path.resolve(REPO_ROOT, row.worktree_path);
  const ownershipPaths = Array.isArray(row.ownership_list) ? row.ownership_list.filter((item) => item && item !== "integration") : [];
  const ownershipArray = ownershipPaths.map(shellQuote).join(" ");
  const commitMessage = row.commit_message || workerCommitMessage(row);
  const script = `#!/usr/bin/env bash
set -u
ORCH_CLI=${shellQuote(SCRIPT_FILE)}
ORCH_CONFIG=${shellQuote(CONFIG_FILE)}
WORKTREE=${shellQuote(worktreeAbs)}
PROMPT_FILE=${shellQuote(paths.prompt)}
LOG_FILE=${shellQuote(paths.log)}
DEBUG_FILE=${shellQuote(paths.debug)}
DONE_FILE=${shellQuote(paths.done)}
MSG_FILE=${shellQuote(paths.message)}
WORKER_ID=${shellQuote(row.worker_id)}
WORKER_PROFILE=${shellQuote(profile.name)}
COMMIT_MESSAGE=${shellQuote(commitMessage)}
OWNERSHIP_PATHS=(${ownershipArray})
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

ENV_EXPORTS="$(TMUX_ORCH_CONFIG="$ORCH_CONFIG" node "$ORCH_CLI" internal-env claude 2>>"$LOG_FILE")"
env_rc=$?
if [ "$env_rc" -ne 0 ]; then
  printf '%s\n' "[tmux-orch] failed to load worker environment; see $LOG_FILE" >"$MSG_FILE"
  write_done "$env_rc"
  exit 0
fi
eval "$ENV_EXPORTS"

cd "$WORKTREE"
PROMPT_TEXT="$(cat "$PROMPT_FILE")"
echo "[tmux-orch] runtime=claude profile=$WORKER_PROFILE" >>"$LOG_FILE"
echo "[tmux-orch] debug_file=$DEBUG_FILE timeout_sec=$WORKER_TIMEOUT_SEC" >>"$LOG_FILE"
${cmd}
rc=$?
if [ "$rc" -eq 0 ] && [ -f "$MSG_FILE" ] && grep -Eq '^status:[[:space:]]*(failed|blocked)[[:space:]]*$' "$MSG_FILE"; then
  echo "[tmux-orch] worker summary reported blocked or failed; skipping auto-commit" >>"$LOG_FILE"
  rc=1
fi
if [ "$rc" -eq 0 ] && [ -f "$MSG_FILE" ]; then
  SUMMARY_COMMIT="$(awk '/^commit:[[:space:]]*/ { sub(/^commit:[[:space:]]*/, ""); print; exit }' "$MSG_FILE")"
  if [ -n "$SUMMARY_COMMIT" ]; then
    if [[ "$SUMMARY_COMMIT" =~ ^(feat|fix|refactor|docs|test|chore|style|perf)\\([A-Za-z0-9._-]+\\):\\ .+ ]] && [[ ! "$SUMMARY_COMMIT" =~ [Cc]odex[[:space:]]update|[Aa][Ii][[:space:]]changes|完成[[:space:]]+w[0-9._-]*[[:space:]]+工作结果 ]]; then
      COMMIT_MESSAGE="$SUMMARY_COMMIT"
      echo "[tmux-orch] using worker summary commit: $COMMIT_MESSAGE" >>"$LOG_FILE"
    else
      printf '%s\n' "[tmux-orch] invalid commit summary field: $SUMMARY_COMMIT" >>"$MSG_FILE"
      rc=1
    fi
  fi
fi
if [ "$rc" -eq 0 ] && [ -n "$(git status --porcelain)" ]; then
  echo "[tmux-orch] preparing commit for $WORKER_ID" >>"$LOG_FILE"
  git status --short >>"$LOG_FILE" 2>&1
  git diff --stat >>"$LOG_FILE" 2>&1 || true
  if [ "$WORKER_ID" = "merge" ] || [ "\${#OWNERSHIP_PATHS[@]}" -eq 0 ]; then
    git add -A >>"$LOG_FILE" 2>&1
  else
    git add -A -- "\${OWNERSHIP_PATHS[@]}" >>"$LOG_FILE" 2>&1
  fi
  git diff --cached --stat >>"$LOG_FILE" 2>&1 || true
  if ! git diff --cached --quiet; then
    git commit -m "$COMMIT_MESSAGE" >>"$LOG_FILE" 2>&1
    commit_rc=$?
    if [ "$commit_rc" -ne 0 ]; then
      printf '%s\n' "[tmux-orch] git commit failed; see $LOG_FILE" >>"$MSG_FILE"
      rc="$commit_rc"
    fi
  fi
  if [ "$rc" -eq 0 ] && [ -n "$(git status --porcelain)" ]; then
    printf '%s\n' "[tmux-orch] uncommitted changes remain outside ownership; see $LOG_FILE" >>"$MSG_FILE"
    git status --short >>"$LOG_FILE" 2>&1
    rc=1
  fi
fi
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

function mergeDoneDependencies(row, rows, wtPath) {
  const deps = Array.isArray(row.depends_on_list) ? row.depends_on_list : [];
  const merged = [];
  for (const depId of deps) {
    const dep = rows.find((item) => item.worker_id === depId);
    if (!dep) throw new CmdError(`worker ${row.worker_id} dependency ${depId} is missing`);
    if (dep.status !== "done") throw new CmdError(`worker ${row.worker_id} dependency ${depId} is ${dep.status}, not done`);
    if (!dep.worker_branch || dep.worker_branch === "-") throw new CmdError(`worker ${row.worker_id} dependency ${depId} has no branch`);
    const alreadyMerged = sh(["git", "merge-base", "--is-ancestor", dep.worker_branch, "HEAD"], { cwd: wtPath, check: false }).status === 0;
    if (alreadyMerged) continue;
    const proc = sh(["git", "merge", "--no-edit", dep.worker_branch], { cwd: wtPath, check: false });
    if (proc.status !== 0) {
      throw new CmdError(`failed to merge dependency ${depId} (${dep.worker_branch}) into ${row.worker_branch}\nstdout:\n${proc.stdout}\nstderr:\n${proc.stderr}`);
    }
    merged.push(depId);
  }
  return merged;
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

function dependencyReadiness(row, rows) {
  const deps = Array.isArray(row.depends_on_list) ? row.depends_on_list : [];
  for (const depId of deps) {
    const dep = rows.find((item) => item.worker_id === depId);
    if (!dep) return { ready: false, blocked: true, detail: `missing dependency ${depId}` };
    if (dep.status === "done") continue;
    if (WORKER_TERMINAL.has(dep.status)) return { ready: false, blocked: true, detail: `dependency ${depId} is ${dep.status}` };
    return { ready: false, blocked: false, detail: `waiting for dependency ${depId}` };
  }
  return { ready: true, blocked: false, detail: "" };
}

function collectReadyImplementationRows(rows) {
  const readyRows = [];
  for (const row of rows) {
    if (row.worker_id === "merge") continue;
    if (row.status === "done" || row.status === "running") continue;
    const dep = dependencyReadiness(row, rows);
    if (dep.blocked) {
      row.status = "blocked";
      row.notes = appendNote(row.notes, dep.detail);
      continue;
    }
    if (!dep.ready) {
      row.notes = appendNote(row.notes, dep.detail);
      continue;
    }
    readyRows.push(row);
  }
  return readyRows;
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

function launchReadyWorkers(state, { reuse = false } = {}) {
  ensureRuntimeAvailable("claude");
  const check = validateOpenSpecParent();
  if (!check.ok) throw new CmdError(`parent OpenSpec preflight failed: ${check.detail}`);
  state.openspec_context = buildOpenSpecContext(state.change_name);
  const rows = state.workers || [];
  const running = rows.filter((row) => row.status === "running").length;
  const readyRows = collectReadyImplementationRows(rows);

  const sessionName = state.session_name || `orch-${state.run_id}`.slice(0, 40);
  state.session_name = sessionName;
  if (tmuxHasSession(sessionName) && running > 0 && !reuse) {
    throw new CmdError(`tmux session already has running workers: ${sessionName} (use --reuse-session or wait for status)`);
  }

  let firstPane = "";
  if (readyRows.length && !tmuxHasSession(sessionName)) firstPane = tmuxNewSession(sessionName);
  let launched = 0;
  for (const row of readyRows) {
    if (launched + running >= MAX_RUNNING_WORKERS) break;
    const wtPath = ensureWorkerWorktree(row);
    const mergedDeps = mergeDoneDependencies(row, rows, wtPath);
    if (mergedDeps.length) row.notes = appendNote(row.notes, `merged_deps=${mergedDeps.join(",")}`);
    const paneId = firstPane || (row.pane_id && tmuxPaneExists(row.pane_id) ? row.pane_id : tmuxNewPane(sessionName));
    firstPane = "";
    startWorker(state, row, paneId, workerPrompt(state, row));
    launched += 1;
  }
  state.stage = launched ? "exec" : state.stage || "exec";
  appendEvent(state, "run", {
    session_name: sessionName,
    launched,
    ready: readyRows.length,
    running,
    max_parallel_workers: MAX_RUNNING_WORKERS,
  });
  return {
    sessionName,
    launched,
    ready: readyRows.length,
    running,
    maxParallelWorkers: MAX_RUNNING_WORKERS,
  };
}

function workerStatusCounter(rows) {
  const counts = {};
  for (const row of rows || []) counts[row.status || "unknown"] = (counts[row.status || "unknown"] || 0) + 1;
  return counts;
}

function implementationRows(state) {
  return (state.workers || []).filter((row) => row.worker_id !== "merge");
}

function mergeWorkerRow(state) {
  return (state.workers || []).find((row) => row.worker_id === "merge") || null;
}

function hasRunningWorkers(state) {
  return (state.workers || []).some((row) => row.status === "running");
}

function allImplementationDone(state) {
  const rows = implementationRows(state);
  return rows.length > 0 && rows.every((row) => row.status === "done");
}

function terminalProblemRows(rows) {
  return (rows || []).filter((row) => row.status === "failed" || row.status === "blocked");
}

function waitAssessment(state, until, { autoIntegrate = false } = {}) {
  const impl = implementationRows(state);
  const merge = mergeWorkerRow(state);
  const running = hasRunningWorkers(state);
  const implProblems = terminalProblemRows(impl);
  const allImplDone = impl.length > 0 && impl.every((row) => row.status === "done");
  const mergeProblem = merge && (merge.status === "failed" || merge.status === "blocked") ? merge : null;

  if (until === "wave-done") {
    if (!running) {
      const problems = terminalProblemRows(state.workers || []);
      return { done: true, ok: problems.length === 0, reason: problems.length ? `terminal_problem:${problems.map((row) => `${row.worker_id}:${row.status}`).join(",")}` : "wave_done" };
    }
    return { done: false, ok: true, reason: "running" };
  }

  if (until === "all-done") {
    if (implProblems.length) return { done: true, ok: false, reason: `implementation_problem:${implProblems.map((row) => `${row.worker_id}:${row.status}`).join(",")}` };
    if (allImplDone) return { done: true, ok: true, reason: "all_done" };
    return { done: false, ok: true, reason: running ? "running" : "waiting_for_next_wave" };
  }

  if (until === "integrated") {
    if (mergeProblem) return { done: true, ok: false, reason: `integration_problem:${merge.worker_id}:${merge.status}` };
    if (merge?.status === "done") return { done: true, ok: true, reason: "integrated" };
    return { done: false, ok: true, reason: merge ? "integration_running" : "integration_not_started" };
  }

  if (until === "complete") {
    if (implProblems.length) return { done: true, ok: false, reason: `implementation_problem:${implProblems.map((row) => `${row.worker_id}:${row.status}`).join(",")}` };
    if (mergeProblem) return { done: true, ok: false, reason: `integration_problem:${merge.worker_id}:${merge.status}` };
    if (!allImplDone) return { done: false, ok: true, reason: running ? "running" : "waiting_for_next_wave" };
    if (autoIntegrate || merge) {
      if (merge?.status === "done") return { done: true, ok: true, reason: "complete" };
      return { done: false, ok: true, reason: merge ? "integration_running" : "integration_not_started" };
    }
    return { done: true, ok: true, reason: "complete_without_integration" };
  }

  throw new CmdError(`unknown wait target: ${until}`);
}

function loadWorkerResult(row) {
  if (!row.result_ref || row.result_ref === "-") return { content: "", summary: {} };
  const file = path.resolve(REPO_ROOT, row.result_ref);
  if (!fs.existsSync(file)) return { content: "", summary: {} };
  const content = fs.readFileSync(file, "utf8");
  return { content, summary: parseSummary(content) };
}

function buildInspectReport(state) {
  const lines = [`# Inspect: ${state.run_id}`, "", `- goal: ${state.goal}`, `- mode: ${state.mode}`, `- stage: ${state.stage || "-"}`, `- openspec_change: ${state.change_name}`, ""];
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

function startIntegrationWorker(state, { allowPartial = false } = {}) {
  ensureRuntimeAvailable("claude");
  const implementationRows = (state.workers || []).filter((row) => row.worker_id !== "merge");
  const notDone = implementationRows.filter((row) => row.status !== "done");
  if (notDone.length && !allowPartial) {
    throw new CmdError(`cannot integrate until all implementation workers are done: ${notDone.map((row) => `${row.worker_id}:${row.status}`).join(", ")}`);
  }
  const doneRows = implementationRows.filter((row) => row.status === "done" && row.worker_branch && row.worker_branch !== "-");
  if (!doneRows.length) throw new CmdError("no done worker branches available for child integration");
  let mergeRow = (state.workers || []).find((row) => row.worker_id === "merge");
  if (!mergeRow) {
    mergeRow = {
      run_id: state.run_id,
      stage: "integrate",
      mode: "openspec-parallel",
      worker_id: "merge",
      task_title: "Integrate completed OpenSpec worker branches",
      task_scope: "Merge completed child worker branches into one integration branch and run final verification",
      ownership: "integration",
      ownership_list: ["integration"],
      depends_on: doneRows.map((row) => row.worker_id).join(", "),
      depends_on_list: doneRows.map((row) => row.worker_id),
      acceptance: "All completed worker branches are integrated and final verification is run",
      acceptance_list: ["All completed worker branches are integrated", "Final verification is run"],
      strategy: "integration",
      base_branch: state.base_branch,
      worker_branch: `orchestrator/${state.run_id}/integrate`,
      worktree_path: `.worktree-tmux-orch/${state.run_id}/integrate`,
      verify_cmd: state.full_verify_cmd || defaultVerifyCmd(),
      status: "planned",
      session_id: "-",
      result_ref: "-",
      notes: "-",
      matrix_entry: {
        id: "merge",
        title: "Integrate completed OpenSpec worker branches",
        scope: "Merge completed child worker branches into one integration branch and run final verification",
        ownership: ["integration"],
        depends_on: doneRows.map((row) => row.worker_id),
        acceptance: ["All completed worker branches are integrated", "Final verification is run"],
        verify_cmd: state.full_verify_cmd || defaultVerifyCmd(),
      },
    };
    mergeRow.commit_message = workerCommitMessage(mergeRow);
    state.workers.push(mergeRow);
  }
  if (mergeRow.status === "done") return { started: false, alreadyDone: true, sourceWorkers: doneRows.length, mergeRow };
  if (mergeRow.status === "running") throw new CmdError("integration worker is already running");
  ensureWorkerWorktree(mergeRow);
  const sessionName = state.session_name || `orch-${state.run_id}`.slice(0, 40);
  state.session_name = sessionName;
  const paneId = tmuxHasSession(sessionName) ? tmuxNewPane(sessionName) : tmuxNewSession(sessionName);
  startWorker(state, mergeRow, paneId, integrationPrompt(state, mergeRow, doneRows));
  state.stage = "integrate";
  appendEvent(state, "integrate", { session_name: sessionName, source_workers: doneRows.map((row) => row.worker_id) });
  return { started: true, alreadyDone: false, sessionName, sourceWorkers: doneRows.length, mergeRow };
}

function cmdDoctor() {
  ensureDirs();
  const missing = ["git", "tmux", "claude"].filter((tool) => !ensureToolExists(tool));
  const profile = activeProfile();
  const openspec = validateOpenSpecParent();
  console.log(`repo_root=${REPO_ROOT}`);
  console.log(`invocation_cwd=${INVOKE_CWD()}`);
  console.log(`orch_dir=${ORCH_DIR}`);
  console.log(`orch_plan=${ORCH_PLAN_FILE}`);
  console.log(`state_dir=${STATE_DIR}`);
  console.log(`config_file=${CONFIG_FILE}`);
  console.log(`git_repo=${gitRepoRoot() || "none"}`);
  console.log(`missing_tools=${missing.length ? missing.join(", ") : "none"}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${profile.name}`);
  console.log(`worker_model=${profile.model || "(claude default)"}`);
  console.log(`worker_base_url=${profile.base_url || "(default)"}`);
  console.log(`openspec_cmd=${resolveOpenSpecCommand()}`);
  console.log(`openspec_initialized=${openspecInitialized() ? "yes" : "no"}`);
  console.log(`openspec_status=${openspec.ok ? "ok" : "failed"}`);
  console.log(`openspec_detail=${openspec.detail}`);
  console.log(`max_parallel_workers=${MAX_RUNNING_WORKERS}`);
  console.log("quickstart=draft -> run -> status -> inspect -> integrate -> close");
  return missing.length || !openspec.ok ? 1 : 0;
}

function cmdDraft(args) {
  const goal = requireArg(args, "--goal").trim();
  if (!goal) throw new CmdError("--goal must not be empty");
  const changeName = selectOpenSpecChange(getArg(args, "--change"));
  const matrixFile = resolveMatrixPath(getArg(args, "--matrix"), changeName);
  const expectedWorkers = getArg(args, "--workers");
  const runId = getArg(args, "--run") || runIdNow();
  const check = validateOpenSpecParent();
  if (!check.ok) throw new CmdError(`parent OpenSpec preflight failed: ${check.detail}`);
  const matrix = loadOrchMatrix(matrixFile, { changeName, goal, expectedWorkers });
  const baseBranch = gitCurrentBranch();
  const rows = buildWorkerRows({ runId, matrix, baseBranch });
  const profile = activeProfile();
  const state = {
    run_id: runId,
    goal,
    mode: "openspec-parallel",
    stage: "draft",
    execution_kind: "modify",
    worker_runtime: "claude",
    worker_profile: profile.name,
    execution_policy: "openspec_parallel_review_first",
    base_branch: baseBranch,
    session_name: `orch-${runId}`.slice(0, 40),
    change_name: changeName,
    matrix_ref: rel(matrixFile),
    worker_count: matrix.worker_count,
    full_verify_cmd: matrix.verify_cmd || defaultVerifyCmd(),
    created_at: isoNow(),
    updated_at: isoNow(),
    workers: rows,
    events: [],
    openspec_context: buildOpenSpecContext(changeName),
  };
  appendEvent(state, "draft", {
    worker_count: rows.length,
    worker_profile: profile.name,
    execution_policy: state.execution_policy,
    change_name: changeName,
    matrix: state.matrix_ref,
  });
  saveState(state);
  writePlan(state);
  console.log(`run_id=${runId}`);
  console.log("mode=openspec-parallel");
  console.log("execution_kind=modify");
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${profile.name}`);
  console.log(`openspec_change=${changeName}`);
  console.log(`matrix=${state.matrix_ref}`);
  console.log(`workers=${rows.length}`);
  console.log(`execution_policy=${state.execution_policy}`);
  console.log(`plan=${ORCH_PLAN_FILE}`);
  return 0;
}

function cmdRun(args) {
  const runId = requireArg(args, "--run");
  const state = loadState(runId);
  refreshWorkerStatuses(state);
  const result = launchReadyWorkers(state, { reuse: hasFlag(args, "--reuse-session") });
  saveState(state);
  writePlan(state);
  console.log(`run_id=${state.run_id}`);
  console.log(`session=${result.sessionName}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${state.worker_profile || "default"}`);
  console.log(`max_parallel_workers=${result.maxParallelWorkers}`);
  console.log(`ready=${result.ready}`);
  console.log(`launched=${result.launched}`);
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
    console.log(JSON.stringify({ run_id: runId, stage: state.stage || "-", status: counts, workers: state.workers || [] }, null, 2));
    return 0;
  }
  console.log(`run_id=${runId}`);
  console.log(`stage=${state.stage || "-"}`);
  console.log(`execution_kind=${state.execution_kind || "modify"}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${state.worker_profile || "default"}`);
  console.log(`openspec_change=${state.change_name || "-"}`);
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
  const state = loadState(requireArg(args, "--run"));
  refreshWorkerStatuses(state);
  const result = startIntegrationWorker(state, { allowPartial: hasFlag(args, "--allow-partial") });
  saveState(state);
  writePlan(state);
  console.log(`run_id=${state.run_id}`);
  console.log(`session=${state.session_name || "-"}`);
  console.log("worker_runtime=claude");
  console.log(`worker_profile=${state.worker_profile || "default"}`);
  console.log("integration_worker=merge");
  console.log(`source_workers=${result.sourceWorkers}`);
  console.log(`started=${result.started ? "yes" : "no"}`);
  return 0;
}

function parseNumberArg(args, name, defaultValue, { min = 0, max = Number.POSITIVE_INFINITY } = {}) {
  const raw = getArg(args, name);
  if (!raw) return defaultValue;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new CmdError(`${name} must be a number from ${min} to ${max}`);
  }
  return value;
}

function sleepMs(ms) {
  if (ms <= 0) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function waitPayload(state, assessment, { until, nextWave, autoIntegrate, timeoutSec, intervalSec }) {
  return {
    run_id: state.run_id,
    stage: state.stage || "-",
    until,
    done: assessment.done,
    ok: assessment.ok,
    reason: assessment.reason,
    next_wave: nextWave,
    auto_integrate: autoIntegrate,
    timeout_sec: timeoutSec,
    interval_sec: intervalSec,
    status: workerStatusCounter(state.workers || []),
    workers: (state.workers || []).map((row) => ({
      worker_id: row.worker_id,
      status: row.status,
      branch: row.worker_branch,
      result_ref: row.result_ref,
      notes: row.notes,
    })),
  };
}

function printWaitPayload(payload, json) {
  if (json) {
    console.log(JSON.stringify(payload, null, 2));
    return;
  }
  console.log(`run_id=${payload.run_id}`);
  console.log(`stage=${payload.stage}`);
  console.log(`until=${payload.until}`);
  console.log(`done=${payload.done ? "yes" : "no"}`);
  console.log(`ok=${payload.ok ? "yes" : "no"}`);
  console.log(`reason=${payload.reason}`);
  for (const key of Object.keys(payload.status).sort()) console.log(`- ${key}: ${payload.status[key]}`);
}

function cmdWait(args) {
  const runId = requireArg(args, "--run");
  const nextWave = hasFlag(args, "--next-wave");
  const autoIntegrate = hasFlag(args, "--auto-integrate");
  const explicitUntil = getArg(args, "--until");
  const until = explicitUntil || (autoIntegrate ? "complete" : nextWave ? "all-done" : "wave-done");
  const validUntil = new Set(["wave-done", "all-done", "integrated", "complete"]);
  if (!validUntil.has(until)) throw new CmdError(`--until must be one of: ${Array.from(validUntil).join(", ")}`);
  const timeoutSec = parseNumberArg(args, "--timeout", 3600, { min: 0 });
  const intervalSec = parseNumberArg(args, "--interval", 5, { min: 0.25, max: 300 });
  const json = hasFlag(args, "--json");
  const deadline = timeoutSec > 0 ? Date.now() + timeoutSec * 1000 : Number.POSITIVE_INFINITY;
  const state = loadState(runId);
  appendEvent(state, "wait_start", { until, next_wave: nextWave, auto_integrate: autoIntegrate, timeout_sec: timeoutSec, interval_sec: intervalSec });
  if (!json) {
    console.log(`run_id=${runId}`);
    console.log(`until=${until}`);
    console.log(`next_wave=${nextWave ? "yes" : "no"}`);
    console.log(`auto_integrate=${autoIntegrate ? "yes" : "no"}`);
    console.log(`timeout_sec=${timeoutSec}`);
    console.log(`interval_sec=${intervalSec}`);
  }

  while (true) {
    refreshWorkerStatuses(state);

    if (nextWave && (until === "all-done" || until === "complete") && !hasRunningWorkers(state) && !allImplementationDone(state)) {
      const launched = launchReadyWorkers(state, { reuse: true });
      if (launched.launched > 0) {
        appendEvent(state, "wait_next_wave", { launched: launched.launched, ready: launched.ready });
        if (!json) console.log(`next_wave_launched=${launched.launched}`);
      } else if (!hasRunningWorkers(state)) {
        const stalled = waitAssessment(state, until, { autoIntegrate });
        if (!stalled.done) {
          const payload = waitPayload(state, { done: true, ok: false, reason: "stalled_no_ready_workers" }, { until, nextWave, autoIntegrate, timeoutSec, intervalSec });
          appendEvent(state, "wait_stalled", { reason: payload.reason });
          saveState(state);
          writePlan(state);
          printWaitPayload(payload, json);
          return 2;
        }
      }
    }

    if (autoIntegrate && (until === "integrated" || until === "complete") && allImplementationDone(state)) {
      const merge = mergeWorkerRow(state);
      if (!merge || merge.status === "planned") {
        const result = startIntegrationWorker(state, { allowPartial: false });
        appendEvent(state, "wait_auto_integrate", { started: result.started, source_workers: result.sourceWorkers });
        if (!json && result.started) console.log("auto_integrate_started=yes");
      }
    }

    const assessment = waitAssessment(state, until, { autoIntegrate });
    saveState(state);
    writePlan(state);
    if (assessment.done) {
      appendEvent(state, "wait_complete", { until, ok: assessment.ok, reason: assessment.reason });
      saveState(state);
      writePlan(state);
      printWaitPayload(waitPayload(state, assessment, { until, nextWave, autoIntegrate, timeoutSec, intervalSec }), json);
      return assessment.ok ? 0 : 2;
    }

    if (Date.now() >= deadline) {
      const timeout = { done: true, ok: false, reason: "timeout" };
      appendEvent(state, "wait_timeout", { until, timeout_sec: timeoutSec });
      saveState(state);
      writePlan(state);
      printWaitPayload(waitPayload(state, timeout, { until, nextWave, autoIntegrate, timeoutSec, intervalSec }), json);
      return 124;
    }

    sleepMs(intervalSec * 1000);
  }
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
  return `${SKILL_NAME}: doctor | draft --goal <goal> --change <change> [--workers n] [--matrix path] [--run id] | run --run id [--reuse-session] | wait --run id [--next-wave] [--until wave-done|all-done|integrated|complete] [--auto-integrate] [--timeout sec] [--interval sec] [--json] | status --run id [--json] | inspect --run id | integrate --run id [--allow-partial] | close --run id`;
}

function main(argv) {
  const [command, ...args] = argv;
  if (command === "internal-env") return cmdInternalEnv(args);
  ensureDirs();
  if (!command || command === "-h" || command === "--help") {
    console.log(usage());
    return 0;
  }
  switch (command) {
    case "doctor": return cmdDoctor(args);
    case "draft": return cmdDraft(args);
    case "run": return cmdRun(args);
    case "wait": return cmdWait(args);
    case "status": return cmdStatus(args);
    case "inspect": return cmdInspect(args);
    case "integrate": return cmdIntegrate(args);
    case "close": return cmdClose(args);
    default: throw new CmdError(`unknown command: ${command}\n${usage()}`);
  }
}

function isMainModule() {
  return process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
}

export {
  buildClaudeEnv,
  CmdError,
  commitScopeFor,
  commitTypeFor,
  MATRIX_SCHEMA,
  dependencyReadiness,
  normalizeAuthEnv,
  parseOrchMatrixData,
  parseSummary,
  selectChangeName,
  usage,
  waitAssessment,
  workerCommitMessage,
};

if (isMainModule()) {
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
}
