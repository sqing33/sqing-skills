---
name: ai-daily-news
description: 聚合开放 RSS、Atom 和 JSON API 中的最新 AI 热点，覆盖官方发布、行业新闻、模型、论文与开源项目。脚本只负责收集、规范化、去重，不过滤任何内容；所有选题、评分和优先级排序由 AI 分析师完成。再用 HTTP 优先、内嵌 `control-in-app-browser` skill 兜底的服务端流程解析跳转、定位一手来源、提取正文和图片线索（通过 `asset_fetcher` 子模块支持 curl 直链 / web_fetch grep / Browser 截图三种工作流），生成每日 7–10 条聚合新闻文档用于查看；支持 Linux、Docker、CI 和定时任务，不依赖桌面浏览器。只有用户明确提供 B 站 UP、BV 号或要求 B 站时才启用可选 B 站采集。
---

# 多源 AI 热点聚合新闻

先发现热点，再核验事实，最后写稿。热点排名只用于选题，不能代替事实核验。

## 设计理念

脚本（`collect_ai_hotspots.py`）**只做三件事**：收集、规范化、去重。它不做任何内容过滤——不按分数筛选、不按来源设上限、不按类别设配额、不判定噪声、不选短名单。所有候选原样输出给 AI 分析师，由 AI 阅读全部候选后自行决定哪些值得核验、哪些可以忽略。

这样做的原因：硬编码的过滤规则（如来源权重、类别配额、噪声 regex）会系统性遗漏重要新闻。例如同一事件的报道如果来自权重较低的来源、或标题用了非标准表述，就会被过滤掉。

## 快速开始

默认采集最近 24 小时的全部合并候选（上限 200 条）：

```bash
python3 scripts/collect_ai_hotspots.py
```

常用选项：

```bash
python3 scripts/collect_ai_hotspots.py --hours 24 --limit 500
python3 scripts/collect_ai_hotspots.py --category models --category opensource
python3 scripts/collect_ai_hotspots.py --source openai --source huggingface-papers
python3 scripts/collect_ai_hotspots.py --language zh --format markdown
```

脚本只依赖 Python 标准库。读取以下可选环境变量：

- `GITHUB_TOKEN`：提高 GitHub API 限额。
- `AI_HOTSPOT_USER_AGENT`：覆盖请求 UA。
- `BILIBILI_COOKIE`：仅显式运行 B 站采集器时读取。
- `BRAVE_SEARCH_API_KEY`：可选；核验时优先使用 Brave Search API，未设置则用 web_search。

候选采集保持零依赖。需要解析 JS 页面、搜索证据或截图时，使用内置的 `control-in-app-browser` skill——**不需要安装 Playwright 或任何额外浏览器依赖**。

源配置、启停状态和默认权重见 `references/sources.json`。要修改来源时编辑该文件，不要把源列表复制进 `SKILL.md`。

## 工作流

发现候选走**两路并行**：脚本收集 RSS/API + AI 主动 web_search。**两路是平等的主候选通道**，不是「脚本为主、web_search 兜底」。

### 1. 发现候选（两路并行）

**路 A — RSS / Atom / JSON API 收集（脚本自动）**

运行 `scripts/collect_ai_hotspots.py`。默认路径只请求开放 RSS、Atom 和 JSON API，不访问 B 站，也不需要浏览器。

脚本输出 JSON，检查其中的：

- `candidates[]`：全部已归一化、去重的候选，按新鲜度排序（最新在前）。**不做任何内容过滤。**
- `source_stats`：每个源的成功状态和条目数。
- `warnings[]`：单源失败、解析异常或限流；其他源成功时继续工作。
- `candidate_pool_count`：合并去重后的候选总数。

每条候选包含 `title`、`url`、`published_at`、`source_name`、`source_kind`、`source_tier`、`language`、`category`、`summary`、`signals`、`score`、`duplicate_sources` 和 `verification_status`。

