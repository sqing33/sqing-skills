# Claude Code 模型评估规则

本参考用于编写运行规范、解读运行器产物，以及把结果集整理成人类可读报告。

## 推荐 Skill 模式

推荐工作流：

1. 长期维护一个只包含稳定模型和网关配置的 `models.yaml`。
2. 让 skill 从当前对话中读取：
   - 任务提示词
   - 目标仓库路径
   - 测试命令
   - 验收说明
   - 允许修改路径
3. 使用 `run-skill` 在内部生成临时 benchmark spec。

推荐 `models.yaml`：

```yaml
models:
  - id: provider-a-fast
    label: Provider A Fast
    launcher:
      type: claude-cli
      model: gpt-5
      max_turns: 25
      extra_args: []
    env:
      ANTHROPIC_BASE_URL: https://gateway.example/v1
      ANTHROPIC_API_KEY: secret
    timeout_minutes: 20
    budget_usd: 3.0
execution:
  max_parallel: 1
  workspace_mode: git-worktree
rubric:
  profile: coding-default
```

`execution.max_parallel` 支持真实并行。追求稳定时用 `1`；需要更快且能承受本地/网络负载时可用 `2` 或 `4`。

## 运行命令

```bash
bin/windows-amd64/eval_runner.exe run-skill \
  --models-file path/to/models.yaml \
  --task-id bugfix-001 \
  --prompt "Fix the failing feature." \
  --repo-path D:/work/my-repo \
  --artifacts-dir D:/benchmarks/run-001 \
  --test-cmd "python -m pytest -q" \
  --acceptance-notes "Keep the public API unchanged." \
  --allowed-path src/ \
  --allowed-path tests/
```

## 兼容的完整 Spec 模式

只有为了兼容、测试或用户明确要求时使用完整 `spec.yaml`。

```yaml
task:
  id: bugfix-001
  prompt: |
    Fix the failing feature.
  acceptance_notes: |
    Keep the public API unchanged.
  allowed_paths:
    - src/
    - tests/

target:
  repo_path: D:/work/my-repo
  setup_cmd: python -m pip install -r requirements.txt
  test_cmd: python -m pytest -q

models:
  - id: provider-a-fast
    label: Provider A Fast
    launcher:
      type: claude-cli
      model: gpt-5
      max_turns: 25
      extra_args: []
    env:
      ANTHROPIC_BASE_URL: https://gateway.example/v1
      ANTHROPIC_API_KEY: secret
```

## 评分维度

面向代码变更任务：

- 正确性：是否满足任务和测试。
- 范围控制：是否只改必要文件。
- 实现质量：代码是否清晰、可维护、符合项目风格。
- 验证质量：是否运行合适测试，是否解释失败。
- 稳定性：是否受启动、认证、超时、网关问题影响。

面向只读审查任务：

- 覆盖率：是否覆盖关键路径。
- 推理质量：结论是否有证据链。
- 结构清晰度：报告是否可读、可行动。
- 范围控制：是否遵守只读约束。
- 效率：是否避免无关扫描和噪声。

## 证据优先级

优先读取：

1. `summary.json`
2. `report.md`
3. `comparison_zh.md`
4. 每个模型目录中的 `status.json`
5. `tests.json`
6. `patch.diff`
7. `claude_stream.jsonl`

不要只看最终分数。分数用于定位问题，最终判断必须结合 diff、测试、日志和任务约束。

## 常见失败分类

- `startup_failure`：模型没有正常启动。
- `auth_failure`：认证或密钥问题。
- `gateway_failure`：网关 URL、模型 ID 或兼容接口问题。
- `timeout`：超时。
- `test_failure`：代码运行了，但测试失败。
- `scope_violation`：修改超出允许范围。
- `no_output`：没有有效输出。
- `readonly_violation`：只读任务中产生了不应有的 diff。

## 报告建议

中文报告应包含：

- 任务概况。
- 结论摘要。
- 横向对比表。
- 每个模型的优缺点。
- 推荐复用场景。
- 证据引用。

结论要能回答：

- 本任务推荐哪个模型。
- 哪个模型最稳。
- 哪些失败是环境问题，哪些是能力问题。
- 下一次应该如何调整模型、测试或任务提示词。
