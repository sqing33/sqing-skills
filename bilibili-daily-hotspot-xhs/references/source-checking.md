# Source Checking and Material Rules

Use this reference after collecting hotspots from Bilibili pinned comments.

## Search Channels and Tools

Use channels in this order:

1. Codex/web search tool
   - Use the built-in web search capability when available.
   - Search for fact sources first; use image search only after the fact source is found.
   - Open candidate pages and read the original page before accepting a source.

2. Official-site targeted search
   - Use `site:` queries against likely primary domains.
   - Common domains:
     - NVIDIA: `site:nvidia.com <product>`
     - OpenAI: `site:openai.com <product>` or `site:platform.openai.com <feature>`
     - Anthropic: `site:anthropic.com <topic>`
     - Google: `site:blog.google <product>` or `site:deepmind.google <product>`
     - GitHub/Copilot: `site:github.blog <feature>` or `site:github.com/features/copilot <feature>`
     - ByteDance/research projects: `site:github.com bytedance <project>`, `site:arxiv.org <project>`, `site:bytedance.com <project>`
     - Hugging Face models: `site:huggingface.co <model>`

3. Repository/model/paper search
   - Search GitHub by repo/project name.
   - Search Hugging Face for model names.
   - Search arXiv/Semantic Scholar for paper-like announcements.
   - Use GitHub README, release pages, and model cards as evidence only when they confirm the exact claim.

4. General search engines and APIs
   - Use Google, Bing, Brave, Tavily, Exa, or SerpAPI if the runtime provides them.
   - Query in English for global AI companies and in Chinese for domestic companies.
   - Treat media/community results as secondary unless they link to a primary source.

5. Browser/page inspection
   - Use browser/open tools to inspect official pages, project pages, GitHub README, docs, papers, model cards, and changelogs.
   - Do not stop at "project page" or "GitHub README". Open the page and list exact usable material blocks.
   - For image leads, identify the exact section to screenshot instead of downloading random images.
   - Record `page_url`, `section_name`, `asset_type`, `what_to_capture`, and `why_useful`.
   - Before taking screenshots, resize the browser/headless viewport to at least `1600x1200` or another wide desktop size.
   - Prefer section screenshots or direct official image assets for diagrams; use full-page screenshots only when the page layout is short enough to remain readable.
   - Close cookie banners, popups, and sticky overlays before saving screenshots.
- After saving each screenshot, inspect it or check it visually. If it is blocked by a banner, too narrow, blank, or missing the target block, retake it.
- If a good screenshot cannot be produced quickly, do not over-spend time. Output the page URL, exact section name, and manual screenshot instruction so the user can capture it by hand.

Do not use Bilibili pinned comments, Bilibili video frames, chapter screenshots, video covers, or UP-created summary cards as final factual sources. They are only leads.

## Source Priority

1. Primary sources: official product blogs, docs, changelogs, GitHub repos/releases/issues, papers, model cards, company announcements, official social accounts.
2. Strong secondary sources: reputable tech media, conference pages, standards bodies, package registries, app store pages.
3. Context-only sources: community comments, Reddit, X posts, forum discussions, Bilibili/YouTube commentary.

For every post-ready hotspot, include at least one source that confirms the core claim. If a source only proves background context, label it as context and keep the claim cautious.

## Search Pattern

- Extract entity names, product names, repo names, model names, dates, and verbs from the hotspot.
- Search both Chinese and English when the entity is international.
- Generate these queries in order:
  - `<entity> <product/project/model> official`
  - `<entity> <product/project/model> release`
  - `<entity> <product/project/model> blog`
  - `<entity> <product/project/model> GitHub`
  - `<entity> <product/project/model> arxiv`
  - `<Chinese raw hotspot claim>`
  - `site:<likely-official-domain> <product/project/model>`

Examples:

```text
NVIDIA Nemotron 3 Ultra official
NVIDIA Nemotron 3 Ultra release
NVIDIA Nemotron 3 Ultra GitHub
site:nvidia.com Nemotron 3 Ultra
site:huggingface.co Nemotron 3 Ultra

ByteDance Bernini unified video generation editing framework
ByteDance Bernini GitHub
Bernini arxiv video generation editing
site:github.com bytedance Bernini
site:arxiv.org Bernini video generation

GitHub Copilot million token context official
GitHub Copilot configurable reasoning levels
site:github.blog Copilot million context
site:github.blog Copilot reasoning levels
```

## Credibility Labels

- `high`: primary source confirms the claim.
- `medium`: reliable secondary source confirms it, primary source missing or incomplete.
- `low`: only social/community sources found.
- `needs_verification`: no source confirms the claim or the source contradicts the pinned comment.

Only `high` and `medium` items should become full Xiaohongshu posts. `low` can be used as a discussion prompt with careful wording. `needs_verification` stays in the option pool.

## Image and Screenshot Leads

Prefer:

