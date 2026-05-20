---
name: git-finalizer
description: 当强模型或主 agent 已经完成代码实现，需要用高速 Claude Code 模型完成测试、审查 git diff、生成提交信息并在校验通过后提交时使用。适用于 Codex、Claude Code 或其他上游编码 agent。每次运行使用临时 tmux 会话，同时按 git 项目复用固定 Claude Code session。
---

# Git Finalizer

## 目标

这个 Skill 用在代码已经由强模型或其他 agent 写完之后。收尾阶段交给高速 Claude Code worker，负责低成本、高频的验证和提交工作：

- 检查当前 git diff。
- 运行测试或检查命令。
- 总结修改内容和风险。
- 生成中文提交信息。
- 只在校验通过后提交。

这个 Skill 不绑定 Codex。上游写代码的工具可以是 Codex、Claude Code，也可以是其他 agent。

## 命令入口

```bash
bash skills/git-finalizer/scripts/git-finalizer.sh <command> [args...]
```

其中 `skills/` 可以替换成实际安装位置，例如 `.codex/skills/` 或 `.claude/skills/`。
这个包装脚本会运行 `node src/git-finalizer.mjs`，不需要 Python 环境，也不需要执行 `npm install`。

## 命令

- `doctor`
- `draft --goal "<摘要>" [--test-cmd "<命令>"] [--run <run_id>]`
- `merge-draft --source <分支> [--into <分支>] [--goal <摘要>] [--test-cmd <命令>] [--auto-stash] [--run <run_id>]`
- `run --run <run_id> [--wait] [--timeout <秒>] [--json]`
- `wait --run <run_id> [--timeout <秒>] [--json]`
- `status --run <run_id> [--json]`
- `inspect --run <run_id>`
- `finalize --run <run_id>` （kind=merge 时自动路由到 merge-finalize）
- `merge-finalize --run <run_id>`
- `close --run <run_id>`

## 工作流程

1. 在目标 git 仓库中运行 `doctor`，检查依赖、模型配置和 Claude Code 能力。
2. 运行 `draft --goal "<修改摘要>" --test-cmd "<测试命令>"`，创建一次收尾任务。
3. 运行 `run --run <run_id>`，启动本轮临时 tmux 会话；想顺手等结束可以加 `--wait` 一次性阻塞到 worker 完成。
4. tmux 会话中会用所选高速模型启动 Claude Code。
5. 如果该项目已有绑定的 Claude Code session，则恢复该 session；否则首次运行会创建 session 并保存 `session_id`。
6. 不想 sleep 轮询 `status` 时，运行 `wait --run <run_id>`，命令会用 `fs.watch`（macOS FSEvents、Linux inotify）+ 500ms 文件轮询兜底，准时返回；可加 `--timeout <秒>` 限制最大等待时间。
7. 运行 `status` 或 `inspect` 查看 worker 结果。
8. 需要提交时显式运行 `finalize`；只有 worker 校验通过且当前 diff 未变化时，才会 stage 并 commit。
9. 如果临时 tmux 会话仍然存在，可以运行 `close` 关闭；这不会清理 Claude Code session 上下文。

## merge 流程（合并分支并生成有内容的提交信息）

普通 `draft` 处理"工作树已经改完、生成普通 commit"的场景；如果要把另一个分支合并进当前分支，并希望 merge commit 的提交信息能描述具体改了什么（而不是 "Merge branch X into Y"），用 `merge-draft` + `merge-finalize`：

1. 切换到目标分支（被合并入的那个，例如 `git checkout main`）。
2. `merge-draft --source <要合并进来的分支> [--into <目标分支>] [--auto-stash]`
   - 默认 `--into` 是当前分支。
   - 自动收集 `git log <merge_base>..<source>`、`git diff --stat`、`git diff --name-status`、ahead/behind 计数，作为 worker 的素材。
   - 如果当前工作树有未提交改动，需要加 `--auto-stash`，否则 `merge-finalize` 会拒绝合并。
   - 不会修改工作树。
