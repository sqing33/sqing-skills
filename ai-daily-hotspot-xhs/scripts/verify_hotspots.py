#!/usr/bin/env python3
"""Resolve and inspect AI hotspot sources using HTTP-only extraction.

The script gathers evidence candidates (title, description, text excerpt,
image suggestions) via standard-library HTTP requests. It never launches a
browser — JavaScript pages, SPA screenshots and interactive navigation are
handled by the AI agent using the ``control-in-app-browser`` skill.

The script never declares a claim verified; evidence always requires
claim-by-claim review before publication.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_MS = 20_000
MAX_HTML_BYTES = 3 * 1024 * 1024
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
PRIMARY_DOMAINS = {
    "ai.google.dev",
    "ai.meta.com",
    "anthropic.com",
    "apple.com",
    "arxiv.org",
    "blog.google",
    "deepmind.google",
    "github.com",
    "github.blog",
    "gradium.ai",
    "huggingface.co",
    "microsoft.com",
    "nvidia.com",
    "openai.com",
    "sensetime.com",
    "sensetime.com.cn",
}
SECONDARY_DOMAINS = {
    "bloomberg.com",
    "reuters.com",
    "slator.com",
    "techcrunch.com",
    "the-decoder.com",
    "thenewstack.io",
    "theverge.com",
    "wired.com",
    "yahoo.com",
}
EXCLUDED_SEARCH_DOMAINS = {
    "bing.com",
    "google.com",
    "news.google.com",
    "search.yahoo.com",
}
ENTITY_DOMAIN_HINTS = {
    "anthropic": "anthropic.com",
    "claude": "anthropic.com",
    "deepmind": "deepmind.google",
    "gemini": "blog.google",
    "github": "github.com",
    "google": "blog.google",
    "hugging face": "huggingface.co",
    "meta": "about.fb.com",
    "nvidia": "nvidia.com",
    "openai": "openai.com",
    "sensenova": "sensetime.com",
    "商汤": "sensetime.com",
}
SEARCH_TOKEN_STOPWORDS = {
    "backs", "built", "company", "external", "feature", "hardware", "here", "image", "model", "newest",
    "official", "over", "people", "photos", "race", "signals", "trade", "training", "users", "voice",
}


class VerifyError(RuntimeError):
    pass


class Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.images: list[dict[str, str]] = []
        self.description = ""
        self.og_image = ""
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key in {"description", "og:description", "twitter:description"} and not self.description:
                self.description = content
            if key in {"og:image", "twitter:image"} and not self.og_image:
                self.og_image = content
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "img":
            src = values.get("src") or values.get("data-src") or ""
            if src:
                self.images.append({"url": src, "alt": values.get("alt", "")})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if not self._skip_depth:
            self.text_parts.append(text)


@dataclass
class PageEvidence:
    requested_url: str
    final_url: str = ""
    title: str = ""
    description: str = ""
    text_excerpt: str = ""
    source_level: str = "unknown"
    fetch_method: str = ""
    image_suggestions: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None


def host_for(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def domain_matches(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def source_level(url: str) -> str:
    host = host_for(url)
    if domain_matches(host, PRIMARY_DOMAINS):
        return "primary_candidate"
    if domain_matches(host, SECONDARY_DOMAINS):
        return "strong_secondary"
    if domain_matches(host, EXCLUDED_SEARCH_DOMAINS):
        return "aggregator"
    return "unknown"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def absolute_image_url(page_url: str, image_url: str) -> str:
    if image_url.startswith("//"):
        return "https:" + image_url
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    parsed = urlparse(page_url)
    if image_url.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{image_url}"
    base = page_url.rsplit("/", 1)[0]
    return f"{base}/{image_url}"


def evidence_from_html(requested_url: str, final_url: str, raw: str, method: str) -> PageEvidence:
    parser = Extractor()
    parser.feed(raw)
    title = normalize_text(" ".join(parser.title_parts))
    text = normalize_text(" ".join(parser.text_parts))
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()
    candidates = []
    if parser.og_image:
        candidates.append({"url": parser.og_image, "alt": "Open Graph image", "section": "页面首图"})
    candidates.extend({"url": row["url"], "alt": row["alt"], "section": row["alt"] or "正文图片"} for row in parser.images[:12])
    for row in candidates:
        url = absolute_image_url(final_url, row["url"])
        if not url or url in seen or url.startswith("data:"):
            continue
        seen.add(url)
        suggestions.append(
            {
                "url": url,
                "source_page": final_url,
                "section_name": row["section"],
                "what_to_capture": row["alt"] or "检查该官方页面图片是否能说明核心事实",
                "rights_note": "需人工确认版权/授权；优先使用官方页面截图。",
            }
        )
        if len(suggestions) >= 5:
            break
    return PageEvidence(
        requested_url=requested_url,
        final_url=final_url,
        title=title,
        description=normalize_text(parser.description),
        text_excerpt=text[:2400],
        source_level=source_level(final_url),
        fetch_method=method,
        image_suggestions=suggestions,
    )


def http_extract(url: str, timeout_ms: int) -> PageEvidence:
    request = Request(
        url,
        headers={
            "User-Agent": os.environ.get("AI_HOTSPOT_USER_AGENT", DEFAULT_UA),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urlopen(request, timeout=timeout_ms / 1000) as response:
            raw = response.read(MAX_HTML_BYTES + 1)
            if len(raw) > MAX_HTML_BYTES:
                raise VerifyError(f"response exceeds {MAX_HTML_BYTES} bytes")
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return evidence_from_html(url, response.geturl(), text, "http")
    except (HTTPError, URLError, TimeoutError, OSError, VerifyError) as exc:
        return PageEvidence(requested_url=url, error=str(exc), fetch_method="http")


def search_result_score(url: str, title: str, query_title: str) -> float:
    level = source_level(url)
    base = {"primary_candidate": 100, "strong_secondary": 45, "unknown": 10, "aggregator": -100}.get(level, 0)
    query_tokens = {token for token in re.findall(r"[A-Za-z0-9-]{3,}|[\u3400-\u9fff]{2,}", query_title.casefold())}
    title_tokens = {token for token in re.findall(r"[A-Za-z0-9-]{3,}|[\u3400-\u9fff]{2,}", title.casefold())}
    overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens))
    return base + overlap * 30


def build_search_queries(title: str) -> list[str]:
    lowered = title.casefold()
    tokens = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}", title):
        normalized = token.casefold().strip(".-")
        if normalized in SEARCH_TOKEN_STOPWORDS or normalized in tokens:
            continue
        tokens.append(normalized)
    terms = " ".join(tokens[:4]) or title
    queries = []
    for marker, domain in ENTITY_DOMAIN_HINTS.items():
        if marker in lowered:
            queries.append(f"site:{domain} {terms}")
            break
    queries.append(f"{terms} official GitHub Hugging Face arXiv")
    return queries


def brave_search(title: str, timeout_ms: int, result_limit: int) -> list[dict[str, str]]:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return []
    rows: list[dict[str, str]] = []
    for query in build_search_queries(title):
        url = "https://api.search.brave.com/res/v1/web/search?q=" + quote_plus(query) + f"&count={max(5, result_limit)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
                "User-Agent": os.environ.get("AI_HOTSPOT_USER_AGENT", DEFAULT_UA),
            },
        )
        try:
            with urlopen(request, timeout=timeout_ms / 1000) as response:
                payload = json.loads(response.read(MAX_HTML_BYTES).decode("utf-8", errors="replace"))
        except Exception:
            continue
        for row in ((payload.get("web") or {}).get("results") or []):
            if row.get("url"):
                rows.append({"url": row["url"], "title": row.get("title") or ""})
    return rank_search_rows(rows, title, result_limit)


def rank_search_rows(rows: list[dict[str, str]], title: str, result_limit: int) -> list[dict[str, str]]:
    cleaned = []
    seen = set()
    for row in rows:
        candidate_url = row.get("url", "")
        if not candidate_url.startswith("http") or candidate_url in seen:
            continue
        if source_level(candidate_url) == "aggregator":
            continue
        seen.add(candidate_url)
        cleaned.append(row)
    cleaned.sort(key=lambda row: search_result_score(row["url"], row.get("title", ""), title), reverse=True)
    return cleaned[:result_limit]


def load_payload(path: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifyError(f"could not load collector JSON: {exc}") from exc
    items_source = payload.get("candidates") or payload.get("shortlist")
    if not isinstance(items_source, list):
        raise VerifyError("collector JSON has no candidates/shortlist list")
    return payload


def verify_item(
    item: dict[str, Any],
    timeout_ms: int,
    result_limit: int,
    no_search: bool,
) -> dict[str, Any]:
    """Resolve a single candidate via HTTP and optional Brave search."""
    direct = http_extract(item["url"], timeout_ms)
    search_rows: list[dict[str, str]] = []
    if not no_search and direct.source_level != "primary_candidate":
        search_rows = brave_search(item["title"], timeout_ms, result_limit)
    evidence_pages: list[PageEvidence] = [direct]
    for row in search_rows:
        evidence = http_extract(row["url"], timeout_ms)
        evidence_pages.append(evidence)
    levels = {page.source_level for page in evidence_pages if not page.error}
    status = "primary_candidate_found" if "primary_candidate" in levels else (
        "secondary_only" if "strong_secondary" in levels else "insufficient_evidence"
    )
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "candidate_url": item.get("url"),
        "published_at": item.get("published_at"),
        "category": item.get("category"),
        "evidence_status": status,
        "evidence_pages": [asdict(row) for row in evidence_pages],
        "warning": "Evidence candidates require claim-by-claim human or agent review before publication.",
    }


def run(args: argparse.Namespace) -> int:
    payload = load_payload(args.input)
    items_source = payload.get("candidates") or payload.get("shortlist") or []
    items = items_source[: args.max_items]
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        results.append(
            verify_item(
                item,
                args.timeout_ms,
                args.search_results,
                args.no_search,
            )
        )
    output = {
        "generated_at": payload.get("collected_at"),
        "input_candidate_count": len(payload.get("candidates") or payload.get("shortlist") or []),
        "processed_count": len(results),
        "extraction_method": "http",
        "search_provider": "brave_api" if os.environ.get("BRAVE_SEARCH_API_KEY") else "none",
        "results": results,
        "warning": (
            "primary_candidate_found means a likely first-party page was found, not that every claim is verified. "
            "JavaScript-rendered pages and screenshots are handled by the control-in-app-browser skill."
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="Collector JSON path, or - for stdin (default).")
    parser.add_argument("--max-items", type=int, default=12)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--search-results", type=int, default=2)
    parser.add_argument("--no-search", action="store_true", help="Resolve candidate pages without search discovery.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_items <= 0 or args.timeout_ms <= 0 or args.search_results < 0:
        raise SystemExit("numeric arguments must be positive; --search-results may be zero")
    try:
        return run(args)
    except VerifyError as exc:
        print(json.dumps({"results": [], "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
