# 运行环境

本 skill 支持在 Linux、Docker、CI 和定时任务中运行。**不依赖 Playwright、不依赖桌面浏览器**——所有需要浏览器渲染的场景（JS 页面、SPA、截图）都使用内置的 `control-in-app-browser` skill。

## 候选采集（零依赖）

```bash
python3 scripts/collect_ai_hotspots.py
```

仅采集候选只需要 Python 标准库，无需任何额外安装。

## 核验事实

```bash
python3 scripts/verify_hotspots.py --input candidates.json > evidence.json
```

`verify_hotspots.py` 只用普通 HTTP 请求页面，提取标题/正文/图片线索。它不启动浏览器、不做截图——遇到 JS 页面或需要截图时，由 AI 使用 `control-in-app-browser` skill 处理。

少量条目或关闭搜索发现：

```bash
python3 scripts/verify_hotspots.py --input candidates.json --max-items 3
python3 scripts/verify_hotspots.py --input candidates.json --no-search
```

## 获取顺序

1. 使用 Python 标准库 HTTP 请求候选页（`verify_hotspots.py` 自动完成）。
2. 页面是 Google News 跳转、JS 空壳或 HTTP 失败时，AI 用 `control-in-app-browser` skill 打开页面。
3. 候选页不是一手来源时，用 `web_search` 定位官方、GitHub、Hugging Face 或 arXiv 页面。
4. 打开排名靠前的证据页，提取标题、描述、正文摘录和图片候选。
5. `primary_candidate_found` 只表示找到疑似一手页面，不表示核心事实已经自动核验；写稿前仍要逐条检查证据。

## 输出

`verify_hotspots.py` 输出 JSON：

- `evidence_status`：`primary_candidate_found` / `secondary_only` / `insufficient_evidence`
- `evidence_pages[]`：最终 URL、标题、描述、正文摘录、来源等级、获取方式和错误
- `image_suggestions[]`：页面图片、区块提示和版权备注
- **不再输出 `screenshot_path`**——截图由 AI 用 `control-in-app-browser` skill 在配图阶段完成

## 服务器约束

- 支持 `HTTP_PROXY` / `HTTPS_PROXY`，不要把账号 Cookie 或 token 写入 skill 目录。
- 不绕过验证码、登录、付费墙或站点风控；遇到阻断时保留错误并降级到其他来源。
- 定时任务建议先保存 collector JSON，再保存 evidence JSON，方便排查当天来源变化。

推荐 cron/CI 分阶段检查退出码：

```bash
set -euo pipefail
DATE=$(date -u +%Y-%m-%d)
OUT=output/$DATE
mkdir -p "$OUT/images"
python3 scripts/collect_ai_hotspots.py > "$OUT/candidates.json" 2> "$OUT/collect.log"
# AI 在此写入 web_candidates.json（路B 发现）
python3 scripts/verify_hotspots.py --input "$OUT/candidates.json" > "$OUT/evidence.json"
# AI 选题核验后写 verified.json，再生成 daily-report.md
```

不要只检查普通管道的最终退出码，否则 collector 失败可能被后续命令掩盖。

## 产物目录规范

每次运行按日期分目录，支持复盘和断点续跑：

```
output/<YYYY-MM-DD>/
  candidates.json      # 路A collect_ai_hotspots.py 输出
  web_candidates.json  # 路B AI补充的候选（AI 手动写，格式见下）
  evidence.json        # verify_hotspots.py 输出
  verified.json        # 最终入选条目 + 配图状态（AI 选题后写）
  daily-report.md      # 最终成稿
  images/              # 所有配图
  collect.log          # warnings 和错误日志
```

`web_candidates.json` 格式（路 B 每条必须字段）：

```json
{
  "title": "...",
  "url": "...",
  "published_at": "ISO 8601 或 null",
  "source_name": "web_search:<query>",
  "category": "models|opensource|products|industry|papers|research",
  "discovered_via": "实际执行的 query"
}
```

## 自动化边界

`evidence.json` 是证据候选，不是最终日报。完全无人值守发布还需要单独调用 LLM 或内容代理，逐条检查 `evidence_pages[]` 后生成 7–10 条 Markdown；不要仅凭 `primary_candidate_found` 自动发布。
