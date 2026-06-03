---
name: claudecode-model-evaluator
description: 在多个 Claude Code 模型后端上对同一个编码任务运行可重复的基准测试，收集产物、比较输出质量、总结优势、劣势和失败模式。当需要评估不同 Claude Code 模型、API 网关或模型配置在相同编程任务上的表现时使用。
---

# Claude Code 模型评估器

这个 skill 用来把同一个编码任务交给多个 Claude Code 后端运行，并生成可复核的对比报告。它适合回答这类问题：

- 哪个模型更适合当前代码库的修复/实现任务？
- 哪个模型更能守住范围，不乱改文件？
- 哪个模型失败是因为启动/网关配置，哪个是真的编码质量不行？
- 不同模型在只读审查、安全审查、代码实现任务上的表现差异是什么？

## 核心原则

- 优先使用长期维护的 `models.yaml` 保存模型和网关配置。
- 不要把任务提示词、仓库路径、测试命令、允许修改路径写死在 `models.yaml`。
- 从当前对话和工作区提取本次任务信息，再用 `run-skill` 临时生成运行规范。
- 先读 `summary.json`，只有需要证据时再看每个模型的 `patch.diff`、`tests.json`、`status.json` 和日志。
- 最终结论默认用中文，除非用户明确要求其他语言。

## 目录结构

- `bin/linux-amd64/eval_runner`：Linux amd64 预编译运行器。
- `bin/macos-arm64/eval_runner`：macOS Apple Silicon 预编译运行器。
- `bin/windows-amd64/eval_runner.exe`：Windows amd64 预编译运行器。
- `runner/`：Go 运行器源码，可用于审查、修改和重新编译。
- `models.example.yaml`：模型配置示例。
- `models.yaml`：本地模型配置，使用前检查是否包含私密密钥。
- `references/rubric.md`：评分和报告解释规则。

## 必需输入

运行前必须确认这些信息：

- `task prompt`：要让模型完成的任务。
- `repo_path`：目标仓库路径，通常是当前工作区。
- `models.yaml`：共享模型配置文件。
- 验证方式：
  - 优先使用测试命令，例如 `npm test`、`pytest`、`go test ./...`。
  - 没有自动化测试时，使用明确的验收说明。
- 可选的 `allowed_paths`：任务应限制在特定文件或目录内时提供。

缺少关键输入时，只问缺少的项，不要要求用户手写完整 spec。

## 推荐工作流

1. 读取 `models.yaml`，确认模型、网关、执行并发和 workspace 策略。
2. 从当前上下文生成短 `task-id`、任务提示词、仓库路径、测试命令和验收说明。
3. 按平台选择运行器。macOS Apple Silicon 使用 `bin/macos-arm64/eval_runner`，Linux 使用 `bin/linux-amd64/eval_runner`，Windows 使用 `bin/windows-amd64/eval_runner.exe`。
4. 优先运行 `run-skill`：

```bash
bin/linux-amd64/eval_runner run-skill \
  --models-file path/to/models.yaml \
  --task-id <short-id> \
  --prompt "<task prompt>" \
  --repo-path <current repo> \
  --artifacts-dir <artifact dir> \
  --test-cmd "<test command>" \
  --acceptance-notes "<notes>" \
  --allowed-path <path-1> \
  --allowed-path <path-2>
```

5. 只有在已有完整独立 `spec.yaml`，或用户明确要求兼容模式时，才使用：

```bash
bin/linux-amd64/eval_runner run --spec path/to/spec.yaml
```

6. 如果只是重新生成报告，不重新跑模型，使用：

```bash
bin/linux-amd64/eval_runner summarize --artifacts-dir path/to/artifacts
```

macOS Apple Silicon 环境把示例中的 `bin/linux-amd64/eval_runner` 换成 `bin/macos-arm64/eval_runner`；Windows 环境换成 `bin/windows-amd64/eval_runner.exe`。

## `models.yaml` 结构

推荐把 `models.yaml` 作为长期配置，只保存稳定信息：

```yaml
models:
  - model: MiniMax-M2.7-highspeed
    env:
      ANTHROPIC_BASE_URL: https://api.minimaxi.com/anthropic
      ANTHROPIC_API_KEY: your-key
    headers:
      X-Provider: minimax
```

注意：