3. `run --run <run_id>` 让 worker 阅读这些素材，生成符合规范的 merge 提交信息（标题概括"这次合并实际引入了什么"，正文 bullet 主要功能改动；**禁止 "Merge branch X into Y" 类空标题**）。
4. `merge-finalize --run <run_id>`（或 `finalize --run <run_id>`，kind=merge 时自动路由）会：
   - 校验源/目标分支 SHA 自 draft 后没变。
   - 如果 `--auto-stash` 开启且工作树脏 → `git stash push`。
   - `git merge --no-ff --no-commit <source>`。
   - 冲突时自动 `git merge --abort`，并在 stash 存在时 `git stash pop` 恢复，然后报错由人工处理。
   - 干净合并 → `git commit -F <message_file>`，使用 worker 生成的提交信息。
   - 提交完成后 `git stash pop`；pop 如有冲突，stash 留在列表中由人工处理（不回滚已完成的 merge commit）。

## wait 子命令

`wait` 用于把"是否结束"的判断交给 skill 自己，调用方只需要发起一次阻塞调用，不再需要外层 `sleep` + `status` 轮询：

- 优先级 1：`worker.done` 哨兵文件已存在 → 立即返回（约 60ms）。
- 优先级 2：`fs.watch` 监听 `worker.done` 所在目录，文件创建即唤醒（macOS / Linux 原生事件）。
- 优先级 3：每 500ms `stat` 一次 done 文件，并检查 tmux 会话是否仍存活（兼容 NFS、Docker volume 等不支持 inotify 的文件系统）。

输出字段：

- `status` — 最终运行状态。
- `wait_reason` — `already_terminal`（调用前就已结束）、`done_file`（哨兵被创建）、`tmux_gone`（会话异常退出且未写 done）、`timeout`（超时）。

退出码：

- `0` — 正常等到终态（含 `done`、`blocked`、`failed`、`committed`）。
- `2` — 等到 `--timeout` 超时。
- `3` — `tmux_gone` 且状态未推进到终态（worker 异常崩溃）。

适用模式：

- 同步前台：`git-finalizer run --run X --wait` 单条命令完成"启动 + 等结果"。
- 事件驱动：用支持后台任务的 harness 启动 `wait`（例如 Claude Code 的 `run_in_background`），直接收完成通知，无需自行编排 sleep。

## Claude Session 复用

- tmux 会话是每次运行临时创建的。
- 默认 `persist_claude_session = true`，同一 git 项目会复用同一个 Claude Code 对话。
- 第一轮不传固定 session id，让 Claude Code 自己创建真实 session，并保存返回的 `session_id`。
- 后续运行使用 `--resume <session_id>` 回到同一个对话；不要用 repo path hash 生成固定 `--session-id`。
- 如果已保存 session 不存在、损坏或无法恢复，会清空该项目 session 并自动重试一次新对话，成功后保存新的真实 `session_id`。
- 同一个 Claude Code session 不能并发占用；如果前一轮还在运行，下一轮需要等待或关闭临时 tmux 会话后再启动。
- 如果设置 `persist_claude_session = false`，则改用 `--no-session-persistence`。
- 如果本机 Claude CLI 需要特殊启动方式，可以设置 `GIT_FINALIZER_CLAUDE_CMD` 作为命令模板。

## 模型配置

运行配置文件是 `config.toml`，可以从 `config.example.toml` 复制得到。

每个 profile 支持：

- `base_url`
- `api_key`
- `model`

所选 profile 会注入：

- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_DEFAULT_HAIKU_MODEL`
- `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_DEFAULT_OPUS_MODEL`

runner 会在 `.state/<run_id>/` 下写入本轮专用的 `claude-settings.json`，并通过 Claude Code `--settings <file>` 传入。该 settings 文件会显式包含当前 profile 的 `env`（`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 等），避免被用户全局 `~/.claude/settings.json` 里的默认模型或 API 配置覆盖。

默认 `use_api_key_helper = true`。runner 还会写入本轮专用的 `api-key-helper.sh` 并在 settings 中声明 `apiKeyHelper`，以兼容 Claude Code 的动态 key 读取路径。