`score` 字段是**信息性元数据**，仅反映来源权重、新鲜度和信号的组合，**不代表新闻重要性**。不要把分数作为选题的唯一依据，也不要等同于可信度。

**重要：候选池按 score 降序输出，但选题必须扫描全池**。阅读 `candidates[]` 时执行：

1. 先快速通读全部候选的 `title`（不跳过低分条目）。
2. 对 `score < 15` 的条目，至少检查 `title` + `source_name`，确认是否为低权重源（`community` / `secondary`）报道的重大事件——这类条目分数低但可能新闻价值高。
3. 若发现低分高价值条目被高分噪声淹没，提升其选题优先级，不受 score 限制。
4. 记录"已扫描全池 N 条，低分检查 M 条"作为选题依据，写进总览表的线索来源备注。

`signals.trending_snapshot=true` 表示"当前正在流行"，不表示今天新发布；此类条目的 `published_at` 保留模型原始创建时间，写稿前必须核实最近为何升温。

**路 B — AI 主动 web_search（必做，不只是兜底）**

脚本跑完后，AI **必须**用 `web_search` 主动搜当天的 AI 大事件，作为**和路 A 平等的主候选通道**。原因：RSS 经常漏掉（特别是国内生态的当日头条、官方博客的未推送更新、HuggingFace 实时热门、刚爆出的突发新闻）。路 B 不是「补漏」，是「双路并行」。

关键词模板（每天按需组合，2-3 个 query 起步）：

- **当日总览**：「<日期> AI 新闻」「<日期> 大模型发布」「AI announcement today」「AI breakthrough <日期>」
- **厂商专项**：`<公司> announcement`（OpenAI / Anthropic / Google DeepMind / Meta / xAI / Mistral / 字节 / 阿里 / 腾讯 / 百度 / DeepSeek / 月之暗面 / MiniMax / 智谱 / 商汤）
- **国内生态**：「<日期> 36Kr AI」「<日期> 智东西」「<日期> 钛媒体 AI」「<日期> 雷峰网」「<日期> 极客公园」
- **开源 / 平台**：`site:huggingface.co <日期>`、`site:github.com <日期> trending AI`、`site:arxiv.org <日期> AI`
- **HuggingFace 实时**：`Hugging Face trending models today`、`HF papers <日期>`

**任何 web_search 找到但 RSS 没抓到的，都直接进入候选池**（AI 在选题阶段一并评估）。每条 web_search 发现的候选也要写齐 `title` / `url` / `published_at`（如能拿到）/ `source_name`（如 `web_search:<query>`）/ `category` 字段，方便后续去重和核验。

### 2. AI 分析与选题

AI 分析师阅读**两路合并后的候选池**（路 A 脚本输出 ∪ 路 B web_search 主动发现），自行完成以下工作（这些工作不再由脚本完成）：

1. **过滤噪声**：识别股票/ETF 评级、营销内容、与 AI 无关的条目，并排除。
2. **合并同源（必走检查清单）**：路 A 已做 URL+标题去重，但路 B 没去重，且路 A 与路 B 之间也没去重。选题前对两路合并后的候选池执行以下检查：
   - **URL 同源**：`canonical_url` 相同（去掉 `utm_*` / `fbclid` / `ref` / `source` 等跟踪参数后一致）→ 合并，`duplicate_sources` 记录两路来源。
   - **标题相似**：两标题去掉「发布 / 推出 / 上线 / announces / launches / releases」等动作词和停用词后，核心实体 + 产品名一致 → 合并（例：「Qwen3-Max 发布」vs「阿里云开源 Qwen3-Max」→ 合并）。
   - **实体 + 动作同源**：同一公司 + 同一产品 + 同一动作（发布 / 开源 / 融资），即使标题表述不同 → 合并为一条，相关链接收齐所有入口。
   - **跨语言同源**：英文原标题和中文媒体报道同一事件 → 合并，保留官方语言版本为主条目。
   - 合并后，每条主条目的 `duplicate_sources` 字段应列出所有合并来源（RSS 源名 + `web_search:<query>`）。
