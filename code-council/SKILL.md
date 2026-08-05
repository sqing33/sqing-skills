---
name: code-council
description: |
  8 段代码改动 pipeline，由 Mcode 编排，在两个关卡强制注入外部审查员（pi CLI + 独立更强的模型）。
  第一次：plan 写完后，让外部审查员独立诊断，发现 Mcode 自己看不到的盲区。
  第二次：代码改完后，让外部审查员对 Mcode 的初审查做二次审查，捕获 Mcode downgraded 的 BLOCK。
  适用于多文件、新接口、并发 / 状态机、权限 / 鉴权、数据迁移等高风险改动。
  触发词："做这个改动"、"实现这个 feature"、"修这个 bug" 等非平凡任务。
---

# code-council: 8 段外部审查 pipeline

> 你 (Mcode) 是**编排者**。`pi` 跑在外部 CLI 进程里，用一个**比你自己更强的模型**当独立审查员。
> 你不死磕 `pi` 的结论，**但也不替它做判断**——所有 `escalate` 项必须问用户。

## 前置依赖

- **pi CLI** — 安装和 provider 配置见 [earendil-works/pi](https://github.com/earendil-works/pi#readme)
- **jq** — macOS: `brew install jq` / Linux: `apt install jq`（已有可跳过）
- **model provider** — 在 `.env` 里设 `CODE_COUNCIL_PROVIDER` + `CODE_COUNCIL_MODEL`

## 何时使用

满足任一条件就走这个 pipeline（不可省）：

- 改动跨 ≥ 2 个文件
- 涉及新接口 / 新 schema / 新公共 API
- 涉及权限、认证、计费、数据迁移等高风险面
- 涉及并发、状态机、缓存等容易出错的设计
- 任务描述里有"完整实现"、"从 0 到 1"等暗示

简单 bug 修复（单文件、< 30 行）可以不走，但走一遍也只要 ~10 秒成本，**默认建议走**。

## 8 段流程

```
 1. 规划       (Mcode)              → 写 plan.md
 2. 任务分析   (pi)                 → 仅诊断, 不动 plan
 3. 修订 plan  (pi)                 → 自修非争议, 争议项标 escalate
 4. 处理升级   (Mcode → 用户)        → 每个 escalate 必走 ask_user, 不替你决定
 5. 应用决策   (pi 重跑)             → 把用户选择回灌 plan, 出最终 plan.final.md
 6. 实现       (Mcode + 子 agent)   → 落代码 + 跑测试
 7. 初次审查   (Mcode)              → 写 first-pass-review.md
 8. 二次审查   (pi)                 → reviewer-of-reviewer
```

## 退出条件

- 通过：`verdict=pass` 且无 BLOCK 级 issue
- 返工：有 BLOCK → 回 stage 6 修，重新走 7-8
- pi 不可用：标记 `pi_skipped=true`，用你自己 stage 7 的结论收尾（**不要主动跳过**）

## 硬规则

1. **每个 `action: escalate` 必须用 `ask_user` 工具向用户提问**——不能自己拍板，不能"我觉得 pi 说错了"直接覆盖。
2. **pi 不开 file system**——只给 `--tools read,grep,glob`，所有 plan 改写由 shell 写盘。
3. **审计**——每次 pi 调用写一行到 `~/.minimax/agents/mcode/code-council-audit.logl`，用于事后复盘"pi 抓的 BLOCK 是真漏还是幻觉"。

## 快速调用

```bash
# stage 2
bin/pi-analyze.sh --plan plan.md --work-dir .code-council/

# stage 3 第一轮 (自修, 抛 escalates)
bin/pi-fix-plan.sh --issues .code-council/stage2.json --plan plan.md --work-dir .code-council/

# stage 3 第二轮 (Mcode 收集用户决策后跑)
bin/pi-fix-plan.sh --issues .code-council/stage2.json --plan plan.md \
  --user-decisions '{"ESC-1":"B"}' --work-dir .code-council/

# stage 7
bin/pi-review.sh \
  --first-pass first-pass-review.md \
  --diff diff.patch \
  --plan plan.final.md \
  --work-dir .code-council/
```

## 模型切换

**改 skill 目录下的 `.env`**（不是 shell 环境变量，也不是硬编码默认）：

```bash
# 编辑 ~/.minimax/skills/code-council/.env
# 改这两行:
CODE_COUNCIL_PROVIDER=ohmyrouter
CODE_COUNCIL_MODEL=gpt-5.6-sol
```

**没有 provider/model 就直接报错退出**（不会用任何"兜底默认值"），错误信息会指向 `.env` 路径。

**优先级**：shell 环境变量 > `.env` > 报错。
所以一次性的覆盖（比如临时换模型对比）不用改文件：

```bash
CODE_COUNCIL_MODEL=gpt-5.6-sol bin/pi-analyze.sh --plan x.md
```

`.env` 里详细列了可用预设和每个字段的含义。

## 上限（防四不像 plan）

- escalate 总数 ≥ 5 → **强退到 stage 1 重做 plan**
- escalate 中 BLOCK 级 ≥ 2 → 同上
- 任一 pi 调用重试 3 次仍失败 → `pi_skipped=true`，不强退，让你自己走完

## 文件结构

```
~/.minimax/skills/code-council/
├── SKILL.md                   # 你正在读
├── prompts/
│   ├── reviewer-base.md       # 共同底座：人格 + 输出 schema
│   ├── analyze.md             # stage 2
│   ├── fix-plan.md            # stage 3
│   └── review-code.md         # stage 8
├── bin/
│   ├── _lib.sh                # retry / json 解析 / 校验 / audit
│   ├── _schema.json           # 严格 JSON schema
│   ├── pi-analyze.sh          # stage 2
│   ├── pi-fix-plan.sh         # stage 3 (两轮可重入)
│   └── pi-review.sh           # stage 8
├── examples/
│   ├── plan.sample.md
│   ├── diff.sample.patch
│   └── first-pass-review.sample.md
└── README.md
```
