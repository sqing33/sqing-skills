# 配图提取工作流（asset-fetcher 子模块）

> `asset_fetcher` 子模块，集成在"配图提取 → 嵌入 Markdown"环节。
> 配套 CLI 工具：`scripts/asset_fetcher.py`（带 `curl` / `extract` / `list-cdns` / `screenshot` 子命令）

## 黄金顺序（按优先级尝试）

```
0. 复用 evidence.json image_suggestions（最高优先）→ 核验阶段已拿到的图片 URL，直接 curl
1. curl 直链（最高保真）       → curl 已知 CDN 上的原始资源
2. web_fetch + grep + curl     → 从发布页 HTML 找出图片 URL 再 curl
3. 内嵌 Browser 截图兜底        → 仅用于 SPA（OpenRouter / LMArena）
```

**Step 0 — 复用 evidence.json image_suggestions**：`verify_hotspots.py` 的 `evidence_pages[].image_suggestions[]` 已经带了页面图片 URL（`url` / `source_page` / `section_name` / `what_to_capture` / `rights_note`）。先遍历这些 suggestion 做信息价值判断，合格的直接 `asset_fetcher.py curl` 落盘，不合格的或为空的才进入 Step 1。**这样可以避免对已经核验过的页面重复 navigate 和 fetch。**

**永远不要直接对发布页整页截图**——那是"懒"图，往往是 logo + 装饰 + 留白。
**永远不要用 PIL 缩放**——会双重缩放糊掉。直接用 2× DPR 原图。

## 信息价值判断（必做）

拿到候选图后，**先判断它有没有"关于这个产品的内在信息"**：

| ✅ 合格的图 | ❌ 不合格的图 |
|---|---|
| 官方架构图（含技术参数） | HF 模型卡页面外壳（只有徽章 + pip install 代码） |
| 性能对比表 / benchmark 图 | 公司 logo / brand 装饰 |
| 真实 demo 视频截图 | 平台统一的 "Like / Follow" UI |
| 复杂数据可视化（排行榜 / 趋势图） | 留白为主的 hero 区域 |
| 官方 banner（含版本号 + 发布日期） | 同主题但信息密度低的"宣传图" |

## 主流平台图床速查

详细见 `asset_fetcher.py` 里的 `KNOWN_CDNS` 字典和 `list-cdns` 子命令的输出。

### huggingface.co（HuggingFace 模型仓库）

- **发现方法**: `web_fetch` 仓库主页 → 拿 README.md → grep `![...](assets/...png)` → curl
- **URL pattern**: `https://huggingface.co/{org}/{repo}/resolve/main/{path}`
- **坑**: DOM query `img` 在 HF 仓库页**返回空**（shadow DOM / 延迟加载），必须用 `web_fetch` raw README

### cdn.sanity.io（Anthropic News 等）

- **发现方法**: `web_fetch` 文章页 → 拿 HTML → grep `_next/image?url=https%3A%2F%2Fcdn.sanity.io...` → URL decode → curl
- **坑**: `_next/image?url=...` query string 是 Next.js 缩放过的；去掉 query 拿原图才是 3840×2160

### qqpublic.qpic.cn（微信公众号图床）

- **关键发现**: 公众号原文 URL（`mp.weixin.qq.com/s/...`）**无法被搜索引擎索引**
- **发现方法**:
  1. `web_search` 找媒体转载（IT之家 / 潮新闻 / 企鹅号 / CSDN）
  2. `web_fetch` 转载页 HTML
  3. grep `qqpublic.qpic.cn/qq_public/...` 拿到直链
  4. curl 这个直链
- **权威性识别**: 图片截屏左下角会有公众号 logo（例如「字节跳动 Seed」+ "已关注"）
- **URL pattern**: `http://qqpublic.qpic.cn/qq_public/{bucket}/{hash}/0?fmt={png|jpg}&size={size}&h={h}&w={w}&ppv={ver}`

### www-cdn.anthropic.com（Anthropic 自有 CDN）

- 同 sanity.io 模式，URL decode 后的 `_next/image?url=` 后面就是直链

### 平台级 SPA（需浏览器截图）