如果设置为 `false`，则回退为直接注入 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` 环境变量。

默认 `auto_finalize_after_run = false`。`run` 只负责启动 worker、运行测试/检查并产出结构化结果；提交必须显式调用 `finalize --run <run_id>`。只有明确设置为 `true` 时，worker 返回 `status: done` 后才会自动进入 finalize。

## Worker 规则

Claude Code worker 必须：

- 读取 `git status`、`git diff`、staged diff、untracked files 和最近 commit 风格。
- 运行配置的测试命令，除非测试命令是 `-`。
- 默认不修改源码文件。
- 如果测试失败或发现风险，只报告问题，不自动修复。
- 输出要求的结构化结果块。

merge 模式下，worker 的素材在 prompt 中已经预填好（被合并分支的 commits、diff stat、name-status），worker **不要**自己执行 merge 或修改工作树；提交信息标题必须概括"实际引入了什么"，**禁止 "Merge branch X into Y" 这种空标题**，正文按主要功能改动分组用 bullet 描述。

必须输出的结果块：

```text
<<<GIT_FINALIZER_RESULT
status: done|blocked|failed
summary: 一句话总结
tests: 测试命令和结果
changed_files: 用英文分号分隔的文件列表
risk_notes: 风险说明；没有风险则写 none
commit_subject: <type>(<scope>): <中文标题>
commit_body: 中文 bullet list，每行一个修改点；无需 body 时写 -
diff_hash: prompt 中提供的 hash
>>>
```

## 提交信息规则

- 提交标题摘要和 body 必须使用中文。
- Conventional Commits 的 type 保持英文：`feat`、`fix`、`refactor`、`test`、`docs`、`style`、`chore`、`perf` 或 `revert`。
- 标题格式：`<type>(<scope>): <中文标题>`。
- scope 使用最小且有意义的模块名。
- body 必须**对齐项目历史 commit 的 body 风格**：先看 `git log -10 --pretty=format:'%h %s%n%b%n---END---'`，如果历史 body 普遍为空或只有 1-2 行，本次也保持简短甚至直接写 `commit_body: -`。
- body 描述**功能、行为、用户可见效果或问题根因**，**不要列出文件名、函数名、模块名、import 名、类名、变量名或代码层面的实现细节**——这些 diff 里都有，不需要在 commit 里复述。
- body 不要写"使用 X helper / 抽出 Y 函数 / 注册 Z 处理器"这种实现拆解，也不要写英文散文。
- body 内容必须使用中文；命令、配置项、平台/服务名等必要技术字面量可以保留英文。
- 默认 0 到 3 条 bullet，每行以 `- ` 开头。多于 3 条通常说明在复述 diff，应当合并。
- 除非修改内容本身涉及 Codex、Claude、AI 或生成标记，否则不要在提交信息里提到它们。
- 结构化结果块必须**原样**输出 `<<<GIT_FINALIZER_RESULT` 与 `>>>` 分隔符——不要包在 markdown 代码块里，不要在 `<<<` 后加引号或 heredoc 标记（如 `<<<'GIT_FINALIZER_RESULT`），否则解析器无法识别。

## 提交安全检查

`finalize` 会在以下情况拒绝提交：

- 没有 diff。
- 测试失败，或 worker 没有返回 `status: done`。
- 仓库处于 detached HEAD。
- 正在 merge、rebase 或 cherry-pick。
- 当前 diff hash 和 worker 审查过的 diff hash 不一致。
- worker 返回 `blocked` 或 `failed`。

默认情况下，`finalize` 会使用 `git add -A` 暂存 tracked 改动和 untracked 文件；ignored 文件不会被包含。

`merge-finalize` 在普通安全检查之外，还会在以下情况拒绝合并：

- 当前不在 `--into` 指定的目标分支上。
- 源分支或目标分支 SHA 自 `merge-draft` 以来发生过变化（避免基于过期素材合并）。
- 目标分支有未提交改动且没有传 `--auto-stash`。
- `git merge --no-ff` 出现冲突 — 自动 `git merge --abort`，并在 `--auto-stash` 开启时尝试 `git stash pop` 还原，然后报错由人工处理。

`--auto-stash` 的安全机制：
- `git stash push` 完成后，对比 `refs/stash` 的 SHA 是否真的产生了新条目；如果只有 git stash 无法捕获的改动（例如 submodule / worktree gitlink 修改），实际不会创建 stash，跳过 pop，避免误 pop 用户已有的 stash entry。
- 真正 pop 时再次校验 `stash@{0}` 的 message 是否包含本轮的 `git-finalizer auto-stash <run_id>` 标签；不匹配（说明用户在 merge 期间并发 push 了别的 stash）则跳过 pop 并报告 `stash_pop=skipped`。
- pop 冲突时不消耗 stash entry（git 默认行为），日志输出 `stash_pop=conflict`，stash 留在列表中由人工处理。

`merge-finalize` 提交完成后会尝试 `git stash pop`；pop 时如发生冲突，stash 会留在列表中由人工处理（不回滚已完成的 merge commit）。