3. **评估重要性**：基于新闻价值、跨源覆盖、影响范围、受众相关度等因素自行判断。
4. **构建选题短名单**：选出值得核验的候选，不受脚本配额限制。
5. **查漏补缺（信号触发，非直觉）**：以下任一信号出现时，必须补一轮定向 `web_search`，并记录触发原因：
   - **信号 A — 主源空返回**：`source_stats` 中某个 `tier=primary` 的源 `ok=false` 或 `accepted_entries=0` → 针对该源对应的厂商 / 平台定向搜（如 OpenAI 源挂了 → 搜 `OpenAI announcement <日期>`）。
   - **信号 B — 用户点名未命中**：用户在 prompt 里提到的事件 / 厂商 / 产品在候选池（路 A + 路 B）中无匹配 → 定向搜「`<事件>` + 官方关键词」。
   - **信号 C — 高可信度条目不足**：核验阶段结束后统计可信度=高的条目 < 5 → 扩大时间窗（见第 3 步「时间窗扩大检查点」）或补搜当日已知重大事件。
   - 每次补搜的结果同样进入候选池，走本步「合并同源」检查清单。

用户限定数量、类别或来源时严格遵守。

### 3. 核验事实

核验分两部分：脚本收集证据 + AI 用 `control-in-app-browser` skill 打开页面确认。

**3a. 脚本收集证据（HTTP 优先）**：

```bash
python3 scripts/verify_hotspots.py --input candidates.json > evidence.json
```

`verify_hotspots.py` 只用普通 HTTP 请求页面、提取标题/正文/图片线索。`primary_candidate_found` 只表示找到疑似一手页面，不能替代逐条事实判断。

**3b. AI 用浏览器确认（JS 页面 / 需要交互时）**：

HTTP 拿不到内容的页面（Google News 跳转、JS 空壳、SPA），用内置 `control-in-app-browser` skill 打开：

```
skill({ name: "control-in-app-browser" })
browser navigate "https://..."
browser inspect
```

读取 `references/source-checking.md` 并逐条执行：

1. 从候选标题抽取实体、产品、模型、仓库、论文和动作词。
2. 候选 URL 是官方页面时直接打开核验；Google News、媒体、HN 等只作为线索入口。
3. 只有标题或摘要时，用中英文关键词搜索官方博客、产品文档、GitHub、Hugging Face、arXiv、论文项目页或公司公告。
4. 至少找到一个能确认核心事实的来源，才进入完整成稿。
5. 找不到一手来源时，可用可信媒体谨慎确认并降低可信度；核心事实仍无法确认时放入"需核实选题池"。

**web_search 交叉验证（必做，防止单源错信）**：

每个候选在找到一手来源后，再用 `web_search` 做一轮交叉验证：

- **正向验证**：搜「`<事件>` site:<官方域名>」确认厂商自己也讲过；搜「`<事件>` + 关键词」找第二路独立信源
- **反向验证**：搜「`<事件>` NOT released / delay / issue / 翻车 / 打脸」找有没有漏掉的负面信号或日期变更
- **时效验证**：搜「`<事件>` <日期>」确认发布日期没被张冠李戴

**可信度判定**：
- ≥ 2 路独立信源 + 1 个反向核验无负面 → **高**
- 1 路信源 + 反向无负面 → **中**
- 只有 1 路或反向搜到负面 → **低**，成稿时必须标风险

**"独立信源"的定义**（判定 ≥2 路时必须遵守）：
- 两路信源有**编辑独立性**才算独立——都引用同一份官方公告的，算 1 路（官方源本身）。
- 官方源 + 独立媒体的**实测 / 独立采访 / 独立分析** → 算 2 路。
- 两路都是"转载同一一手源"（如两家媒体都引 OpenAI 博客）→ 算 1 路。
- 一手源 + 包注册表 / 应用商店 / GitHub Release / Hugging Face 模型卡等**不同性质的平台** → 算 2 路。

