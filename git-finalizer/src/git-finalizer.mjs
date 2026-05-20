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

function revParse(root, ref) {
  const proc = sh(["git", "rev-parse", "--verify", ref], { cwd: root, check: false });
  if (proc.status !== 0) throw new CmdError(`unknown git ref: ${ref}`);
  return proc.stdout.trim();
}

function branchExists(root, branch) {
  return sh(["git", "rev-parse", "--verify", "--quiet", branch], { cwd: root, check: false }).status === 0;
}

function mergeBase(root, a, b) {
  return sh(["git", "merge-base", a, b], { cwd: root }).stdout.trim();
}

function topStashSha(root) {
  const r = sh(["git", "rev-parse", "--quiet", "--verify", "refs/stash"], { cwd: root, check: false });
  return r.status === 0 ? r.stdout.trim() : "";
}

function topStashMessage(root) {
  const r = sh(["git", "log", "-1", "--format=%s", "refs/stash"], { cwd: root, check: false });
  return r.status === 0 ? r.stdout.trim() : "";
}

// safeStashPop pops the top stash only if it matches the expected label we just pushed.
// This protects pre-existing user stashes when our auto-stash push silently no-op'd
// (e.g. only submodule/gitlink modifications, which git stash cannot capture).
function safeStashPop(root, expectedLabel) {
  const sha = topStashSha(root);
  if (!sha) return { skipped: true, reason: "no_stash_present" };
  const subject = topStashMessage(root);
  if (expectedLabel && subject && !subject.includes(expectedLabel)) {
    return { skipped: true, reason: `top_stash_mismatch:${subject.slice(0, 64)}` };
  }
  const pop = sh(["git", "stash", "pop"], { cwd: root, check: false });
  if (pop.status !== 0) {
    return { conflict: true, stderr: pop.stderr || pop.stdout };
  }
  return { ok: true };
}

function gatherMergeContext(root, source, into) {
  const sourceSha = revParse(root, source);
  const intoSha = revParse(root, into);
  const baseSha = mergeBase(root, intoSha, sourceSha);
  const range = `${baseSha}..${sourceSha}`;
  const log = sh(
    ["git", "log", range, "--reverse", "--pretty=format:%h %s%n%b%n---COMMIT-END---"],
    { cwd: root, check: false },
  ).stdout.trim();
  const stat = sh(["git", "diff", "--stat", `${baseSha}...${sourceSha}`], { cwd: root, check: false }).stdout.trim();
  const nameStatus = sh(["git", "diff", "--name-status", `${baseSha}...${sourceSha}`], { cwd: root, check: false }).stdout.trim();
  const aheadBehind = sh(
    ["git", "rev-list", "--left-right", "--count", `${intoSha}...${sourceSha}`],
    { cwd: root, check: false },
  ).stdout.trim();
  return { sourceSha, intoSha, baseSha, log, stat, nameStatus, aheadBehind };
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
    settings: path.join(STATE_DIR, runId, "claude-settings.json"),
  };
}

function buildPrompt(state, root) {
  if (state.kind === "merge") return buildMergePrompt(state, root);
  return buildCommitPrompt(state, root);
}

