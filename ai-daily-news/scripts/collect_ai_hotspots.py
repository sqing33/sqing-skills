#!/usr/bin/env python3
"""Collect, normalize, and deduplicate AI hotspot candidates.

The script ONLY collects, normalizes, and deduplicates candidates from public
RSS, Atom, and JSON endpoints. All content filtering, scoring, prioritization,
and shortlisting is left entirely to the AI analyst. The script does NOT:
  - filter noise candidates
  - apply source/category caps
  - select a diverse subset
  - pick a shortlist

It outputs every merged candidate sorted by freshness (newest first).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


DEFAULT_HOURS = 24
DEFAULT_LIMIT = 200
DEFAULT_TIMEOUT = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_UA = "ai-daily-news/1.0 (+public RSS and JSON collector)"
SOURCE_CATALOG = Path(__file__).resolve().parent.parent / "references" / "sources.json"
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "oc",
    "ref",
    "source",
}


class CollectorError(RuntimeError):
    """Raised for source, parsing, or configuration failures."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    format: str
    language: str
    categories: tuple[str, ...]
    tier: str
    weight: float
    enabled: bool
    parser: str | None = None
    disabled_reason: str | None = None
    freshness_mode: str = "published"
    minimum_stars: int = 0


@dataclass
class Candidate:
    title: str
    url: str
    published_at: str | None
    source_name: str
    source_kind: str
    source_tier: str
    language: str
    category: str
    summary: str
    signals: dict[str, Any]
    score: float = 0.0
    duplicate_sources: list[str] = field(default_factory=list)
    verification_status: str = "unverified"
    source_id: str = ""
    source_weight: float = 0.0
    freshness_at: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("source_id", None)
        data.pop("source_weight", None)
        data.pop("freshness_at", None)
        return data


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def strip_markup(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(value: Any, source_id: str = "") -> str:
    title = strip_markup(value)
    if source_id.startswith("google-news-"):
        title = re.sub(r"\s+-\s+[^-]{2,80}$", "", title).strip()
    return title


def normalized_title(value: str) -> str:
    text = html.unescape(value).casefold()
    text = re.sub(r"\b(the|a|an|official|announces?|launches?|releases?|introduces?)\b", " ", text)
    text = re.sub(r"[^\w\u3400-\u9fff]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


TOPIC_STOPWORDS = {
    "a", "about", "against", "alleged", "an", "and", "at", "calls", "company", "for", "former",
    "from", "how", "in", "is", "it", "may", "of", "on", "over", "that", "the", "their", "to",
    "two", "what", "with",
}
TOPIC_ACTIONS = {
    "acquire", "ban", "delay", "fund", "launch", "open", "partner", "release", "steal", "sue",
}
ENTITY_STOPWORDS = {"about", "artificial", "intelligence", "model", "models", "news", "the", "with"}
TITLE_ACTION_PHRASES = (
    "open source", "release", "launch", "sue", "fund", "partner",
    "开源", "发布", "推出", "上线", "融资", "起诉", "合作", "联手",
)


def topic_tokens(value: str) -> set[str]:
    text = normalized_title(value)
    replacements = (
        (r"\b(?:lawsuit|sued|sues|suing)\b", "sue"),
        (r"\b(?:theft|stolen|stealing)\b", "steal"),
        (r"\b(?:released|releasing)\b", "release"),
        (r"\b(?:launched|launching)\b", "launch"),
        (r"\b(?:funded|funding|raises|raised)\b", "fund"),
        (r"\b(?:acquired|acquisition)\b", "acquire"),
        (r"\b(?:partners|partnership|collaboration)\b", "partner"),
        (r"\bsecrets\b", "secret"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return {token for token in text.split() if len(token) > 1 and token not in TOPIC_STOPWORDS}


def entity_tokens(value: str) -> set[str]:
    tokens = {token.casefold().strip(".-") for token in re.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}", value)}
    return {token for token in tokens if token not in ENTITY_STOPWORDS and len(token) >= 4}


def canonical_url(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), ""))


def load_sources(path: Path = SOURCE_CATALOG) -> list[Source]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorError(f"could not load source catalog {path}: {exc}") from exc
    rows = payload.get("sources")
    if not isinstance(rows, list):
        raise CollectorError(f"source catalog {path} has no sources list")
    sources: list[Source] = []
    seen: set[str] = set()
    for row in rows:
        try:
            source = Source(
                id=str(row["id"]),
                name=str(row["name"]),
                url=str(row["url"]),
                format=str(row["format"]).lower(),
                parser=row.get("parser"),
                language=str(row.get("language") or "und"),
                categories=tuple(str(item) for item in row.get("categories") or ("other",)),
                tier=str(row.get("tier") or "discovery"),
                weight=float(row.get("weight") or 0),
                enabled=bool(row.get("enabled", True)),
                disabled_reason=row.get("disabled_reason"),
                freshness_mode=str(row.get("freshness_mode") or "published"),
                minimum_stars=int(row.get("minimum_stars") or 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectorError(f"invalid source entry: {row!r}: {exc}") from exc
        if source.id in seen:
            raise CollectorError(f"duplicate source id: {source.id}")
        if source.format not in {"rss", "atom", "json"}:
            raise CollectorError(f"unsupported format for {source.id}: {source.format}")
        seen.add(source.id)
        sources.append(source)
    return sources


def select_sources(
    sources: Iterable[Source],
    source_ids: list[str],
    categories: list[str],
    languages: list[str],
) -> list[Source]:
    all_sources = list(sources)
    known = {source.id for source in all_sources}
    unknown = sorted(set(source_ids) - known)
    if unknown:
        raise CollectorError(f"unknown source id(s): {', '.join(unknown)}")
    explicit = bool(source_ids)
    selected: list[Source] = []
    for source in all_sources:
        if explicit:
            if source.id not in source_ids:
                continue
        elif not source.enabled:
            continue
        if categories and not set(categories).intersection(source.categories):
            continue
        if languages and source.language not in languages:
            continue
        selected.append(source)
    if not selected:
        raise CollectorError("no sources match the requested filters")
    return selected


def source_url(source: Source, since: datetime) -> str:
    return source.url.replace("{since}", since.date().isoformat())


def fetch_text(source: Source, url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    headers = {
        "Accept": "application/atom+xml, application/rss+xml, application/json, text/xml;q=0.9, */*;q=0.5",
        "User-Agent": os.environ.get("AI_HOTSPOT_USER_AGENT", DEFAULT_UA),
        "Connection": "close",
    }
    if source.id.startswith("github-"):
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    request = Request(url, headers=headers)
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            chunks: list[bytes] = []
            size = 0
            reader = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() - started > timeout:
                    raise CollectorError(f"total download time exceeded {timeout}s")
                chunk = reader(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise CollectorError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
            raw = b"".join(chunks)
    except HTTPError as exc:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        suffix = f"; retry after {retry_after}s" if retry_after else ""
        raise CollectorError(f"HTTP {exc.code}{suffix}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CollectorError(f"request failed: {exc}") from exc
    return raw.decode(charset, errors="replace")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def first_text(element: ET.Element, names: set[str]) -> str:
    for child in element.iter():
        if local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def entry_link(element: ET.Element) -> str:
    fallback = ""
    for child in element.iter():
        if local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or child.text or "").strip()
        rel = child.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            return href
        if href and not fallback:
            fallback = href
    return fallback


def xml_entries(text: str, source: Source, observed_at: datetime) -> list[Candidate]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CollectorError(f"invalid XML: {exc}") from exc
    entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
    candidates: list[Candidate] = []
    for entry in entries:
        raw_title = first_text(entry, {"title"})
        title = clean_title(raw_title, source.id)
        link = entry_link(entry)
        if not title or not link:
            continue
        raw_date = first_text(entry, {"pubdate", "published", "updated", "date"})
        published = parse_datetime(raw_date)
        summary = strip_markup(first_text(entry, {"description", "summary", "content", "abstract"}))
        signals: dict[str, Any] = {}
        original_source = first_text(entry, {"source"})
        if original_source:
            signals["original_source"] = strip_markup(original_source)
        if not published:
            signals["missing_published_at"] = True
        freshness = observed_at if source.freshness_mode == "observed" or published is None else published
        candidates.append(
            make_candidate(source, title, link, published, summary, signals, freshness)
        )
    return candidates


def json_entries(text: str, source: Source, observed_at: datetime) -> list[Candidate]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectorError(f"invalid JSON: {exc}") from exc
    parser = source.parser
    if parser == "huggingface_papers":
        return parse_huggingface_papers(payload, source, observed_at)
    if parser == "huggingface_models":
        return parse_huggingface_models(payload, source, observed_at)
    if parser == "github_repositories":
        return parse_github_repositories(payload, source, observed_at)
    raise CollectorError(f"unknown JSON parser for {source.id}: {parser}")


def make_candidate(
    source: Source,
    title: str,
    url: str,
    published: datetime | None,
    summary: str,
    signals: dict[str, Any],
    freshness: datetime | None,
) -> Candidate:
    category = infer_category(source, title, summary)
    return Candidate(
        title=title,
        url=url,
        published_at=isoformat(published),
        source_name=source.name,
        source_kind=source.parser or source.format,
        source_tier=source.tier,
        language=source.language,
        category=category,
        summary=summary,
        signals=signals,
        duplicate_sources=[source.name],
        source_id=source.id,
        source_weight=source.weight,
        freshness_at=isoformat(freshness),
    )


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "papers": ("paper", "arxiv", "study", "论文", "研究报告"),
    "models": (
        " model", "model ", "llm", "language model", "foundation model", "weights", "checkpoint",
        "模型", "大模型", "多模态", "参数模型",
    ),
    "opensource": ("open source", "open-source", "github", "repository", "repo", "framework", "show hn", "self-hosted", "开源"),
    "products": (
        "product", "feature", " api", "app", "platform", "service", "tool", "browser", "use case",
        "产品", "功能", "上线", "浏览器", "用例",
    ),
    "industry": (
        "funding", "raises", "acquires", "acquisition", "partnership", "policy", "lawsuit", "sues", "sue", "court", "investment",
        "competes", "competition", "commentary", "podcast", "interview", "trend", "paradigm", "stock",
        "share price", "earnings", "salary", "executive", "founder letter",
        "融资", "收购", "合作", "联手", "政策", "诉讼", "投资", "竞争", "评论", "播客", "专访", "趋势", "范式", "生态",
        "股价", "解禁", "财报", "薪酬", "创始人", "内部信", "公司治理",
    ),
}

MODEL_ACTION_KEYWORDS = (
    "release", "launch", "introduce", "unveil", "debut", "open source", "open-source", "upgrade", "preview",
    "发布", "推出", "开源", "上线", "升级", "更新", "预览",
)
VERSIONED_MODEL_NAME = re.compile(r"\b(?:gpt|claude|gemini|grok|llama|qwen|mistral)[\s-]*\d", re.I)


def infer_category(source: Source, title: str, summary: str) -> str:
    selectable = [item for item in source.categories if item != "official"]
    if source.id in {"arxiv-ai", "huggingface-papers"}:
        return "papers"
    if len(selectable) == 1:
        return selectable[0]
    text = f" {title} ".casefold()
    if "industry" in selectable and any(keyword in text for keyword in CATEGORY_KEYWORDS["industry"]):
        return "industry"
    if "opensource" in selectable and any(keyword in text for keyword in CATEGORY_KEYWORDS["opensource"]):
        return "opensource"
    model_subject = any(keyword in text for keyword in CATEGORY_KEYWORDS["models"]) or bool(VERSIONED_MODEL_NAME.search(text))
    model_action = any(keyword in text for keyword in MODEL_ACTION_KEYWORDS)
    if "models" in selectable and model_subject and model_action:
        return "models"
    if "products" in selectable and any(keyword in text for keyword in CATEGORY_KEYWORDS["products"]):
        return "products"
    if "models" in selectable and model_subject and source.tier == "primary":
        return "models"
    if source.tier in {"secondary", "discovery", "community"} and "industry" in selectable:
        return "industry"
    if "products" in selectable:
        return "products"
    if "research" in selectable:
        return "research"
    return selectable[0] if selectable else source.categories[0]


def parse_huggingface_papers(payload: Any, source: Source, observed_at: datetime) -> list[Candidate]:
    if not isinstance(payload, list):
        raise CollectorError("Hugging Face papers payload is not a list")
    candidates: list[Candidate] = []
    for row in payload:
        paper = row.get("paper") if isinstance(row, dict) else None
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id") or row.get("paperId") or "").strip()
        title = clean_title(paper.get("title"))
        if not title or not paper_id:
            continue
        published = parse_datetime(paper.get("publishedAt") or row.get("publishedAt"))
        signals = {
            "upvotes": number(row.get("upvotes") or paper.get("upvotes")),
            "comments": number(row.get("numComments") or row.get("comments")),
        }
        freshness = published or observed_at
        candidates.append(
            make_candidate(
                source,
                title,
                f"https://huggingface.co/papers/{paper_id}",
                published,
                strip_markup(paper.get("summary") or row.get("summary")),
                compact_signals(signals),
                freshness,
            )
        )
    return candidates


def parse_huggingface_models(payload: Any, source: Source, observed_at: datetime) -> list[Candidate]:
    if not isinstance(payload, list):
        raise CollectorError("Hugging Face models payload is not a list")
    candidates: list[Candidate] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("modelId") or row.get("id") or "").strip()
        if not model_id:
            continue
        published = parse_datetime(row.get("createdAt") or row.get("lastModified"))
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        signals = compact_signals(
            {
                "trending_score": number(row.get("trendingScore")),
                "likes": number(row.get("likes")),
                "downloads": number(row.get("downloads")),
                "pipeline_tag": row.get("pipeline_tag"),
                "license": next((tag.split(":", 1)[1] for tag in tags if str(tag).startswith("license:")), None),
                "trending_snapshot": True,
            }
        )
        summary_parts = [str(row.get("pipeline_tag") or "").strip(), str(row.get("library_name") or "").strip()]
        summary = " · ".join(part for part in summary_parts if part)
        candidate = make_candidate(
            source,
            model_id,
            f"https://huggingface.co/{model_id}",
            published,
            summary,
            signals,
            published or observed_at,
        )
        candidate.category = "models"
        candidates.append(candidate)
    return candidates


def parse_github_repositories(payload: Any, source: Source, observed_at: datetime) -> list[Candidate]:
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CollectorError("GitHub repositories payload has no items list")
    candidates: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        full_name = str(row.get("full_name") or "").strip()
        link = str(row.get("html_url") or "").strip()
        stars = number(row.get("stargazers_count"))
        if not full_name or not link or stars < source.minimum_stars:
            continue
        published = parse_datetime(row.get("created_at") or row.get("pushed_at"))
        signals = compact_signals(
            {
                "stars": stars,
                "forks": number(row.get("forks_count")),
                "watchers": number(row.get("watchers_count")),
                "language": row.get("language"),
            }
        )
        candidates.append(
            make_candidate(
                source,
                full_name,
                link,
                published,
                strip_markup(row.get("description")),
                signals,
                published or observed_at,
            )
        )
    return candidates


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def compact_signals(signals: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in signals.items() if value not in (None, "", 0, 0.0, False)}


def parse_source(text: str, source: Source, observed_at: datetime) -> list[Candidate]:
    if source.format in {"rss", "atom"}:
        return xml_entries(text, source, observed_at)
    if source.format == "json":
        return json_entries(text, source, observed_at)
    raise CollectorError(f"unsupported source format: {source.format}")


def candidate_in_window(candidate: Candidate, cutoff: datetime, observed_at: datetime) -> bool:
    freshness = parse_datetime(candidate.freshness_at)
    if freshness is None:
        return True
    return cutoff <= freshness <= observed_at + timedelta(hours=1)


def titles_match(left: Candidate, right: Candidate) -> bool:
    if canonical_url(left.url) and canonical_url(left.url) == canonical_url(right.url):
        return True
    a = normalized_title(left.title)
    b = normalized_title(right.title)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = min(len(a), len(b))
    if shorter < 12:
        return False
    ratio = SequenceMatcher(None, a, b).ratio()
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    overlap = len(a_tokens & b_tokens) / max(1, min(len(a_tokens), len(b_tokens)))
    if ratio >= 0.9 or (ratio >= 0.82 and overlap >= 0.75):
        return True
    a_topic = topic_tokens(left.title)
    b_topic = topic_tokens(right.title)
    common = a_topic & b_topic
    topic_overlap = len(common) / max(1, min(len(a_topic), len(b_topic)))
    if len(common) >= 3 and bool(common & TOPIC_ACTIONS) and topic_overlap >= 0.45:
        return True
    shared_entities = entity_tokens(left.title) & entity_tokens(right.title)
    shared_actions = {
        action
        for action in TITLE_ACTION_PHRASES
        if action in left.title.casefold() and action in right.title.casefold()
    }
    return bool(shared_entities and shared_actions)


def merge_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    merged: list[Candidate] = []
    ordered = sorted(
        candidates,
        key=lambda item: (-item.source_weight, item.source_id, normalized_title(item.title), item.url),
    )
    for candidate in ordered:
        match = next((existing for existing in merged if titles_match(existing, candidate)), None)
        if match is None:
            merged.append(candidate)
            continue
        if candidate.source_name not in match.duplicate_sources:
            match.duplicate_sources.append(candidate.source_name)
        match.duplicate_sources.sort()
        match.signals = merge_signals(match.signals, candidate.signals)
        if not match.summary and candidate.summary:
            match.summary = candidate.summary
        current_date = parse_datetime(match.published_at)
        candidate_date = parse_datetime(candidate.published_at)
        if candidate_date and (not current_date or candidate_date > current_date):
            match.published_at = candidate.published_at
        current_freshness = parse_datetime(match.freshness_at)
        candidate_freshness = parse_datetime(candidate.freshness_at)
        if candidate_freshness and (not current_freshness or candidate_freshness > current_freshness):
            match.freshness_at = candidate.freshness_at
    return merged


def merge_signals(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            merged[key] = max(number(merged.get(key)), number(value))
        elif key not in merged:
            merged[key] = value
    return merged


def score_candidate(candidate: Candidate, observed_at: datetime) -> float:
    freshness = parse_datetime(candidate.freshness_at) or observed_at
    age_hours = max(0.0, (observed_at - freshness).total_seconds() / 3600)
    recency = max(0.0, 28.0 - age_hours * 0.55)
    cross_source = min(24.0, max(0, len(candidate.duplicate_sources) - 1) * 8.0)
    signal_bonus = 0.0
    for key in ("trending_score", "upvotes", "comments", "stars", "forks", "likes", "downloads"):
        value = number(candidate.signals.get(key))
        if value > 0:
            multiplier = 2.0 if key in {"trending_score", "upvotes", "stars"} else 1.0
            signal_bonus += math.log10(value + 1) * multiplier
    signal_bonus = min(18.0, signal_bonus)
    missing_date_penalty = 6.0 if candidate.signals.get("missing_published_at") else 0.0
    return round(candidate.source_weight + recency + cross_source + signal_bonus - missing_date_penalty, 2)


def stable_id(candidate: Candidate) -> str:
    key = canonical_url(candidate.url) or normalized_title(candidate.title)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def fetch_one(source: Source, since: datetime, observed_at: datetime, timeout: int) -> tuple[list[Candidate], dict[str, Any]]:
    url = source_url(source, since)
    text = fetch_text(source, url, timeout=timeout)
    candidates = parse_source(text, source, observed_at)
    return candidates, {
        "id": source.id,
        "name": source.name,
        "ok": True,
        "fetched_entries": len(candidates),
        "url": url,
    }


def collect(
    sources: list[Source],
    hours: int,
    limit: int,
    timeout: int = DEFAULT_TIMEOUT,
    observed_at: datetime | None = None,
    candidate_categories: list[str] | None = None,
) -> tuple[dict[str, Any], int]:
    observed_at = observed_at or utc_now()
    cutoff = observed_at - timedelta(hours=hours)
    all_candidates: list[Candidate] = []
    stats_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    workers = min(8, max(1, len(sources)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_one, source, cutoff, observed_at, timeout): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                candidates, stat = future.result()
                accepted = [
                    item
                    for item in candidates
                    if candidate_in_window(item, cutoff, observed_at)
                    and (not candidate_categories or item.category in candidate_categories)
                ]
                stat["accepted_entries"] = len(accepted)
                stats_by_id[source.id] = stat
                all_candidates.extend(accepted)
            except Exception as exc:  # noqa: BLE001 - isolate source failures.
                message = f"{source.id}: {exc}"
                warnings.append(message)
                stats_by_id[source.id] = {
                    "id": source.id,
                    "name": source.name,
                    "ok": False,
                    "fetched_entries": 0,
                    "accepted_entries": 0,
                    "url": source_url(source, cutoff),
                    "error": str(exc),
                }
    source_stats = [stats_by_id[source.id] for source in sources]
    succeeded = sum(1 for stat in source_stats if stat["ok"])
    if succeeded == 0:
        payload = {
            "collected_at": isoformat(observed_at),
            "hours": hours,
            "limit": limit,
            "source_stats": source_stats,
            "warnings": warnings,
            "candidates": [],
            "needs_window_expansion": hours <= 24,
            "error": "all selected sources failed",
        }
        return payload, 2
    merged = merge_candidates(all_candidates)

    # Score is computed as informational metadata only — NOT used for filtering.
    for candidate in merged:
        candidate.score = score_candidate(candidate, observed_at)

    # Sort ALL candidates by freshness (newest first), then score as tiebreaker.
    # No caps, no diversity filtering, no shortlist selection — all analysis is
    # delegated to the AI analyst.
    merged.sort(
        key=lambda item: (
            -(parse_datetime(item.freshness_at) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            -item.score,
            normalized_title(item.title),
            item.url,
        )
    )

    # Apply only a hard limit on total output size (not content filtering).
    if limit > 0:
        merged = merged[:limit]

    candidates_out = []
    for candidate in merged:
        row = candidate.to_dict()
        row["id"] = stable_id(candidate)
        candidates_out.append(row)

    payload = {
        "collected_at": isoformat(observed_at),
        "hours": hours,
        "limit": limit,
        "candidate_pool_count": len(merged),
        "total_candidates_output": len(candidates_out),
        "selected_sources": [source.id for source in sources],
        "ranking_policy": {
            "note": "No content filtering applied. All merged candidates are output sorted by "
                    "freshness (newest first). score is computed as informational metadata only. "
                    "All filtering, prioritization, and shortlisting is delegated to the AI analyst.",
        },
        "source_stats": source_stats,
        "warnings": sorted(warnings),
        "candidates": candidates_out,
        "needs_window_expansion": hours <= 24 and len(merged) < 10,
        "error": None,
    }
    return payload, 0


def render_markdown(payload: dict[str, Any]) -> str:
    collected = parse_datetime(payload.get("collected_at")) or utc_now()
    items = payload.get("candidates") or []
    lines = [
        f"# AI 热点候选池 · {collected.date().isoformat()}",
        "",
        f"> 采集范围：最近 {payload.get('hours')} 小时",
        f"> 采集时间：{payload.get('collected_at')}",
        f"> 合并后候选池：{payload.get('candidate_pool_count', 0)} 条；输出：{len(items)} 条",
        "> 状态：脚本仅做收集 + 规范化 + 去重，不过滤任何内容。以下全部候选项均由 AI 分析后决定取舍。",
        "",
        "| # | 发布时间 | 分数 | 类别 | 标题 | 线索来源 |",
        "|---:|---|---:|---|---|---|",
    ]
    for index, item in enumerate(items, start=1):
        title = str(item.get("title") or "").replace("|", "\\|")
        sources = "、".join(item.get("duplicate_sources") or [item.get("source_name")])
        link = item.get("url") or ""
        lines.append(
            f"| {index} | {item.get('published_at') or '未知'} | {item.get('score')} | "
            f"{item.get('category')} | [{title}]({link}) | {sources} |"
        )
    if payload.get("warnings"):
        lines.extend(["", "## 采集警告", ""])
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    if payload.get("needs_window_expansion"):
        lines.extend(["", "> 候选池少于 10 条：请使用 `--hours 48` 扩大时间窗口后重新采集。"])
    if payload.get("error"):
        lines.extend(["", f"错误：{payload['error']}"])
    return "\n".join(lines) + "\n"


def run_self_test() -> int:
    source = Source(
        id="fixture",
        name="Fixture",
        url="https://example.com/feed",
        format="rss",
        language="en",
        categories=("industry",),
        tier="secondary",
        weight=10,
        enabled=True,
    )
    now = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    sample = """<?xml version="1.0"?><rss><channel><item>
    <title>Example AI launch</title><link>https://example.com/launch?utm_source=x</link>
    <pubDate>Sun, 12 Jul 2026 23:00:00 GMT</pubDate><description>Details</description>
    </item></channel></rss>"""
    parsed = xml_entries(sample, source, now)
    assert len(parsed) == 1
    assert canonical_url(parsed[0].url) == "https://example.com/launch"
    duplicate = make_candidate(
        source,
        "Example AI launch",
        "https://example.com/launch",
        now,
        "",
        {},
        now,
    )
    merged = merge_candidates([parsed[0], duplicate])
    assert len(merged) == 1
    print(json.dumps({"ok": True, "candidate": merged[0].to_dict()}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="Freshness window in hours (default: 24).")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum candidates to output (default: 200). No content filtering is applied; this only caps total output size.")
    parser.add_argument("--category", action="append", default=[], help="Filter by category; repeatable.")
    parser.add_argument("--source", action="append", default=[], help="Filter by source id; repeatable. Explicit ids may select disabled sources.")
    parser.add_argument("--language", choices=("en", "zh", "und"), action="append", default=[], help="Filter by source language; repeatable.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format (default: json).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-source timeout in seconds.")
    parser.add_argument("--catalog", type=Path, default=SOURCE_CATALOG, help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true", help="Run offline parser and dedupe checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.hours <= 0 or args.limit <= 0 or args.timeout <= 0:
        raise SystemExit("--hours, --limit, and --timeout must be positive")
    try:
        sources = select_sources(
            load_sources(args.catalog),
            source_ids=args.source,
            categories=args.category,
            languages=args.language,
        )
        payload, exit_code = collect(
            sources,
            args.hours,
            args.limit,
            timeout=args.timeout,
            candidate_categories=args.category,
        )
    except CollectorError as exc:
        payload = {"candidates": [], "warnings": [], "error": str(exc)}
        exit_code = 2
    if args.format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