**特别注意状态变更**：同一事件的"发布公告"和"正式发布"可能相隔数天。例如模型发布公告在 7/31，但开源权重在 8/3 才上线。必须区分"宣布日期"和"实际发生日期"，写稿时以最新状态为准。

每日最终成稿目标为 8 条，允许 7–10 条。

**时间窗扩大检查点（核验阶段结束后执行）**：

1. 统计 `evidence.json` 中 `evidence_status=primary_candidate_found` 且 AI 判定可信度=高的条目数 N。
2. 若 N ≥ 7 → 进入写稿。
3. 若 5 ≤ N < 7 → 重跑 `collect_ai_hotspots.py --hours 48`，对扩展条目在总览表「发布时间」列追加「（48h 窗口）」标注。
4. 若 N < 5 → 扩大后仍不足时宁缺毋滥，不用未核实或低质量条目补数。

默认内容配比：模型/官方产品 2–3 条、开源项目/开发工具 2–3 条、行业动态 1–2 条、论文/技术研究 1–2 条。以通过核验为前提，不为满足配比保留弱选题。

### 4. 提取配图素材（asset_fetcher 子模块 + Browser 兜底）

**核心原则：直链能拿到合格图 → 不用 Browser；拿不到或图不合格 → 必须用 Browser。没有"跳过配图"这个选项，没有"无本地配图"这个出口——每条热点必须有合格配图。**

**🔴 必做：图源优先级（不按此顺序 = 任务未完成）**

每条热点的配图必须先走 1→2→3,只有上一级拿不到才能降级。**任何条目最终没拿官方源,必须在 4 行元数据的"备注"里写明降级原因**(如"X 月 X 日官方博客未发原图,改用 The Decoder 报道主图")。

| 优先级 | 来源类型 | 示例 | 为什么优先 |
|---|---|---|---|
| **1 (必走)** | **官方一手** | 公司博客(anthropic.com / qwen.ai / minimaxi.com / deepseek.com)/ 官方 X 账号(@karpathy @sama 等)/ HuggingFace 模型卡 README 资源 / GitHub README / arXiv 论文图 / 项目官方主页(官方网站而非搜索引擎收录页) | 无广告、无平台 logo、原始参数和版本号都在、信息密度最高 |
| 2 | 权威英文媒体 | The Decoder / TechCrunch / The Information / Ars Technica | 无广告堆叠、有独立记者标注 |
| 3 (兜底) | 中文媒体转载的腾讯 CDN(智东西/36氪/钛媒体/IT之家) | `qqpublic.qpic.cn/qq_public/...` | 几乎都带平台 logo/广告水印/UI 边框,信息密度被稀释,仅在 1+2 都无图时使用 |

**反例**(踩过的坑):Karpathy 推文演示图、自变量 HOST 框架图、灵波科技业务全景图 —— 都被默认用了腾讯 CDN 转载,而不是 `karpathy.ai/lotr-movie/` 实拍、`arxiv.org/abs/2607.20033` 论文图、灵波官网,导致"几乎都有广告"。**不允许再发生**。

**目标**:每篇报道配 1–5 张有信息价值的图,**必须**用 `![alt](images/xxx.png)` Markdown 语法**真正嵌入**最终 .md 文件;**只写路径字符串 = 没做**。

核心 1 张(主图,例如官方 banner / 性能对比表) + 补充 0-4 张(架构图 / 排行榜 / demo 截图 / 数据可视化)。

#### 信息价值判断表（贯穿全流程的判定标准）

每次拿到候选图（无论来自 curl 还是 Browser 截图），都对照此表判断。**这是唯一判定标准，不合格的图不凑合用**：

| ✅ 合格 → 保留 | ❌ 不合格 → 必须重取 |
|---|---|
| 官方架构图（含技术参数） | 模型卡页面外壳（只有框架 + 徽章 + pip install 代码） |
| 性能对比表 / benchmark 图 | 公司 logo / brand 装饰 |
| 真实 demo 视频截图 | "Like / Follow" UI |
| 排行榜 / 趋势图 | 留白 hero 区域 |
| 含版本号 + 发布日期的官方 banner | 信息密度低的宣传图 / HF 默认 social 缩略图 |
| 优先级 1 来源(官方博客/HF/README/官方 X) | 带平台 logo / 广告水印 / UI 边框的新闻转载图(腾讯 CDN 智东西/36氪 转载等,仅在 1+2 都无图时降级) |