function buildCommitPrompt(state, root) {
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
2) 阅读最近提交完整风格(包含 body): \`git log -10 --pretty=format:'%h %s%n%b%n---END---'\`。先把这些样本看完，再决定 subject 长度、body 是否需要、body 行数与措辞。
3) 如果 test_cmd 不是 \`-\`，运行该测试/检查命令。
4) 判断当前改动是否可以提交。测试失败、明显风险、无法理解 diff、或命令无法运行时，返回 \`blocked\` 或 \`failed\`，不要提交。
5) 生成符合以下规范的 commit_subject 和 commit_body。

提交信息规范:
- commit_subject 使用格式: <type>(<scope>): <中文标题>
- type 保留英文 Conventional Commits 类型，只能使用 feat、fix、refactor、test、docs、style、chore、perf、revert
- scope 使用最小有意义模块名；没有明确模块时可以省略括号，使用 <type>: <中文标题>
- 中文标题必须用中文概括主要改动，不要写英文句子，不要以句号结尾
- commit_body 必须**对齐项目历史 commit 的 body 风格**: 如果历史 body 普遍为空或只有 1-2 行，本次也保持简短甚至直接写 \`-\`；只有当历史普遍写多条 bullet 时才写多条
- commit_body 描述**功能、行为、用户可见效果或问题根因**，**不要列出文件名、函数名、模块名、import 名、类名、变量名或具体代码层面的实现细节**——这些都能从 diff 看到，不需要在 commit 里复述
- commit_body 不写动机长文，不写"由 AI 生成"，不写"使用 X helper / 抽出 Y 函数 / 注册 Z 处理器"这种实现拆解
- commit_body 使用中文，必要技术字面量(命令、配置项、平台名)可保留英文
- 默认 0 到 3 条 bullet，每行以 "- " 开头；多于 3 条通常说明在复述 diff，应当合并

严格约束:
- 不要编辑、格式化、重写或删除任何项目文件。
- 不要执行 git add、git commit、git reset、git checkout、git restore。
- 可以运行只读 git 命令和 test_cmd。
- 如果测试失败，只报告原因和建议修复方向。
- diff_hash 字段必须原样返回: ${state.draft_diff_hash}

最终回复末尾必须**原样**包含这个结构化块: 直接以 \`${RESULT_BEGIN}\` 一整行开头、以 \`${RESULT_END}\` 一整行结尾，每个字段单行。**不要把这段包在 markdown 代码块里(\`\`\`)，不要在 \`<<<\` 后面加引号或 heredoc 标记**(例如不要写成 \`<<<'GIT_FINALIZER_RESULT\` 或 \`<<<"GIT_FINALIZER_RESULT"\`)，否则解析器会失败:
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

function buildMergePrompt(state, root) {
  const ctx = state.merge || {};
  return `你是 git-finalizer 的高速 Claude Code worker。本轮要把分支 \`${state.merge_source}\` 合并进 \`${state.merge_into}\`，你只负责审查将要合并的 commits 与 diff、生成 merge 提交信息。**当前合并尚未执行**，工作树仍处于合并前状态——不要试图修改文件、不要执行 git 写操作。

本轮任务:
- run_id: ${state.run_id}
- repo_root: ${root}
- goal: ${state.goal}
- test_cmd: ${state.test_cmd}
- merge_source: ${state.merge_source} (sha=${ctx.sourceSha || "-"})
- merge_into: ${state.merge_into} (sha=${ctx.intoSha || "-"})
- merge_base: ${ctx.baseSha || "-"}
- ahead/behind(into vs source): ${ctx.aheadBehind || "-"}

将要合并的 commits(按时间正序):
${ctx.log || "(none)"}

将要合并的 diff stat:
${ctx.stat || "(none)"}

将要合并的 name-status:
${ctx.nameStatus || "(none)"}

你需要执行:
1) 阅读上面提供的 commits 列表与 diff stat，理解这次合并到底引入了哪些**功能改动**(不只是文件改了)。
2) 阅读最近 commit body 风格供参考: \`git log -10 --pretty=format:'%h %s%n%b%n---END---'\`(只读)。
3) 如有需要，运行只读 git 命令进一步看具体改动: \`git diff ${ctx.baseSha || "<base>"}...${ctx.sourceSha || "<source>"} -- <path>\`。
4) 如果 test_cmd 不是 \`-\`，运行该命令(可选;通常 merge-finalize 阶段会再次跑;此处可跳过)。
5) 判断 merge 是否可以提交。如果发现 commits 间互相冲突的迹象、目标分支已经偏离 merge base 太远、或 commits 中夹带了不该合入的内容，返回 \`blocked\` 或 \`failed\`。

合并提交信息规范(**重点**):
- commit_subject 使用格式: <type>(<scope>): <中文标题>
- type 保留英文 Conventional Commits 类型(feat / fix / refactor / chore / perf / docs 等)
- scope 使用最小有意义模块名(如 web / server / bot / scheduler)；跨多个 scope 时可省略括号或选最主要的
- 中文标题必须用一句话概括"这次合并实际引入了什么"——**禁止使用 "Merge branch X into Y" 这种空标题**；要让人看了标题就知道改了什么功能
- commit_body 用中文 bullet list 列出本次 merge 引入的**主要功能改动组**(不是文件清单),通常对应 1 条或多条 commit;每条 bullet 描述功能/行为/用户可见效果
- commit_body 不要罗列文件、函数、变量、import、类名等代码层面细节(这些都在 diff 里)
- commit_body 不要写"合并某分支""进行了一次合并"这种废话
- bullet 每行以 "- " 开头；通常 1-5 条；如果 commits 数量多但都属于同一主题可合并为更少的 bullet
- 标题与正文都使用中文；命令、配置项、平台名等必要技术字面量可保留英文

严格约束:
- 不要编辑、格式化、重写或删除任何项目文件。
- 不要执行 git add、git commit、git merge、git reset、git checkout、git restore、git stash 等任何写操作。
- 当前 worktree 仍在 merge_into 分支上、未执行合并；不要试图"先合并再总结"。
- 你看到的 commits 与 diff 已经在本 prompt 中提供，可以直接根据它们写提交信息。

最终回复末尾必须**原样**包含这个结构化块: 直接以 \`${RESULT_BEGIN}\` 一整行开头、以 \`${RESULT_END}\` 一整行结尾。**不要把这段包在 markdown 代码块里**，**不要在 \`<<<\` 后加引号或 heredoc 标记**:
${RESULT_BEGIN}
status: done|blocked|failed
summary: 一句话总结这次合并引入的内容
tests: 测试命令与结果(没跑则写 skipped)
changed_files: 主要文件;用分号分隔(可只列 top 5)
risk_notes: 风险或 none
commit_subject: <type>(<scope>): <中文标题>
commit_body: 中文 bullet list,描述合并引入的功能改动
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
  if (fs.existsSync(paths.settings)) args.push("--settings", paths.settings);
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
  const settings = { env: claudeEnv(profile, true) };
  if (useApiKeyHelper && profile.api_key) settings.apiKeyHelper = paths.keyHelper;
  fs.writeFileSync(paths.settings, `${JSON.stringify(settings, null, 2)}\n`, { mode: 0o600 });
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
  const beginRe = /<<<\s*['"`]?\s*GIT_FINALIZER_RESULT\s*['"`]?\s*/g;
  let lastMatch = null;
  for (const m of text.matchAll(beginRe)) {
    lastMatch = m;
  }
  if (!lastMatch) return {};
  const bodyStart = lastMatch.index + lastMatch[0].length;
  const end = text.indexOf(RESULT_END, bodyStart);
  if (end < 0) return {};
  const out = {};
  for (const line of text.slice(bodyStart, end).trim().split(/\r?\n/)) {
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
    kind: "commit",
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

function cmdMergeDraft(args) {
  ensureDirs();
  const root = repoRoot();
  ensureNoGitOperation(root);
  if (!args.source) throw new CmdError("missing required argument: --source");
  if (!branchExists(root, args.source)) throw new CmdError(`source ref not found: ${args.source}`);
  const into = args.into || currentBranch(root);
  if (!branchExists(root, into)) throw new CmdError(`target ref not found: ${into}`);
  if (into === args.source) throw new CmdError(`source and target are the same: ${into}`);
  const onTarget = currentBranch(root) === into;
  if (!onTarget && !args.into) {
    throw new CmdError(`current branch is not ${into}; pass --into <branch> explicitly`);
  }
  const ctx = gatherMergeContext(root, args.source, into);
  if (ctx.sourceSha === ctx.intoSha) throw new CmdError(`nothing to merge: ${args.source} == ${into}`);
  if (ctx.sourceSha === ctx.baseSha) throw new CmdError(`already merged: ${args.source} is reachable from ${into}`);
  const runId = args.run || runIdNow();
  if (fs.existsSync(statePath(runId))) throw new CmdError(`run state already exists: ${runId}`);
  const goal = args.goal || `merge ${args.source} into ${into}`;
  const state = {
    run_id: runId,
    kind: "merge",
    goal,
    repo_root: root,
    repo_key: projectKey(root),
    branch: into,
    merge_source: args.source,
    merge_into: into,
    merge: ctx,
    auto_stash: args.autoStash === true,
    test_cmd: args.testCmd ?? "-",
    draft_diff_hash: `merge:${ctx.intoSha}..${ctx.sourceSha}`,
    status: "planned",
    created_at: isoNow(),
    recent_commits: recentCommits(root),
    events: [],
  };
  appendEvent(state, "merge_draft", {
    source: state.merge_source,
    into: state.merge_into,
    base: ctx.baseSha,
    auto_stash: state.auto_stash,
  });
  saveState(state);
  console.log(`run_id=${runId}`);
  console.log(`kind=merge`);
  console.log(`merge_source=${state.merge_source} (${ctx.sourceSha.slice(0, 12)})`);
  console.log(`merge_into=${state.merge_into} (${ctx.intoSha.slice(0, 12)})`);
  console.log(`merge_base=${ctx.baseSha.slice(0, 12)}`);
  console.log(`ahead/behind=${ctx.aheadBehind || "-"}`);
  console.log(`auto_stash=${state.auto_stash}`);
  console.log(`state_file=${statePath(runId)}`);
}

async function cmdRun(args) {
  ensureDirs();
  const state = loadState(args.run);
  const root = path.resolve(state.repo_root);
  refreshStatus(state);
  if (state.status === "running" || TERMINAL_STATUSES.has(state.status)) {
    console.log(`run_id=${args.run}`);
    console.log(`status=${state.status}`);
    if (state.tmux_session) console.log(`tmux_session=${state.tmux_session}`);
    if (args.wait && state.status === "running") {
      await cmdWait({ run: args.run, json: args.json, timeoutSec: args.timeoutSec });
    }
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
  if (args.wait) {
    await cmdWait({ run: args.run, json: args.json, timeoutSec: args.timeoutSec });
  }
}

function tmuxSessionAlive(name) {
  if (!name) return false;
  return sh(["tmux", "has-session", "-t", name], { check: false }).status === 0;
}

function waitForWorkerDone(state, opts = {}) {
  const paths = workerPaths(state.run_id);
  const timeoutMs = Number.isFinite(opts.timeoutSec) && opts.timeoutSec > 0 ? opts.timeoutSec * 1000 : 0;
  if (fs.existsSync(paths.done)) return Promise.resolve("done_file");
  fs.mkdirSync(path.dirname(paths.done), { recursive: true });
  return new Promise((resolve) => {
    let settled = false;
    let watcher = null;
    let poll = null;
    let timer = null;
    const finish = (reason) => {
      if (settled) return;
      settled = true;
      try { watcher?.close(); } catch {}
      if (poll) clearInterval(poll);
      if (timer) clearTimeout(timer);
      resolve(reason);
    };
    try {
      watcher = fs.watch(path.dirname(paths.done), () => {
        if (fs.existsSync(paths.done)) finish("done_file");
      });
    } catch {}
    poll = setInterval(() => {
      if (fs.existsSync(paths.done)) {
        finish("done_file");
        return;
      }
      if (state.tmux_session && !tmuxSessionAlive(state.tmux_session)) {
        if (fs.existsSync(paths.done)) finish("done_file");
        else finish("tmux_gone");
      }
    }, 500);
    if (timeoutMs) timer = setTimeout(() => finish("timeout"), timeoutMs);
    if (fs.existsSync(paths.done)) finish("done_file");
  });
}

async function cmdWait(args) {
  const state = loadState(args.run);
  refreshStatus(state);
  let waitReason;
  if (TERMINAL_STATUSES.has(state.status)) {
    waitReason = "already_terminal";
  } else {
    waitReason = await waitForWorkerDone(state, { timeoutSec: args.timeoutSec });
  }
  const fresh = loadState(args.run);
  refreshStatus(fresh);
  if (args.json) {
    console.log(JSON.stringify({ ...fresh, wait_reason: waitReason }, null, 2));
  } else {
    console.log(`run_id=${args.run}`);
    console.log(`status=${fresh.status}`);
    console.log(`wait_reason=${waitReason}`);
    console.log(`tmux_session=${fresh.tmux_session || "-"}`);
    console.log(`result_file=${fresh.result_file || "-"}`);
  }
  if (waitReason === "timeout") process.exitCode = 2;
  else if (waitReason === "tmux_gone" && !TERMINAL_STATUSES.has(fresh.status)) process.exitCode = 3;
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
  if (state.kind === "merge") {
    cmdMergeFinalize(args);
    return;
  }
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

function cmdMergeFinalize(args) {
  const state = loadState(args.run);
  refreshStatus(state);
  if (state.kind !== "merge") throw new CmdError(`run ${args.run} is not a merge run`);
  const root = path.resolve(state.repo_root);
  ensureNoGitOperation(root);
  const branch = currentBranch(root);
  if (branch !== state.merge_into) {
    throw new CmdError(`current branch is ${branch}, expected ${state.merge_into}; checkout the target branch first`);
  }

  const paths = workerPaths(args.run);
  const result = fs.existsSync(paths.result) ? readJson(paths.result) : {};
  const parsed = state.parsed_result || result.parsed || {};
  if (parsed.status !== "done" || state.status !== "done") {
    throw new CmdError(`worker did not pass validation: state=${state.status} result=${parsed.status}`);
  }

  const reviewedSourceSha = state.merge?.sourceSha;
  const reviewedIntoSha = state.merge?.intoSha;
  const currentSourceSha = revParse(root, state.merge_source);
  const currentIntoSha = revParse(root, state.merge_into);
  if (currentSourceSha !== reviewedSourceSha) {
    throw new CmdError(`source moved after review: reviewed=${reviewedSourceSha} current=${currentSourceSha}`);
  }
  if (currentIntoSha !== reviewedIntoSha) {
    throw new CmdError(`target moved after review: reviewed=${reviewedIntoSha} current=${currentIntoSha}`);
  }

  const message = cleanCommitMessage(parsed.commit_subject, parsed.commit_body);
  const msgFile = path.join(STATE_DIR, args.run, "commit-message.txt");
  fs.mkdirSync(path.dirname(msgFile), { recursive: true });
  fs.writeFileSync(msgFile, `${message}\n`);

  let stashRef = "";
  if (state.auto_stash && hasDiff(root)) {
    const stashLabel = `git-finalizer auto-stash ${state.run_id}`;
    const beforeSha = topStashSha(root);
    const stashOut = sh(["git", "stash", "push", "-m", stashLabel], { cwd: root, check: false });
    if (stashOut.status !== 0) {
      throw new CmdError(`auto-stash failed: ${stashOut.stderr || stashOut.stdout}`);
    }
    const afterSha = topStashSha(root);
    if (afterSha === beforeSha || !afterSha) {
      // git stash push reported success but did not create a new stash entry.
      // This happens when the only "diff" is something git stash cannot capture
      // (e.g. submodule/worktree gitlink modifications). The working tree is now
      // effectively the same as before push, so there is no stash for us to pop.
      appendEvent(state, "auto_stash_noop", { label: stashLabel, before: beforeSha, after: afterSha });
      saveState(state);
    } else {
      stashRef = stashLabel;
      appendEvent(state, "auto_stash", { label: stashLabel, sha: afterSha });
      saveState(state);
    }
  } else if (!state.auto_stash && hasDiff(root)) {
    throw new CmdError(
      `target branch has uncommitted changes; either commit/stash them first, or re-run merge-draft with --auto-stash`,
    );
  }

  const merge = sh(
    ["git", "merge", "--no-ff", "--no-commit", state.merge_source],
    { cwd: root, check: false },
  );
  if (merge.status !== 0) {
    sh(["git", "merge", "--abort"], { cwd: root, check: false });
    if (stashRef) {
      safeStashPop(root, stashRef);
    }
    throw new CmdError(
      `git merge failed (likely conflicts); aborted and ${stashRef ? "restored stash" : "no stash to restore"}.\n${merge.stderr || merge.stdout}`,
    );
  }

  try {
    sh(["git", "commit", "-F", msgFile], { cwd: root });
  } catch (error) {
    if (stashRef) {
      safeStashPop(root, stashRef);
    }
    throw error;
  }

  state.commit_hash = sh(["git", "rev-parse", "--short", "HEAD"], { cwd: root }).stdout.trim();
  state.commit_message_file = msgFile;
  state.status = "committed";
  appendEvent(state, "merge_commit", { commit_hash: state.commit_hash });
  saveState(state);

  console.log("status=committed");
  console.log(`commit_hash=${state.commit_hash}`);

  if (stashRef) {
    const popResult = safeStashPop(root, stashRef);
    if (popResult.skipped) {
      appendEvent(state, "stash_pop_skipped", { reason: popResult.reason });
      saveState(state);
      console.log(`stash_pop=skipped (${popResult.reason})`);
    } else if (popResult.conflict) {
      appendEvent(state, "stash_pop_failed", { stderr: popResult.stderr });
      saveState(state);
      console.log(`stash_pop=conflict (left in stash list as ${stashRef})`);
      console.log(`stash_pop_stderr=${(popResult.stderr || "").trim().split(/\r?\n/)[0] || "-"}`);
    } else {
      appendEvent(state, "stash_pop", {});
      saveState(state);
      console.log("stash_pop=ok");
    }
  }
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
    } else if (item === "--source") {
      args.source = rest[++i];
    } else if (item === "--into") {
      args.into = rest[++i];
    } else if (item === "--auto-stash") {
      args.autoStash = true;
    } else if (item === "--timeout") {
      const value = Number(rest[++i]);
      if (!Number.isFinite(value) || value < 0) throw new CmdError("--timeout must be a non-negative number of seconds");
      args.timeoutSec = value;
    } else if (item === "--wait") {
      args.wait = true;
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
  merge-draft --source <branch> [--into <branch>] [--goal <summary>] [--test-cmd <command>] [--auto-stash] [--run <run_id>]
  run --run <run_id> [--wait] [--timeout <seconds>] [--json]
  wait --run <run_id> [--timeout <seconds>] [--json]
  status --run <run_id> [--json]
  inspect --run <run_id>
  finalize --run <run_id>          # routes to merge-finalize automatically when kind=merge
  merge-finalize --run <run_id>
  close --run <run_id>`);
}

function requireArg(args, key) {
  if (!args[key]) throw new CmdError(`missing required argument: --${key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`)}`);
}

async function main() {
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
    case "merge-draft":
      requireArg(args, "source");
      cmdMergeDraft(args);
      break;
    case "run":
      requireArg(args, "run");
      await cmdRun(args);
      break;
    case "wait":
      requireArg(args, "run");
      await cmdWait(args);
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
    case "merge-finalize":
      requireArg(args, "run");
      cmdMergeFinalize(args);
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

main().catch((error) => {
  if (error instanceof CmdError) {
    console.error(`error: ${error.message}`);
    process.exit(1);
  }
  console.error(error);
  process.exit(1);
});