- `model` 是唯一必填模型名；运行器会自动用它生成 `id` 和 `label`，并等价展开为 `launcher.type: claude-cli` + `launcher.model`。
- 需要自定义产物目录名或报告展示名时，才额外写 `id` 或 `label`。
- 第三方 Anthropic 兼容网关中，`model` 必须是网关接受的真实模型 ID。
- 网关差异放在 `env` 里，优先走原生 `claude-cli` launcher；需要高级 Claude CLI 参数时可改用完整 `launcher` 写法。
- 不写 `execution.max_parallel` 时默认不限制并发，相当于本次有几个模型就同时跑几个。
- 不写 `execution.workspace_mode` 时默认使用 `git-worktree` 隔离每个模型的修改。
- 不写 `rubric.profile` 时默认使用 `coding-default`。
- `launcher.max_turns` 是可选项；不写时运行器不会传 `--max-turns`。
- `timeout_minutes` 是可选项；不写时运行器不设置进程超时。
- `budget_usd` 是可选项；不写时运行器不会传 `--max-budget-usd`。
- Claude Code 的 1M context 不是独立 runner 参数。按官方 Claude Code 模型配置，支持的模型可在模型名后追加 `[1m]`，例如 `model: claude-opus-4-7[1m]`；也可设置 `ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-7[1m]`。需要禁用 1M context 时设置 `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`。
- 每个模型可选 `headers`，运行器会把它转成 Claude Code 支持的 `ANTHROPIC_CUSTOM_HEADERS` 多行请求头；如果 `env.ANTHROPIC_CUSTOM_HEADERS` 已存在，会追加而不是覆盖。
- 运行器写入 `run_spec`、`summary`、`status`、日志和报告前会脱敏常见 key/token/authorization 文本，但仍应避免让模型主动打印环境变量。
- 只有本地 `claude` CLI 无法直接驱动后端时，才使用备用 `launch_cmd`。

## Windows 中文输入注意

在 Windows 上，如果任务提示词或验收说明包含中文，优先使用 `--prompt-file` 和 `--acceptance-notes-file`，避免 PowerShell 命令行编码导致 `task_packet.md` 乱码。

## 重新编译运行器

需要修改或重新编译运行器时进入 `runner/`，优先使用构建脚本。脚本会先运行 `go test ./...`，再编译三个平台：

```bash
cd runner
./build.sh
```

Windows PowerShell：

```powershell
cd runner
./build.ps1
```

输出路径：

- `bin/linux-amd64/eval_runner`
- `bin/macos-arm64/eval_runner`
- `bin/windows-amd64/eval_runner.exe`

## 运行器环境变量

运行器会为每次模型调用导出：

- `MODEL_EVAL_TASK_PACKET`：生成的 `task_packet.md` 路径。
- `MODEL_EVAL_ARTIFACT_DIR`：该模型的产物目录。
- `MODEL_EVAL_WORKSPACE`：隔离工作区路径。
- `MODEL_EVAL_MODEL_ID`：模型标识。
- `MODEL_EVAL_TASK_ID`：任务标识。
- `MODEL_EVAL_RESULT_FILE`：可选 JSON 结果文件，包装器可写入 `cost_usd` 等额外数据。

## 结果解读

按这个顺序看产物：

1. `summary.json`
2. `report.md`
3. `comparison_zh.md`
4. 需要证据时查看每个模型的 `status.json`、`tests.json`、`patch.diff`、`claude_stream.jsonl`

使用 [references/rubric.md](references/rubric.md) 的评分权重，但不要把硬分数当作唯一结论。最终报告要解释：

- 哪个模型最适合当前任务。
- 哪个模型最能控制修改范围。
- 失败来自启动/认证/网关不稳定，还是来自编码质量。
- 每个模型下一次更适合承担什么类型任务。

## 只读审查模式

运行器支持两类任务：

- `code_change`：实现、修复、重构，预期会修改文件。
- `readonly_review`：审计、安全审查、分析类任务，不应修改现有代码。

只读任务中：

- `no diff` 不等于 `no output`。
- `result_text`、`stdout.log` 或 `claude_stream.jsonl` 中的强审查报告算有效输出。
- 评分应更关注覆盖率、推理质量、结构、范围控制和效率。

## 报告要求

默认面向人类的报告包含：

- 任务概况
- 结论摘要
- 横向结果表
- 模型逐项点评
- 复用建议
- 证据引用

报告默认不展示美元成本，即使原始产物里保留了调试用成本字段。

## 故障排查

- 响应来自错误后端：检查 `env.ANTHROPIC_BASE_URL`。
- `model_not_found`：检查 `launcher.model` 是否匹配网关真实模型 ID。
- 看起来没带密钥：检查 `status.json` 中的 `auth_env_source`。
- 手动 `claude -p` 成功但基准失败：确认运行器使用 `--setting-sources local`，避免 `~/.claude/settings.json` 覆盖路由或认证。