- Official product screenshots, launch-page images, docs diagrams, demo pages.
- Project-page demo sections, framework/architecture diagrams, benchmark tables, comparison charts, embedded demo videos, and example galleries.
- GitHub repo social preview, README screenshots, release assets.
- Paper figures when the paper page or PDF is clearly the source.

Do not use:

- Bilibili video screenshots, chapter keyframes, video covers, or UP-created information cards as Markdown post images.
- Bilibili/YouTube commentary screenshots as substitutes for official screenshots, diagrams, benchmark tables, model cards, product pages, or media source pages.
- Any image that only proves the UP covered the story, rather than proving the external fact.

If no suitable external image can be captured, include the verified source links and a short manual screenshot instruction for the official/media page instead of using a Bilibili image.

For each opened source page, produce a material inventory:

```text
页面：
可截图区块：
- 区块名称：
  素材类型：首屏 / 架构图 / Demo / benchmark / 代码入口 / 模型卡 / 论文图
  截图内容：
  适合用途：
  版权备注：
```

## Screenshot Quality Rules

When saving images for Markdown output:

- Use a wide viewport, ideally `1600x1200` or larger, before capture.
- For diagrams such as framework charts, download the official image asset when available and embed that instead of a viewport screenshot.
- If the source page contains charts, benchmark tables, comparison tables, architecture diagrams, or demo galleries, prioritize those over generic hero screenshots.
- If one source page contains multiple useful charts, capture multiple images rather than compressing them into one vague page screenshot.
- For demo pages, capture the exact section containing examples, not just the hero page.
- For pages with cookie dialogs or lazy loading, close dialogs and scroll to trigger loading before capture.
- If the in-app browser screenshot tool is unstable, too narrow, or times out, use Playwright/headless Chromium with a wide viewport to capture the real page section.
- Put images at the end of each generated post unless the user asks to interleave them with text.
- If the user asks for a clean publishing draft, output only `title + body + tags + images + related links`; omit source tables, risks, and internal notes.
- Use this fixed format for each standalone post: `标题：...`, `正文：...` followed by tags, then images, then `相关链接：`.
- Titles should preserve the entity, product/project/model name, and action. They may exceed 20 visible characters when needed for clarity.
- Do not add collection tags such as `#AI早报`; tags should match the individual hotspot.
- If screenshots are missing or imperfect, still include `related links` and, when useful, add a short manual capture note after the link.

Example for Bernini:

```text
页面：https://bernini-ai.github.io/
可截图区块：
- Hero + Demo Video
  素材类型：项目首屏 / 视频 Demo
  截图内容：标题、作者团队、Paper/Code/Model 按钮、嵌入式 Demo Video
  适合用途：证明项目身份和官方入口
- V2V / RV2V / VV2V / R2V examples
  素材类型：能力 Demo
  截图内容：视频编辑、参考图编辑、内容插入、参考图生成视频案例
  适合用途：展示 Bernini 支持的任务类型
- Framework
  素材类型：架构图
  截图内容：assets/framework.png，MLLM-based semantic planner + DiT-based renderer
  适合用途：解释“统一视频生成与编辑框架”的技术结构
```

Do not:

- Present copyrighted article images as reusable assets without caveat.
- Scrape/paywall images.
- Invent screenshots.
- Claim authorization when only a URL was found.

Output material leads as `image_suggestions` with `url`, `source_page`, `what_to_capture`, and `rights_note`.

## Writing Boundaries

- Replace "officially confirmed as best/first/strongest" with sourced, narrow statements.
- Avoid exact prices, rankings, deadlines, usage numbers, benchmark scores, and user quotes unless the source directly provides them.
- Default to news-brief writing, not KOC/light-review writing.
- Use KOC/first-person writing only when the user explicitly asks for it.
- If a claim is volatile, include the source date or publication date in the internal notes.
- Do not mention Bilibili as the evidence source in publishing copy. Avoid phrases such as "B 站视频提到", "视频信息卡显示", "UP 主称", "视频里说", or "据该视频". Rewrite the item around the verified external source: official blog/docs/GitHub/paper/company announcement/reputable media.

## News Brief Writing Style

Default output should resemble an AI daily/news brief:

1. Overview section
   - Group hotspots by category such as `要闻`, `模型发布`, `开发生态`, `产品应用`, `技术与洞察`, `行业动态`, `前瞻与传闻`.
   - Each item is one concise headline plus an index number.

2. Item body
   - Start with a one-sentence lead that states the core event.
   - Follow with one or two dense paragraphs covering:
     - who released/announced it
     - what changed
     - key technical details or parameters
     - availability/open-source/license/API/platform status
     - benchmark or claim source, if any
     - caveats or uncertainty
   - End with source links.

3. Tone
   - Objective, compact, information-first.
   - Avoid "我觉得", "我试了下", "适合谁" unless the user asks for commentary.
   - Avoid clickbait and Xiaohongshu-style first-person hooks by default.

4. Disclosure
   - If AI helped summarize sources, add a short note: `提示：内容由 AI 辅助整理，可能存在遗漏或错误，请以来源链接为准。`