#### 配图获取流程

**Step 0 — 复用 evidence.json 的 image_suggestions**

核验阶段（第 3 步）已经打开过每个一手页面，`evidence.json` 的 `evidence_pages[].image_suggestions[]` 带回了页面图片 URL（每条含 `url` / `source_page` / `section_name` / `what_to_capture` / `rights_note`）。先检查这些已拿到的 URL，避免重复 navigate：

1. 遍历每条入选候选的 `evidence.json` → `results[].evidence_pages[].image_suggestions[]`。
2. 对每个 suggestion 的 URL 执行 **Step 1**（curl 直链获取 + 信息价值判断）。

**Step 1 — curl 直链（零依赖，首选）**

适用于：HuggingFace / Sanity / 微信公众号图床等有公开直链的资源。

```bash
asset_fetcher.py curl --url <直链> --output images/xxx.png
```

- 拿到图 → 用 `images_understand images/xxx.png` 检查内容 → 对照**信息价值判断表**：
  - ✅ 合格 → 用，**不需要 Browser，流程结束**
  - ❌ 不合格 → 删除该文件，进入 Step 2
- 拿不到图（404 / 超时 / 空文件）→ 进入 Step 2

**Step 2 — web_fetch + grep + curl（标准页）**

适用于：有 HTML 但图嵌在页面里的情况。

```bash
web_fetch(<发布页>) > /tmp/page.md
asset_fetcher.py extract --file /tmp/page.md    # 输出图片 URL 列表
# 选最佳 1-5 个 URL → 每个 curl
```

- HTML 中有真实图片 URL（非 favicon / logo / 头像）→ curl，回到 **Step 1 的信息价值判断**
  - ✅ 合格 → 用，**不需要 Browser，流程结束**
  - ❌ 不合格 → 进入 Step 3
- HTML 是空壳 / `web_fetch` 返回 < 500 字符 / 拿不到 → 进入 Step 3

**Step 3 — 用 control-in-app-browser skill 截图（强制兜底，不是可选项）**

**本 skill 内置 `control-in-app-browser` skill，这是唯一指定的浏览器截图工具。不需要安装 Playwright 或任何额外依赖。**

**触发条件（满足任一即必须执行，不得跳过）**：
- Step 1 + Step 2 都没拿到合格图
- 平台是 SPA（openrouter.ai / arena.ai / lmarena.ai / huggingface.co/spaces/ / claude.ai）
- 候选图被判为 HF 默认缩略图 / logo / 留白（Step 1 信息价值判断不合格）
- 页面是 JS 渲染，`web_fetch` 拿不到内容

**执行步骤（必须按顺序，每步真做）**：

```
1. skill({ name: "control-in-app-browser" })        # 加载内置浏览器 skill（第一步，必须做）
2. browser navigate "https://..."                    # 打开目标 URL
3. browser inspect                                    # 找到目标区块（榜单/模型卡/性能表）
4. browser screenshot --clip <x,y,w,h>               # 截取指定区块
   --output images/xxx.png
5. images_understand images/xxx.png                  # 验证内容（必须做）
```

- 截图文件 > 50KB 且 `images_understand` 确认包含目标内容 → 用
- 截图仍不合格（空白 / 遮挡 / 裁切错误）→ 换 clip 区域 / 滚动页面 / 切换 tab 重截
- **必须持续重试直到拿到合格图，不允许放弃**：换 clip 区域 → 滚动触发懒加载 → 关闭 cookie 弹窗后重截 → 换页面（如从模型卡切到 README / paper 页 / leaderboard）→ 换候选热点中其他可截图页面

