# code-council

外部审查 skill：让 `pi` CLI 用独立模型（默认 `minimax-cn/MiniMax-M2.7`，生产切 `ohmyrouter/gpt-5.6-sol`）当 Mcode 的 reviewer。

## 前置依赖

| 依赖 | 说明 | 安装 |
|---|---|---|
| **pi CLI** | 跑 pi 调外部模型 | 见 [earendil-works/pi](https://github.com/earendil-works/pi#readme) |
| **jq** | JSON 解析 | macOS: `brew install jq` / Linux: `apt install jq` |
| **model provider** | pi 要连的模型服务 | 在 `.env` 里设 `CODE_COUNCIL_PROVIDER` + `CODE_COUNCIL_MODEL` |

`pi` 的安装、provider 配置、API key 怎么配——全部看官方 README，**不在这里复制**，避免上下文膨胀。

## 5 分钟跑通

```bash
# 1. 直接跑 stage 2 测一下
cd /Users/chongqing/.minimax/skills/code-council
./bin/pi-analyze.sh --plan examples/plan.sample.md --work-dir /tmp/code-council-test

# 2. 看输出
cat /tmp/code-council-test/stage2-issues.json | jq .

# 3. 跑 stage 3 (需要先有 stage 2 输出)
./bin/pi-fix-plan.sh \
  --issues /tmp/code-council-test/stage2-issues.json \
  --plan examples/plan.sample.md \
  --work-dir /tmp/code-council-test

# 4. 看 partial plan
diff examples/plan.sample.md /tmp/code-council-test/plan.partial.md
```

## 切到生产模型

**改 skill 目录的 `.env` 文件**（推荐，永久切换）：

```bash
# 编辑 ~/.minimax/skills/code-council/.env
CODE_COUNCIL_PROVIDER=ohmyrouter
CODE_COUNCIL_MODEL=gpt-5.6-sol
```

**或者**用 shell 环境变量临时覆盖（一次性，不改文件）：

```bash
CODE_COUNCIL_PROVIDER=ohmyrouter CODE_COUNCIL_MODEL=gpt-5.6-sol \
  bin/pi-analyze.sh --plan x.md
```

优先级：env var > .env > **报错（没有兜底默认值）**。

`.env` 里所有可用预设（MiniMax-M2.7 / MiniMax-M2.7-highspeed / gpt-5.6-sol）和字段说明都写好了，改文件即可。如果 `.env` 缺失或没设 provider/model，脚本会直接报错退出，错误信息告诉你该改哪个文件。

## 退出码

| code | 含义 |
|---|---|
| 0 | 成功, verdict 接受 |
| 1 | 参数错误 |
| 2 | pi 不可用 (3 次重试后) |
| 3 | 输出 schema 校验失败 |
| 10 | stage 3 第一轮跑完, 还有 unfixed escalates, 需要用户决策 (再跑一次带 `--user-decisions`) |
| 20 | stage 8 有 BLOCK 级 issue, 必须看 |

## audit log

每次 pi 调用一行 NDJSON，写到 `~/.minimax/agents/mcode/code-council-audit.logl`。

字段：`ts, stage, provider, model, verdict, pi_skipped, issues, extra`

复盘用法：

```bash
# 看最近 20 次
tail -20 ~/.minimax/agents/mcode/code-council-audit.logl | jq .

# 统计各 stage 的 verdict 分布
jq -r '"\(.stage) \(.verdict) blocks=\(.extra.blocks // 0)"' \
  ~/.minimax/agents/mcode/code-council-audit.logl | sort | uniq -c
```

## 维护

| 想改什么 | 改哪 |
|---|---|
| 调模型 / thinking | 环境变量 `CODE_COUNCIL_*` |
| 调 reviewer 人格 / severity 语义 | `prompts/reviewer-base.md` |
| 调 stage 2 诊断侧重 | `prompts/analyze.md` |
| 调 stage 3 fix / escalate 规则 | `prompts/fix-plan.md` |
| 调 stage 8 reviewer-of-reviewer 行为 | `prompts/review-code.md` |
| 调输出 schema | `bin/_schema.json` (改完顺手用 `check-jsonschema` 验一下) |
| 加重试次数 / 退避 | `_lib.sh` 里的 `CODE_COUNCIL_MAX_RETRIES` / `CODE_COUNCIL_RETRY_BACKOFF_SEC` |

## 设计哲学

1. **pi 不持文件**——`--tools read,grep,glob` 白名单，所有 plan/diff 写盘由 shell 做，留 audit
2. **升级项必走用户**——`action: escalate` 一律 `ask_user`，Mcode 不替用户决定
3. **两轮 fix 可重入**——stage 3 第一轮只修不决，第二轮带 `--user-decisions` 落最终版，幂等
4. **轻量 audit**——logl NDJSON 一行一次，不做 dashboard，靠事后 `jq` 复盘
5. **失败兜底不阻塞**——pi 不可用 → `pi_skipped=true`，不强退，让 Mcode 自己收尾

## 没做的事（暂不实现）

- ❌ 自动检测"pi 是不是比 Mcode 强"（靠 audit log 事后看）
- ❌ Web UI / 看板（`jq` 就够）
- ❌ 多 reviewer 并行（一次一个 pi 调用，简单够用）
- ❌ `--plan` 自动从 OpenSpec 抓（当前必须显式传路径）
- ❌ 集成到 Mcode 的 hook / 自动触发（当前是显式 skill 调用）
