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
- `run --run <run_id>`
- `status --run <run_id> [--json]`
- `inspect --run <run_id>`
- `finalize --run <run_id>`
- `close --run <run_id>`

## 工作流程

1. 在目标 git 仓库中运行 `doctor`，检查依赖、模型配置和 Claude Code 能力。
2. 运行 `draft --goal "<修改摘要>" --test-cmd "<测试命令>"`，创建一次收尾任务。
3. 运行 `run --run <run_id>`，启动本轮临时 tmux 会话。
4. tmux 会话中会用所选高速模型启动 Claude Code。
5. 如果该项目已有绑定的 Claude Code session，则恢复该 session；否则首次运行会创建 session 并保存 `session_id`。
6. 运行 `status` 或 `inspect` 查看 worker 结果。
7. 需要提交时显式运行 `finalize`；只有 worker 校验通过且当前 diff 未变化时，才会 stage 并 commit。
8. 如果临时 tmux 会话仍然存在，可以运行 `close` 关闭；这不会清理 Claude Code session 上下文。

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

默认 `use_api_key_helper = true`。runner 会在 `.state/<run_id>/` 下写入本轮专用的 `api-key-helper.sh`，并通过 Claude Code `--settings` 传入。这个方式会同时发送 `X-Api-Key` 和 `Authorization: Bearer`，更适合兼容 Claude Code 的鉴权路径。

如果设置为 `false`，则回退为直接注入 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` 环境变量。

默认 `auto_finalize_after_run = false`。`run` 只负责启动 worker、运行测试/检查并产出结构化结果；提交必须显式调用 `finalize --run <run_id>`。只有明确设置为 `true` 时，worker 返回 `status: done` 后才会自动进入 finalize。

## Worker 规则

Claude Code worker 必须：

- 读取 `git status`、`git diff`、staged diff、untracked files 和最近 commit 风格。
- 运行配置的测试命令，除非测试命令是 `-`。
- 默认不修改源码文件。
- 如果测试失败或发现风险，只报告问题，不自动修复。
- 输出要求的结构化结果块。

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
- body 必须是 bullet list，每行描述一个具体修改点，每行都必须以 `- ` 开头。
- body 内容必须使用中文；代码标识符、文件名、命令、模型名和其他字面技术 token 可以保留英文。
- body 不要写英文散文。
- 只有非常小、body 没有额外价值的修改，才使用 `commit_body: -`。
- 除非修改内容本身涉及 Codex、Claude、AI 或生成标记，否则不要在提交信息里提到它们。

## 提交安全检查

`finalize` 会在以下情况拒绝提交：

- 没有 diff。
- 测试失败，或 worker 没有返回 `status: done`。
- 仓库处于 detached HEAD。
- 正在 merge、rebase 或 cherry-pick。
- 当前 diff hash 和 worker 审查过的 diff hash 不一致。
- worker 返回 `blocked` 或 `failed`。

默认情况下，`finalize` 会使用 `git add -A` 暂存 tracked 改动和 untracked 文件；ignored 文件不会被包含。
