#!/usr/bin/env python3
"""
asset-fetcher — 为 AI 模型/产品报道提取首手配图

集成在 ai-daily-hotspot-xhs skill 的"配图"环节。支持三种工作流（按优先级）:

  1. curl 直接下载公开 CDN/图床上的高分辨率原始资源
  2. web_fetch 抓 HTML/JSON/Markdown，从内嵌资源中提取图片 URL 再 curl
  3. 浏览器截图（控制内嵌 Browser）兜底：仅用于无原始 URL 但页面区域本身有信息价值的情况

不负责:
  - 内容筛选 / 选题 / 评估图片"是否有信息价值" — 这是 AI 的事
  - 写文章 / 嵌入文章 — caller 自己处理
  - OCR / 二次编辑

CLI 入口:
  asset_fetcher.py curl --url "https://..." --output images/xxx.png
  asset_fetcher.py extract --file web_fetch_output.html        # 提取所有图 URL
  asset_fetcher.py list-cdns                                  # 列出已知平台图床规则
  asset_fetcher.py screenshot --url "https://openrouter.ai/rankings"  # 输出浏览器操作建议

依赖:
  - python3 (标准库即可,无第三方依赖)
  - bash + curl (Linux/macOS)
  - 内嵌 Browser 截图走 Mavis control-in-app-browser skill
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote


# =============================================================================
# 已知 AI 平台的图床/资源 CDN 规则
# 详细文档见 references/image-extraction.md
# =============================================================================
KNOWN_CDNS = {
    "huggingface.co": {
        "type": "git-lfs",
        "raw_pattern": "https://huggingface.co/{org}/{repo}/resolve/main/{path}",
        "how_to_discover": (
            "fetch README.md via web_fetch; grep for 'assets/...png|.jpg'; "
            "blob/main -> resolve/main"
        ),
        "example": "https://huggingface.co/MiniMaxAI/H3-Base-FL2VA/resolve/main/assets/minimax-h3.png",
    },
    "cdn.sanity.io": {
        "type": "image-cdn",
        "raw_pattern": "https://cdn.sanity.io/images/{projectId}/{dataset}/{path}",
        "how_to_discover": (
            "fetch article HTML, grep for 'cdn.sanity.io' img src; "
            "对 Next.js 页面是 _next/image?url=ENCODED_URL,URL decode 拿到直链"
        ),
        "example": "https://cdn.sanity.io/images/4zrzovbb/website/b5e071ba6a9ce5628b4662f05484d1806a9fdc94-3840x2160.png",
    },
    "qqpublic.qpic.cn": {
        "type": "wechat-image-bed",
        "raw_pattern": "http://qqpublic.qpic.cn/qq_public/{bucket}/{hash}/0?fmt={fmt}&size={size}&h={h}&w={w}&ppv={ppv}",
        "how_to_discover": (
            "公众号原文 URL 不可索引;通过 web_search 找媒体转载(IT之家/潮新闻/企鹅号/CSDN),"
            "web_fetch 转载页 HTML,grep qqpublic.qpic.cn/qq_public/... 拿到直链"
        ),
        "example": "http://qqpublic.qpic.cn/qq_public/0/28-3505681457-C415AA0CBE9E87F491B2987862A29FDB/0?fmt=png&size=914&h=810&w=1440&ppv=1",
        "note": "这是腾讯图床,微信公众号原文图片都走这里。",
    },
    "www-cdn.anthropic.com": {
        "type": "anthropic-assets",
        "raw_pattern": "https://www-cdn.anthropic.com/images/{projectId}/website/{filename}",
        "how_to_discover": (
            "fetch anthropic.com/news/<post>, grep for '_next/image?url=https%3A%2F%2Fwww-cdn.anthropic.com' "
            "in img srcSet; URL decode"
        ),
        "example": "https://www-cdn.anthropic.com/images/4zrzovbb/website/54b7ab1d2c2521f83ae5d2da5f9d99321c370d24-2880x1620.png",
    },
    "openrouter.ai": {
        "type": "ssr-react",
        "how_to_discover": "页面是 JS-rendered,web_fetch 拿到的是空 shell。用 Mavis 内嵌 Browser inspect + 截图区域",
        "example": "https://openrouter.ai/rankings",
    },
    "arena.ai": {
        "type": "ssr-react",
        "note": "lmarena.ai 重定向到 arena.ai",
        "how_to_discover": "同上,用 Mavis 内嵌 Browser",
        "example": "https://arena.ai/leaderboard/text",
    },
}


# =============================================================================
# Workflow 1: 直接 curl 下载
# =============================================================================
def cmd_curl(args):
    """下载一个 URL 到 output 路径。"""
    if not args.url:
        sys.exit("Error: --url required")
    if not args.output:
        sys.exit("Error: --output required")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "curl", "-sL",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "-o", str(output),
        "-w", "HTTP: %{http_code}\\nSize: %{size_download}\\n",
        "--max-time", str(args.max_time),
        args.url,
    ]

    print(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="", file=sys.stderr)
        sys.exit(f"curl failed (exit {result.returncode})")

    if not output.exists() or output.stat().st_size < 1024:
        sys.exit(f"Error: output {output} missing or suspiciously small")

    file_type = subprocess.run(
        ["file", str(output)], capture_output=True, text=True
    ).stdout.strip()
    print(f"Type: {file_type}")

    if "HTML" in file_type and "PNG" not in file_type and "JPEG" not in file_type:
        sys.exit(f"Error: downloaded file is HTML, not an image. URL wrong?")

    print(f"✓ Saved to {output} ({output.stat().st_size:,} bytes)")


# =============================================================================
# Workflow 2: 从 HTML / Markdown 提取图片 URL
# =============================================================================
def cmd_extract(args):
    """从 stdin 或 file 中提取所有图片 URL。

    支持的 pattern:
      1. <img src=...>
      2. srcSet=... (Next.js / Sanity)
      3. ![](url) (Markdown)
      4. 裸 https://...png|jpg|jpeg|webp|gif URL
      5. 微信公众号图床 qqpublic.qpic.cn
      6. HuggingFace assets/ 路径

    输出 JSON 数组 [{url, type, host, raw_match}, ...]
    """
    if args.file:
        text = Path(args.file).read_text()
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        sys.exit("Error: provide --file or pipe text via stdin")

    urls = []

    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', text):
        urls.append({"url": unquote(m.group(1)), "type": "img-src", "raw": m.group(0)[:200]})

    for m in re.finditer(r'srcSet=["\']([^"\']+)["\']', text):
        for u in m.group(1).split(","):
            u = u.strip().split(" ")[0]
            if u:
                urls.append({"url": unquote(u), "type": "srcset", "raw": u})

    for m in re.finditer(r'!\[[^\]]*\]\(([^)]+)\)', text):
        urls.append({"url": m.group(1), "type": "markdown", "raw": m.group(0)[:200]})

    for m in re.finditer(
        r'https?://[^\s"\'<>)]+\.(?:png|jpg|jpeg|webp|gif)(?:\?[^\s"\'<>)]*)?',
        text,
        re.IGNORECASE,
    ):
        urls.append({"url": m.group(0), "type": "bare-url", "raw": m.group(0)})

    for m in re.finditer(r"https?://qqpublic\.qpic\.cn/[^\s\"'<>)]+", text):
        urls.append({"url": m.group(0), "type": "wechat-bed", "raw": m.group(0)})

    for m in re.finditer(
        r'["\'](https?://[^\s"\'<>]+?/assets/[^\s"\'<>]+\.(?:png|jpg|jpeg|webp))["\']',
        text,
        re.IGNORECASE,
    ):
        urls.append({"url": m.group(1), "type": "huggingface-assets", "raw": m.group(0)})

    seen = set()
    unique = []
    for u in urls:
        if u["url"] not in seen:
            seen.add(u["url"])
            u["host"] = urlparse(u["url"]).netloc
            unique.append(u)

    if args.format == "json":
        print(json.dumps(unique, indent=2, ensure_ascii=False))
    else:
        for u in unique:
            print(f"[{u['type']}] {u['host']}")
            print(f"  {u['url']}")


# =============================================================================
# Workflow 3: 列出已知图床规则
# =============================================================================
def cmd_list_cdns(args):
    print("已知平台的图床/资源 CDN 规则:\n")
    for host, info in KNOWN_CDNS.items():
        print(f"## {host}")
        print(f"  type: {info.get('type')}")
        if "raw_pattern" in info:
            print(f"  raw_pattern: {info['raw_pattern']}")
        print(f"  how_to_discover: {info.get('how_to_discover')}")
        if "example" in info:
            print(f"  example: {info['example']}")
        if "note" in info:
            print(f"  NOTE: {info['note']}")
        print()


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="asset-fetcher — 提取 AI 模型/产品报道的首手配图 (ai-daily-news 子模块)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_curl = sub.add_parser("curl", help="直接下载一个 URL 到 output 路径")
    p_curl.add_argument("--url", required=True)
    p_curl.add_argument("--output", required=True)
    p_curl.add_argument("--max-time", type=int, default=30)
    p_curl.set_defaults(func=cmd_curl)

    p_extract = sub.add_parser("extract", help="从 HTML/Markdown 提取图片 URL")
    p_extract.add_argument("--file", help="本地 HTML/Markdown 文件")
    p_extract.add_argument("--format", choices=["json", "text"], default="text")
    p_extract.set_defaults(func=cmd_extract)

    p_list = sub.add_parser("list-cdns", help="列出已知 AI 平台图床规则")
    p_list.set_defaults(func=cmd_list_cdns)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