- `openrouter.ai/rankings` — 排行榜
- `arena.ai/leaderboard` — LMArena
- `huggingface.co/spaces/...` — HF Spaces

`web_fetch` 拿到的都是空 shell，**用内置 `control-in-app-browser` skill 截图**：
1. `skill({ name: "control-in-app-browser" })` 加载浏览器 skill
2. `browser navigate "https://..."` 打开页面
3. `browser inspect` 找到目标区块
4. 必要时 `browser click` 切 tab / 关闭 cookie 弹窗
5. `browser screenshot --clip <x,y,w,h> --output images/<name>.png` 截图
6. `images_understand images/<name>.png` 验证内容

## 完整工作流示例（huggingface.co）

```bash
# 1. fetch README
web_fetch("https://huggingface.co/MiniMaxAI/H3-Base-FL2VA") > /tmp/h3_readme.md

# 2. 提取所有 image URL
python3 scripts/asset_fetcher.py extract --file /tmp/h3_readme.md

# 输出:
# [huggingface-assets] huggingface.co
#   https://huggingface.co/MiniMaxAI/H3-Base-FL2VA/resolve/main/assets/minimax-h3.png
# [huggingface-assets] huggingface.co
#   https://huggingface.co/MiniMaxAI/H3-Base-FL2VA/resolve/main/assets/overview.png
# ...

# 3. curl 一张
python3 scripts/asset_fetcher.py curl \
  --url "https://huggingface.co/MiniMaxAI/H3-Base-FL2VA/resolve/main/assets/minimax-h3.png" \
  --output images/h3-banner.png
```

## 完整工作流示例（微信公众号）

```bash
# 1. 搜索媒体转载
web_search("Seedance 2.5 字节跳动Seed 公众号 原文")

# 2. 拿转载页 HTML
web_fetch("https://so.html5.qq.com/page/real/search_news?docid=70000021_7146a6c5b0a31852") \
  > /tmp/seedance_forward.html

# 3. 提取公众号直链
python3 scripts/asset_fetcher.py extract --file /tmp/seedance_forward.html

# 输出:
# [wechat-bed] qqpublic.qpic.cn
#   http://qqpublic.qpic.cn/qq_public/0/28-1843972034-3B99DCCDEF124F9FE83D40039A1A19BE/0?fmt=png&...

# 4. curl
python3 scripts/asset_fetcher.py curl \
  --url "http://qqpublic.qpic.cn/qq_public/0/28-1843972034-.../0?fmt=png&..." \
  --output images/seedance-official.png
```

## 踩坑笔记

- **macOS 没有 GNU `timeout`** — `asset_fetcher.py curl` 已带 `--max-time` 兜底；或 `brew install coreutils` 用 `gtimeout`
- **`web_fetch` 拿 SSR React 页 = 空 shell** — 切内嵌 Browser
- **微信公众号原文 URL 不可索引** — 走"媒体转载 → grep 直链"绕道
- **HF 仓库页 DOM `img` query 返回空** — 必须 `web_fetch` raw README
- **浏览器 cookie 弹窗挡数据** — 截图前先 click dismiss，或用 clip 绕开
- **不要 PIL 缩放** — 直接拿 2× DPR 原图；想"压缩"改 jpg q=95 + sips
- **PNG optimize 在大图上会 hang** — 改用 sips 转 jpg，或直接无损 PNG

## 为什么作为子模块集成在 skill 内

配图提取 100% 服务于"AI 热点日报"流程的"写稿 → 嵌入"环节。单独拆 skill 反而割裂了工作流（caller 要手动选 skill + 切上下文）。集成进来让 AI 一次跑完整个日报流水线：

```
collect_ai_hotspots.py  →  候选池
           ↓
      AI 选题 + 核验
           ↓
     写初稿（无图）
           ↓
   asset_fetcher.py   ←  本子模块
     curl / extract / screenshot
           ↓
   嵌入 Markdown 图片语法
           ↓
     最终成稿
```

每篇报道配图是 1-5 张，所以**总图数 = 报道数 × 1~5**，对一个 12-18 条的日报是 12-90 张图，asset_fetcher 必须有高性能批量能力（虽然现在子命令是单条，但内部 AI 会在循环里调用）。