**Step 3 执行证据（防止偷懒）**：调用 Browser 后必须记录 `browser navigate URL` + `screenshot clip 参数` + `文件大小`。`images/` 下必须有对应的合格截图文件——**没有"无本地配图"这个出口，每条热点必须有图**。

#### 成稿后图片审查（必做，不能跳过）

**所有热点写完、图片全部嵌入 .md 后，逐图复查一遍**。这是最后一道防线，防止"拿到图就塞进去"导致配图和正文不匹配。

审查方式：

```
对 images/ 下每张图执行 images_understand images/xxx.png，检查：
1. 内容相关性：图片内容是否和该条热点的正文主题匹配？（如正文讲 Qwen3-Max 性能，图却是一张通用的阿里云 logo → 不匹配）
2. 信息价值：对照信息价值判断表，是否仍为合格图？（如 curl 时看着像架构图，实际是装饰图 → 不合格）
3. 文字可读性：图中的参数 / 版本号 / 排行榜文字是否清晰可读？（模糊 / 裁切掉关键信息 → 不合格）
4. 版权状态：是否为官方素材或有授权？（来源不明的图 → 不合格）
```

审查结果处理：
- ✅ 全部合格 → 交付
- ❌ 任一不合格 → **删除该图，回到该条热点的 Step 1 重新获取**（走完整 curl → web_fetch → Browser 流程），直到拿到合格图为止

**图片不嵌入 = 未完成**。最终 .md 文件交付前必须确认:
- 每个热点正文里都有 `![alt](images/xxx.png)` 真正渲染的配图
- 不能只写 `图片链接:images/xxx.png` 这种纯文本字符串
- **每条热点必须有合格配图，不存在"无图"的条目**

**绝不要做的事**:
- ❌ 对发布页整页截图(那是"懒"图,通常只有 logo + 装饰 + 留白)
- ❌ 用 PIL 缩放后保存(双重缩放糊掉)
- ❌ 拿"HF 模型卡外壳"(只有框架 + 徽章 + pip install 代码)当配图
- ❌ **只写"图片建议"占位而不实际触发 Browser —— 这是把"必要流程"伪装成"建议"**
- ❌ **拿到一张图就直接嵌入，不做 `images_understand` 内容审查**

**完整工作流**:

```bash
# 工具:scripts/asset_fetcher.py (零依赖,标准库)
python3 scripts/asset_fetcher.py --help

# Workflow 1: 已知直链(HF/Sanity/微信公众号)
python3 scripts/asset_fetcher.py curl \
  --url "https://huggingface.co/MiniMaxAI/H3-Base-FL2VA/resolve/main/assets/minimax-h3.png" \
  --output images/h3-banner.png

# Workflow 2: 从发布页 HTML 找图
web_fetch("https://huggingface.co/MiniMaxAI/H3-Base-FL2VA") > /tmp/h3_readme.md
python3 scripts/asset_fetcher.py extract --file /tmp/h3_readme.md
# → 输出 [huggingface-assets] https://huggingface.co/.../assets/... 列表
# → 选最佳 1-5 个 → curl 每个

# Workflow 3: SPA / JS 渲染平台截图(必做,不接受只写"需用 Browser")
skill({ name: "control-in-app-browser" })             # 第一步:加载 Browser skill
browser navigate "https://openrouter.ai/rankings"      # 第二步:打开页面
browser inspect                                          # 第三步:找到 Top 5 区块
browser screenshot --clip <x,y,w,h> --output images/openrouter-top5.png  # 第四步:截图
images_understand images/openrouter-top5.png           # 第五步:验证内容

# Workflow 4: 微信公众号
# 注意: 公众号原文 mp.weixin.qq.com URL 不可被搜索引擎索引
# → web_search 找"媒体转载"(IT之家/潮新闻/企鹅号/CSDN)
# → web_fetch 转载页 HTML
# → grep qqpublic.qpic.cn/qq_public/ 拿到腾讯图床直链
# → curl 该直链(图片左下角可见公众号 logo = 一手权威性证明)
```

