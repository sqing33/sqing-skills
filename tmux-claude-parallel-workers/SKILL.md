---
name: tmux-claude-parallel-workers
description: 用主控代理拆解和规划编程任务，再通过 tmux 并行调起多个 Claude Code worker 到独立 branch/worktree 中执行。主控代理可以是 Codex，也可以是 Claude Code；主控代理只负责任务拆分、执行顺序和验收标准规划，实际实现全部交给子 Claude Code。Use when users want a lead agent such as Codex or Claude Code to plan and split work, while all implementation is delegated to Claude Code child workers.
---

# Tmux Claude Parallel Workers

## 目标

- 用主控代理自动完成：任务拆解与规划 -> 计划审查 -> tmux 并行执行 -> 子 Claude 整合 -> 查看结果。
- 主控代理只负责拆分任务、安排执行顺序、明确约束和验收标准，不直接承担实现。
- 默认先产出 `ORCH_PLAN.md`；命中“直接执行”关键词时可跳过审查。
- 保留所有 worker 分支与 worktree；默认仅关闭 tmux session。
- 实际实现全部由子 Claude Code worker 完成。

## 命令入口

```bash
bash skills/tmux-claude-parallel-workers/scripts/tmux-orch.sh <command> [args...]
```

其中 `skills/` 可对应你的实际目录，例如 `.codex/skills/` 或 `.claude/skills/`。
入口脚本会运行 `node src/tmux-orch.mjs`，不需要 Python 环境。

## Commands

- `doctor`
- `draft --goal "<需求>" [--workers N] [--run <run_id>]`
- `run --run <run_id>`
- `status --run <run_id> [--json]`
- `inspect --run <run_id>`
- `integrate --run <run_id>`
- `close --run <run_id>`

## 核心流程

1. 执行 `draft` 生成 `ORCH_PLAN.md`。
2. 若用户未给出“直接执行”意图，先让用户审查计划表。
3. 用户确认后执行 `run`，创建 tmux session 并启动 Claude worker。
4. 每个 worker 应只处理自己 ownership 范围内的文件或模块。
5. 主控代理不亲自改代码；实现、验证反馈和结果总结都由子 Claude worker 产出。
6. 如需合并多 worker 结果，执行 `integrate`，由一个子 Claude 负责整合，不让父代理手动合并。
7. 执行 `status` 追踪进度；所有 worker 完成后 session 会自动关闭。
8. 执行 `inspect` 查看各 worker 的详细输出。
9. 需要时执行 `close` 手动关闭 session。

## Claude Code 支持

- 适合“父 Codex 调子 Claude Code”或“父 Claude Code 调子 Claude Code”的 tmux 并行分工场景。
- 父层无论是 Codex 还是 Claude Code，都只负责规划，不负责实际实现。
- 多 worker 并行时，建议在 goal 中显式写出 ownership，例如：`app/api/** :: 实现上传接口`。
- worker 默认调用本机 `claude` CLI。
- 当前默认命令模板基于官方 CLI 能力核对后设置为非交互 `claude -p --dangerously-skip-permissions ...`。
- worker 实际配置文件为 `skills/tmux-claude-parallel-workers/config.toml`。
- 示例模板文件为 `skills/tmux-claude-parallel-workers/config.example.toml`。
- TOML 里固定保留 3 组模型配置：`minimax`、`glm_5_1`、`kimi_k2_5`。
- 每组配置只包含 `base_url`、`api_key`、`model`。
- 运行时不再通过命令行切换；子 worker 永远使用 `config.toml` 里的 `selected_model`。
- 这个 skill 只能控制它新启动的子 Claude worker 模型，不能在当前会话内部改掉“父 Claude”正在使用的模型。
- 如本机 `claude` 的实际命令格式和默认假设不同，可通过环境变量 `TMUX_ORCH_CLAUDE_CMD` 覆盖底层启动命令。
- 覆盖命令会在 worker 脚本内执行，可使用 `$PROMPT_TEXT`、`$MSG_FILE`、`$LOG_FILE` 这些变量。

## 直接执行规则

- 仅靠自然语言关键词触发跳过审查：`直接执行`、`不用表格`、`跳过表格`、`无需审查`、`直接开跑`、`马上执行`。
- 未命中关键词时，默认走“先表格审查”流程。

## 硬约束

- 不删除 worker 分支。
- 不删除 worker worktree。
- 默认并发上限为 `8`。

## 产物位置

- 计划表：`ORCH_PLAN.md`
- 运行状态：`skills/tmux-claude-parallel-workers/.state/<run_id>.json`
- worker 日志：`skills/tmux-claude-parallel-workers/.logs/<run_id>/`
- worker 最终回复：`skills/tmux-claude-parallel-workers/.results/<run_id>/`
