---
name: tmux-openspec-parallel-workers
description: OpenSpec 驱动的 tmux 并发实现工作流。适用于强父代理先把编码需求分析成 OpenSpec change 和机器可读 worker 矩阵，再启动多个 Claude Code 子 worker 在独立 git worktree/branch 中并发实现，最后由 merge worker 统一整合和验证。
---

# Tmux OpenSpec Parallel Workers

## 目标

- 用 OpenSpec 作为父代理和子 worker 之间的实现契约。
- 父代理负责需求分析、OpenSpec 编写、任务拆分、验收标准和执行调度，不直接写实现代码。
- 多个 Claude Code worker 在 tmux 中并发运行，每个 worker 使用独立 branch 和 worktree。
- worker branch 和 worktree 默认保留，`close` 只关闭 tmux session。
- 完成的 worker 分支由专门的 merge worker 统一整合并运行最终验证。

## 命令入口

```bash
bash skills/tmux-openspec-parallel-workers/scripts/tmux-orch.sh <command> [args...]
```

脚本会运行 `node src/tmux-orch.mjs`。目标仓库从当前 `cwd` 所在 Git root 解析，不从 skill 目录反推。

## 命令

- `doctor`
- `draft --goal "<需求>" --change <change> [--workers N] [--matrix <path>] [--run <run_id>]`
- `run --run <run_id> [--reuse-session]`
- `wait --run <run_id> [--next-wave] [--until wave-done|all-done|integrated|complete] [--auto-integrate] [--timeout <sec>] [--interval <sec>] [--json]`
- `status --run <run_id> [--json]`
- `inspect --run <run_id>`
- `integrate --run <run_id> [--allow-partial]`
- `close --run <run_id>`

## 父代理流程

1. 创建或选择一个 OpenSpec change。
2. 写好 `proposal.md`、`design.md`、`tasks.md` 和 `specs/**`。
3. 写 `openspec/changes/<change>/tmux-orch.json`，矩阵细则见 `references/worker-protocol.md`。
4. 由父代理决定 worker 数量；可用 `--workers N` 校验矩阵中的 `workers.length`。
5. 先运行 `doctor`，再运行 `draft --goal "<需求>" --change <change>`。
6. 检查 `ORCH_PLAN.md`；拆分正确后运行 `run --run <run_id>`。
7. 推荐用 `wait --run <run_id> --next-wave --until all-done` 等待所有实现 worker，并自动推进依赖波次。
8. 所有实现 worker 为 `done` 后运行 `integrate --run <run_id>`，再用 `wait --run <run_id> --until integrated` 等 merge worker。
9. 也可以用 `wait --run <run_id> --next-wave --auto-integrate --until complete` 自动推进实现波次并自动启动 merge worker。
10. 用 `inspect` 汇总子 worker 输出；只在取消或清理 tmux 时运行 `close`。

## Wait 模式

- `wait` 是阻塞式等待命令，不是后台 daemon；中断后重新运行同一命令即可从现有 state 继续。
- `wait` 每轮读取 `.done` 退出码、result 文件、tmux pane 状态和 git handoff 状态，然后刷新 `.tmux-orch/state/<run_id>.json` 与 `ORCH_PLAN.md`。
- 默认 `wait --run <id>` 等当前 wave 结束，即直到没有 running worker。
- `--next-wave` 会在当前 wave 结束后自动运行下一批依赖已满足的 worker；未显式传 `--until` 时默认等到 `all-done`。
- `--auto-integrate` 会在所有实现 worker 完成后自动启动 merge worker；未显式传 `--until` 时默认等到 `complete`。
- `--until wave-done`：等当前 running worker 全部结束。
- `--until all-done`：等所有非 merge worker 都为 `done`。
- `--until integrated`：等 merge worker 为 `done`。
- `--until complete`：等实现 worker 全部完成；配合 `--auto-integrate` 时还会等 merge worker 完成。
- `--timeout` 默认 `3600` 秒，`0` 表示不设超时；`--interval` 默认 `5` 秒。

## Worker 矩阵

矩阵是必需文件，默认路径：

```text
openspec/changes/<change>/tmux-orch.json
```

最小结构：

```json
{
  "schema_version": "tmux-openspec-parallel/v1",
  "change": "change-name",
  "goal": "original user goal",
  "worker_count": 1,
  "verify_cmd": "openspec-cn validate --all --strict --no-interactive",
  "workers": [
    {
      "id": "w01",
      "title": "API contract implementation",
      "scope": "Implement backend endpoints from OpenSpec",
      "ownership": ["server/**", "openspec/changes/change-name/specs/api/**"],
      "depends_on": [],
      "acceptance": ["OpenSpec API scenarios are implemented"],
      "verify_cmd": "openspec-cn validate --all --strict --no-interactive && npm test",
      "commit": {
        "type": "feat",
        "scope": "api",
        "description": "实现 OpenSpec API 合约端点"
      }
    }
  ]
}
```

拆矩阵或排查矩阵问题时读取 `references/worker-protocol.md`。

## 执行规则

- `depends_on` 未全部 `done` 的 worker 保持 `planned`；依赖完成后再次运行 `run` 启动下一波。
- worker 只能编辑 `ownership` 覆盖的文件；确需边界修改时，父代理应先把路径加入矩阵 ownership。
- worker 不得运行 tmux orchestration 命令，不得切换分支，不得归档 OpenSpec change，不得再启动其他 worker。
- worker 不得手动提交；成功 worker 的改动由 orchestrator 按 Git handoff 协议自动提交。
- 自动提交信息必须说明实际修改内容，格式为 `<type>(<scope>): <中文描述>`；不要使用 `完成 w01 工作结果` 这类无信息描述。
- worker 最终摘要可以提供 `commit:` 字段覆盖矩阵默认提交信息，例如 `feat(cli): 增加任务文件读取错误提示`。
- 启动有依赖的 worker 前，orchestrator 会把已完成依赖分支合并到该 worker 的 worktree。
- `wait --next-wave` 会自动推进依赖波次，但不会掩盖失败；任一实现 worker `failed` 或 `blocked` 时会以非 0 退出。
- merge worker 负责整合完成的子分支并运行完整验证命令。
- 父代理可以检查 state、日志和报告，但不应手动实现或手动合并代码。

## 产物

- 计划表：`ORCH_PLAN.md`
- 运行状态：`.tmux-orch/state/<run_id>.json`
- worker 日志：`.tmux-orch/logs/<run_id>/`
- worker 最终回复：`.tmux-orch/results/<run_id>/`
- worker worktree：`.worktree-tmux-orch/<run_id>/`
- worker branch：`orchestrator/<run_id>/<worker-id>-<slug>`

## 配置

- 当前版本子 worker runtime 仅支持 Claude Code。
- 可用 `TMUX_ORCH_CLAUDE_CMD` 覆盖 worker 命令。
- 可用 `TMUX_ORCH_OPENSPEC_CMD` 覆盖 OpenSpec CLI。
- worker 数量由父代理通过矩阵决定；提供 `worker_count` 或 `--workers N` 时必须等于 `workers.length`。
- worker 总数和最大并发数硬上限都是 `10`。
- 最大并发默认 `8`；可用 `TMUX_ORCH_MAX_RUNNING_WORKERS` 覆盖，但仍会被限制到 `10`。
- 本地模型 profile 写在 `config.toml`，模板见 `config.example.toml`。
- profile 的 `auth_env` 控制 Claude Code 认证变量：`auth_token` 用于 Anthropic-compatible 网关，`api_key` 用于官方 Anthropic API key 路径，`both` 仅作为兼容兜底。