**主流平台图床速查**(完整版见 `references/image-extraction.md`):

| 平台 | 发现方法 | URL pattern | 触发 Browser? |
|---|---|---|:-:|
| huggingface.co | `web_fetch` README.md → grep `assets/...png` | `huggingface.co/{org}/{repo}/resolve/main/{path}` | 默认 social 缩略图时必触发 |
| cdn.sanity.io | `web_fetch` HTML → grep `_next/image?url=` → URL decode | `cdn.sanity.io/images/{id}/{dataset}/{path}` | 一般不需要 |
| qqpublic.qpic.cn | 媒体转载页 → grep `qqpublic.qpic.cn` | `qqpublic.qpic.cn/qq_public/{bucket}/{hash}/0?fmt=...` | 一般不需要 |
| www-cdn.anthropic.com | 同 Sanity | `www-cdn.anthropic.com/images/{id}/website/{file}` | 一般不需要 |
| OpenRouter / LMArena | web_fetch 拿空 → **必须**浏览器截图 | — | **必触发** |
| HuggingFace Spaces | web_fetch 拿空 → **必须**浏览器截图 | — | **必触发** |

**每个素材记录**:
- 本地路径 (`images/xxx.png`) + 原始 URL + 来源类型 + 尺寸 + 用途
- 截图需检查: 遮罩? 空白? 懒加载? 版权状态?
- **最终必须确认**:每张图都对应 .md 文件里至少一个 `![alt](images/xxx.png)` 真正渲染

**踩坑笔记**(完整版见 `references/image-extraction.md`):
- macOS 没有 GNU `timeout` → asset_fetcher 已带 `--max-time 30` 兜底
- HF 仓库页 DOM `img` query 返回空 → 必须 `web_fetch` raw README
- 微信公众号原文 URL 不可索引 → 走"媒体转载 → grep 直链"绕道
- 浏览器 cookie 弹窗挡数据 → 截图前先 click dismiss,或用 clip 绕开
- PNG optimize 在大图上会 hang → 改用 sips 转 jpg q=95,或直接无损 PNG
- HF 默认 social 缩略图信息密度低 → 触发 Browser 抓真实模型卡 banner

**为什么这是子模块而不是独立 skill**: 配图提取 100% 服务于"写稿 → 嵌入"环节,集成让 AI 一次跑完流水线。

### 5. 写稿

只使用已核验来源中的事实。写成客观、信息密集的 AI 新闻聚合文档，用于快速查看当天 AI 大事；不要写第一人称体验、夸张排名、无来源价格、编造用户反馈或绝对化结论。

每条新闻写成一个完整段落：发生了什么 → 关键参数/数据 → 影响范围 → 开放方式/适用场景 → 限制或争议。

公众号、媒体早报、Google News、HN、B 站简介或评论只用于发现选题和定位链接，不能作为正文素材。正文不得出现”媒体称””视频里说””其他早报提到”等表述。

## 显式 B 站模式

只有用户给出 UP、空间链接、BV 号、视频链接，或明确要求从 B 站采集时，才运行：

```bash
python3 scripts/collect_bili_daily_hotspots.py --up <mid-or-space-url-or-name>
python3 scripts/collect_bili_daily_hotspots.py --video <bvid-or-video-url>
```

B 站脚本保留原有默认 UP、WBI、Cookie 和风控处理，但不要从通用采集失败自动切换到 B 站。B 站简介、评论和视频画面仍只是线索；事实与配图必须回到一手来源核验。

## 必须输出

完成核验后输出一个包含 7–10 条内容的 Markdown 聚合新闻文档；默认目标 8 条。

**产物按 `output/<YYYY-MM-DD>/` 目录组织**，包含 `candidates.json` / `web_candidates.json` / `evidence.json` / `verified.json` / `daily-report.md` / `images/`，便于复盘和断点续跑（完整目录规范见 `references/server-runtime.md`）。

