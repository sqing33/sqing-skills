---
name: tmux-claude-openspec-worker
description: 用主控代理通过 OpenSpec 管理实现规范，再把代码实现任务交给一个 tmux 里的 Claude Code worker 执行。父代理负责 OpenSpec，子 Claude 按 OpenSpec 写代码。Use when users want a lead agent to use OpenSpec as the contract, then launch one Claude Code worker in tmux for implementation.
---

# Tmux Claude OpenSpec Worker

## 目标

- 父代理先用 OpenSpec 管理和确认变更规范。
- 再把代码实现任务交给一个 Claude Code worker 执行。
- 子 Claude 以 OpenSpec 产出物为实现契约。
- 固定单 worker，固定直接执行，不做模式切换、不做计划审查。
- 保留 worker 分支与 worktree；默认仅关闭 tmux session。

## 命令入口

```bash
bash skills/tmux-claude-openspec-worker/scripts/tmux-orch.sh <command> [args...]
```

其中 `skills/` 可对应你的实际目录，例如 `.codex/skills/` 或 `.claude/skills/`。
入口脚本会运行 `node src/tmux-orch.mjs`，不需要 Python 环境。

## Commands

- `doctor`
- `draft --goal "<需求>" [--workers N] [--run <run_id>]`
- `run --run <run_id>`
- `status --run <run_id> [--json]`
- `inspect --run <run_id>`
- `close --run <run_id>`

## 核心流程

1. 父代理先准备好 OpenSpec change 与产出物。
2. 执行 `draft` 写入任务和 run_id。
3. 执行 `run`；父代理先做 OpenSpec validate，并读取活动 change 的 `status/instructions apply`。
4. 创建 tmux session 并启动 Claude worker，子 Claude 按 OpenSpec 上下文实现代码。
5. 执行 `status` 查看运行状态。
6. 执行 `inspect` 查看结果和日志。
7. 需要时执行 `close` 手动关闭 session。

## Claude Code 支持

- 适合“父代理调一个 Claude Code worker”这种最简单的执行场景。
- 父层不负责写实现代码，但负责 OpenSpec 相关工作：选择 change、验证规范、把 OpenSpec 上下文传给子 Claude。
- skill 固定单 worker 执行。
- worker 默认调用本机 `claude` CLI。
- 当前默认命令模板会使用 `claude --bare --no-session-persistence -p --dangerously-skip-permissions --permission-mode bypassPermissions ...`，避免把当前登录态、插件、项目级 skill 扫描和会话持久化混入 worker 运行时。
- worker 实际配置文件为 `skills/tmux-claude-openspec-worker/config.toml`。
- 示例模板文件为 `skills/tmux-claude-openspec-worker/config.example.toml`。
- 仓库需要先完成 OpenSpec 初始化。若未初始化，可运行 `npx -y @studyzy/openspec-cn init --tools codex,claude`。
- `run` 前父代理会执行 `validate --all --strict --no-interactive`，并自动选择活动 change。
- 若存在多个活动 change，可通过环境变量 `TMUX_ORCH_OPENSPEC_CHANGE=<change-name>` 指定本次实现目标。
- skill 已移除 `max_parallel_workers` 配置，执行模型固定为单线程单 worker。
- TOML 里固定保留 3 组模型配置：`minimax`、`glm_5_1`、`kimi_k2_5`。
- 每组配置只包含 `base_url`、`api_key`、`model`。
- `api_key` 会同时注入 `ANTHROPIC_API_KEY` 和 `ANTHROPIC_AUTH_TOKEN`，兼容不同 Claude 代理网关的鉴权习惯。
- 运行时不再通过命令行切换；子 worker 永远使用 `config.toml` 里的 `selected_model`。
- 这个 skill 只能控制它新启动的子 Claude worker 模型，不能在当前会话内部改掉“父 Claude”正在使用的模型。
- 如本机 `claude` 的实际命令格式和默认假设不同，可通过环境变量 `TMUX_ORCH_CLAUDE_CMD` 覆盖底层启动命令。
- 覆盖命令会在 worker 脚本内执行，可使用 `$PROMPT_TEXT`、`$MSG_FILE`、`$LOG_FILE`、`$DEBUG_FILE` 这些变量。
- `run` 会把 OpenSpec 的 `status --json` 与 `instructions apply --json` 结果写入子 Claude prompt，作为代码实现规范。
- worker 失败时，可以查看 `.logs/<run_id>/` 下的日志和 debug 文件，排查代理、鉴权或模型兼容性问题。

## 硬约束

- 不删除 worker 分支。
- 不删除 worker worktree。
- 默认并发上限为 `1`。
- 默认同时运行上限为 `1`；skill 会强制单线程执行，不再并行启动多个 worker。

## 产物位置

- 计划表：`ORCH_PLAN.md`
- 运行状态：`skills/tmux-claude-openspec-worker/.state/<run_id>.json`
- worker 日志：`skills/tmux-claude-openspec-worker/.logs/<run_id>/`
- worker debug 日志：`skills/tmux-claude-openspec-worker/.logs/<run_id>/*.debug.log`
- worker 最终回复：`skills/tmux-claude-openspec-worker/.results/<run_id>/`