```markdown
# AI 热点日报 · <YYYY-MM-DD>

> 采集范围：最近 <小时数> 小时
> 候选来源：<成功来源；失败来源另记 warnings>
> 采集时间：<时间和时区>

## 热点总览

| # | 发布时间 | 热点 | 来源 | 可信度 | 风险 |
|---|---|---|---|---|---|
| 1 | <时间> | <热点短标题> | <来源名> | 高/中/低 | <风险或”无”> |
| 2 | ... | ... | ... | ... | ... |

## 新闻详情

### 1. <完整热点标题>

<完整新闻段落：发生了什么、关键参数/数据、影响范围、开放方式、适用场景和限制。客观信息密集。>

![配图 alt](images/xxx.png)

**来源**：<官方 / 一手 URL>

---

### 2. <完整热点标题>

<完整新闻段落...>

![配图 alt](images/xxx.png)

**来源**：<官方 / 一手 URL>
```

显式 B 站模式可在文档头部追加来源 UP、视频、BV 号和视频地址；通用模式不要输出这些字段。

## 格式与边界

- 热点总览只用一张表，列名固定为 `#`、`发布时间`、`热点`、`来源`、`可信度`、`风险`。
- 新闻详情中每个热点单独一节，标题用 `### N. <完整标题>`（不限制字数，保留完整信息）。
- 默认日报只收录 7–10 条通过核验的热点；不足 7 条时允许少发，不得用噪声或未核实条目补齐。
- 正文写成完整新闻段落，包含关键参数/数据/影响/开放方式；不写第一人称体验。
- 图片用 `![alt](images/文件名)` 嵌入新闻详情段落中，不需要四行元数据。
- 没有合适图片时持续重试（换 clip / 换页面 / 换候选热点），直到拿到合格图；不要用第三方图顶替，也不要用空字符串占位。
- 只有存在未核实条目时才输出”需核实选题池”。
- 不自动发布，不保存 Cookie 或 token，不编造事实、链接、截图或授权状态。
- 在文末添加：`提示：内容由 AI 辅助整理，可能存在遗漏或错误，请以来源链接为准。`

## 交付前自检（必走，不能跳过）

写完 .md 后，**必须**逐条勾选，任一未过都得回头改：

- [ ] 7-10 条热点全部写齐，每条有完整新闻段落
- [ ] 热点总览 6 列对齐：`# / 发布时间 / 热点 / 来源 / 可信度 / 风险`
- [ ] **每条配图都 `![alt](images/xxx.png)` 真正嵌入，不是只写路径字符串**
- [ ] **每条热点都有合格配图，不存在无图条目（不允许用"无本地配图"等标注代替获取图片）**
- [ ] **直链能拿到合格图的用了 curl，拿不到或图不合格的已用 Browser 截图（不是跳过）**
- [ ] SPA 平台（OpenRouter / LMArena / HuggingFace Spaces）已**真用 Browser 截图**，**不是写"需用 Browser 截图"占位**
- [ ] **成稿后逐图做了 `images_understand` 内容审查**：每张图都和正文主题匹配、信息价值合格、文字可读；不合格的已删除并重新获取，直到合格
- [ ] 文末有免责声明
- [ ] 没出现"媒体称""视频里说""其他早报提到"等表述
- [ ] 可信度低的条目已在"风险"列标出
- [ ] 如果是 .md 文件交付，正文里所有图片路径都对应本地存在的图（可用 `ls images/` 核对）
- [ ] **正文中的每个参数/日期/数字都能在 `evidence.json` 对应条目的 `evidence_pages[].text_excerpt` 中找到出处**（事实一致性，防参数漂移）
- [ ] **已扫描候选池全量条目（含 `score<15` 的低分条目），无遗漏的重大事件**
- [ ] **🔴 每张配图的来源优先级正确(1 官方 / 2 权威英文媒体 / 3 中文媒体转载)**:任何降级到 3 的图,必须在正文或备注里写明降级原因。默认期望所有图都走优先级 1。

**自检不通过 = 任务未完成**。
