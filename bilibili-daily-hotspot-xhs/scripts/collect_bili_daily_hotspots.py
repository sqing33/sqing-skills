#!/usr/bin/env python3
"""Collect Bilibili daily-news pinned-comment hotspots.

The script uses public Bilibili web APIs plus optional BILIBILI_COOKIE.
It prints structured JSON and never stores credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API = "https://api.bilibili.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class BiliError(RuntimeError):
    pass


@dataclass
class Hotspot:
    timestamp: str
    title: str
    raw_line: str
    source_up: str | None = None
    source_video_url: str | None = None
    source_comment_type: str | None = None


@dataclass
class CreatorResult:
    input: str
    up_name: str | None = None
    mid: int | None = None
    video_title: str | None = None
    bvid: str | None = None
    aid: int | None = None
    pubdate: str | None = None
    video_url: str | None = None
    cover_url: str | None = None
    pinned_comment: dict[str, Any] | None = None
    hotspots: list[Hotspot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def request_json(
    path_or_url: str,
    params: dict[str, Any] | None = None,
    allow_codes: set[int] | None = None,
) -> dict[str, Any]:
    url = path_or_url if path_or_url.startswith("http") else API + path_or_url
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)

    headers = {
        "User-Agent": os.environ.get("BILIBILI_USER_AGENT", DEFAULT_UA),
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }
    cookie = os.environ.get("BILIBILI_COOKIE")
    if cookie:
        headers["Cookie"] = cookie

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if raw:
            try:
                payload = json.loads(raw)
                msg = payload.get("message") or payload.get("msg") or raw[:200]
            except json.JSONDecodeError:
                msg = raw[:200]
        else:
            msg = exc.reason
        if exc.code in (401, 403, 412):
            msg = f"{msg}。B站风控/权限校验失败，可尝试设置 BILIBILI_COOKIE。"
        raise BiliError(f"HTTP {exc.code} for {url}: {msg}") from exc
    except Exception as exc:  # noqa: BLE001 - report remote/network failures clearly.
        raise BiliError(f"request failed for {url}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BiliError(f"non-JSON response for {url}: {raw[:200]}") from exc

    code = payload.get("code")
    if code not in (0, None) and code not in (allow_codes or set()):
        msg = payload.get("message") or payload.get("msg") or "unknown error"
        if code in (-101, -352, -403, -412):
            msg += "。B站风控/权限校验失败，可尝试设置 BILIBILI_COOKIE。"
        raise BiliError(f"Bilibili API error {code}: {msg}")
    return payload


def get_mixin_key() -> str:
    nav = request_json("/x/web-interface/nav", allow_codes={-101})
    wbi_img = (nav.get("data") or {}).get("wbi_img") or {}
    img_key = _basename_without_ext(wbi_img.get("img_url", ""))
    sub_key = _basename_without_ext(wbi_img.get("sub_url", ""))
    if not img_key or not sub_key:
        raise BiliError("could not obtain WBI image keys from /x/web-interface/nav")
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _basename_without_ext(url: str) -> str:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def signed_params(params: dict[str, Any], mixin_key: str) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = str(value).translate({ord(c): None for c in "!'()*"})
    cleaned["wts"] = str(int(time.time()))
    query = "&".join(
        f"{quote(str(k), safe='')}={quote(str(cleaned[k]), safe='')}"
        for k in sorted(cleaned)
    )
    cleaned["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return cleaned


def resolve_up(raw: str, mixin_key: str) -> tuple[int, str | None, list[str]]:
    text = raw.strip()
    warnings: list[str] = []
    mid_match = re.search(r"(?:space\.bilibili\.com/)?(\d{3,})", text)
    if mid_match:
        return int(mid_match.group(1)), None, warnings

    params = signed_params(
        {
            "search_type": "bili_user",
            "keyword": text,
            "page": 1,
            "pagesize": 5,
        },
        mixin_key,
    )
    payload = request_json("/x/web-interface/search/type", params)
    results = ((payload.get("data") or {}).get("result") or [])
    if not results:
        raise BiliError(f"could not resolve UP name: {text}")
    first = results[0]
    if len(results) > 1:
        warnings.append(f"UP name search returned {len(results)} candidates; using first result.")
    return int(first["mid"]), strip_html(first.get("uname") or text), warnings


def latest_video(mid: int, mixin_key: str, ps: int = 1) -> list[dict[str, Any]]:
    params = signed_params(
        {
            "mid": mid,
            "ps": ps,
            "pn": 1,
            "order": "pubdate",
            "platform": "web",
        },
        mixin_key,
    )
    payload = request_json("/x/space/wbi/arc/search", params)
    vlist = (((payload.get("data") or {}).get("list") or {}).get("vlist") or [])
    if not vlist:
        raise BiliError(f"no videos found for mid={mid}")
    return vlist


def video_detail(bvid: str | None = None, aid: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if bvid:
        params["bvid"] = bvid
    elif aid:
        params["aid"] = aid
    else:
        raise ValueError("bvid or aid is required")
    payload = request_json("/x/web-interface/view", params)
    return payload["data"]


def top_or_fallback_comment(aid: int) -> tuple[dict[str, Any] | None, str, list[str]]:
    warnings: list[str] = []
    payload = request_json("/x/v2/reply", {"type": 1, "oid": aid, "pn": 1, "ps": 20, "sort": 2})
    data = payload.get("data") or {}
    upper = data.get("upper") or {}
    top = upper.get("top") or data.get("top")
    if top:
        return normalize_reply(top), "pinned_reply", warnings

    replies = data.get("replies") or []
    if replies:
        warnings.append("No pinned comment found; using first high-like/root reply as fallback.")
        return normalize_reply(replies[0]), "fallback_high_like_reply", warnings

    warnings.append("No comments found.")
    return None, "none", warnings


def normalize_reply(reply: dict[str, Any]) -> dict[str, Any]:
    content = reply.get("content") or {}
    member = reply.get("member") or {}
    return {
        "rpid": reply.get("rpid") or reply.get("rpid_str"),
        "member_name": member.get("uname"),
        "member_mid": member.get("mid"),
        "message": content.get("message") or "",
        "ctime": reply.get("ctime"),
        "like": reply.get("like"),
    }


TIMESTAMP_LINE = re.compile(
    r"^\s*(?:置顶\s*)?(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?P<title>.+?)\s*$"
)


def parse_hotspots(
    message: str,
    source_up: str | None = None,
    source_video_url: str | None = None,
    source_comment_type: str | None = None,
) -> list[Hotspot]:
    hotspots: list[Hotspot] = []
    seen: set[str] = set()
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = TIMESTAMP_LINE.match(line)
        if not match:
            continue
        title = normalize_title(match.group("title"))
        if not title:
            continue
        dedupe_key = title.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        hotspots.append(
            Hotspot(
                timestamp=match.group("ts"),
                title=title,
                raw_line=line,
                source_up=source_up,
                source_video_url=source_video_url,
                source_comment_type=source_comment_type,
            )
        )
    return hotspots


def normalize_title(title: str) -> str:
    title = strip_html(title)
    title = html.unescape(title)
    title = re.sub(r"\s+", " ", title).strip(" -:：\t")
    return title


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(text))).strip()


def iso_from_ts(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


@dataclass
class CreatorResult:
    input: str
    up_name: str | None = None
    mid: int | None = None
    video_title: str | None = None
    bvid: str | None = None
    aid: int | None = None
    pubdate: str | None = None
    video_url: str | None = None
    cover_url: str | None = None
    pinned_comment: dict[str, Any] | None = None
    hotspots: list[Hotspot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    videos: list[dict[str, Any]] = field(default_factory=list)


def bvid_from_video_input(raw_video: str) -> str | None:
    text = raw_video.strip()
    match = re.search(r"(BV[0-9A-Za-z]+)", text)
    if match:
        return match.group(1)
    return None


def collect_for_up(raw_up: str, mixin_key: str, max_hotspots: int | None = None) -> CreatorResult:
    result = CreatorResult(input=raw_up)
    try:
        mid, up_name, resolve_warnings = resolve_up(raw_up, mixin_key)
        result.mid = mid
        result.up_name = up_name
        result.warnings.extend(resolve_warnings)

        videos = latest_video(mid, mixin_key, ps=1)
        first = videos[0]
        bvid = first.get("bvid")
        if not bvid:
            raise BiliError(f"latest video for mid={mid} has no bvid")

        detail = video_detail(bvid=bvid)
        result.video_title = detail.get("title")
        result.bvid = detail.get("bvid")
        result.aid = int(detail.get("aid"))
        result.pubdate = iso_from_ts(detail.get("pubdate"))
        result.cover_url = detail.get("pic")
        result.video_url = f"https://www.bilibili.com/video/{result.bvid}" if result.bvid else None

        comment, source_type, comment_warnings = top_or_fallback_comment(result.aid)
        result.warnings.extend(comment_warnings)
        if comment:
            comment["source_type"] = source_type
            result.pinned_comment = comment
            parsed = parse_hotspots(
                comment.get("message") or "",
                source_up=result.up_name,
                source_video_url=result.video_url,
                source_comment_type=source_type,
            )
            if max_hotspots is not None:
                parsed = parsed[:max_hotspots]
            result.hotspots = parsed
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    return result


def collect_for_video(raw_video: str, max_hotspots: int | None = None) -> CreatorResult:
    result = CreatorResult(input=raw_video)
    try:
        bvid = bvid_from_video_input(raw_video)
        aid: int | None = None
        if not bvid:
            if raw_video.strip().isdigit():
                aid = int(raw_video.strip())
            else:
                raise BiliError(f"could not parse video input as BV id, video URL, or aid: {raw_video}")

        detail = video_detail(bvid=bvid, aid=aid)
        owner = detail.get("owner") or {}
        result.up_name = owner.get("name")
        result.mid = int(owner["mid"]) if owner.get("mid") is not None else None
        result.video_title = detail.get("title")
        result.bvid = detail.get("bvid")
        result.aid = int(detail.get("aid"))
        result.pubdate = iso_from_ts(detail.get("pubdate"))
        result.cover_url = detail.get("pic")
        result.video_url = f"https://www.bilibili.com/video/{result.bvid}" if result.bvid else None

        comment, source_type, comment_warnings = top_or_fallback_comment(result.aid)
        result.warnings.extend(comment_warnings)
        if comment:
            comment["source_type"] = source_type
            result.pinned_comment = comment
            parsed = parse_hotspots(
                comment.get("message") or "",
                source_up=result.up_name,
                source_video_url=result.video_url,
                source_comment_type=source_type,
            )
            if max_hotspots is not None:
                parsed = parsed[:max_hotspots]
            result.hotspots = parsed
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    return result


def read_up_file(path: str) -> list[str]:
    values: list[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                values.append(line)
    return values


def run_self_test() -> int:
    sample = """置顶 00:09 NVIDIA 正式发布并开源 Nemotron 3 Ultra 模型
00:30 OpenAI 发布 ChatGPT 新记忆架构 Dreaming
not a timeline line
04:56 VoidZero 加入 Cloudflare，核心项目维持开源
05:08 Google 宣布向犹他州所有 K-12 学校提供 Gemini for Education
04:56 VoidZero 加入 Cloudflare，核心项目维持开源
"""
    parsed = parse_hotspots(sample, "橘鸦Juya", "https://www.bilibili.com/video/BVtest", "pinned_reply")
    assert len(parsed) == 4, f"expected 4 parsed hotspots, got {len(parsed)}"
    assert parsed[0].timestamp == "00:09"
    assert parsed[0].title.startswith("NVIDIA")
    assert parsed[2].timestamp == "04:56"

    reply = normalize_reply(
        {
            "rpid": 1,
            "like": 9,
            "member": {"uname": "tester", "mid": "123"},
            "content": {"message": sample},
        }
    )
    assert reply["member_name"] == "tester"
    assert "NVIDIA" in reply["message"]

    print(json.dumps({"ok": True, "parsed_hotspots": [asdict(h) for h in parsed]}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--up", action="append", default=[], help="UP mid, space URL, or username. Repeatable.")
    parser.add_argument("--up-file", help="UTF-8 text file with one UP per line.")
    parser.add_argument("--video", action="append", default=[], help="Video BV id, video URL, or aid. Repeatable.")
    parser.add_argument("--max-hotspots", type=int, default=None, help="Max hotspots per UP.")
    parser.add_argument("--self-test", action="store_true", help="Run parser self-test without network.")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    ups = list(args.up)
    if args.up_file:
        ups.extend(read_up_file(args.up_file))
    ups = [u.strip() for u in ups if u and u.strip()]
    videos = [v.strip() for v in args.video if v and v.strip()]
    if not ups and not videos:
        parser.error("provide at least one --up, --up-file, or --video")

    mixin_key = None
    if ups:
        try:
            mixin_key = get_mixin_key()
        except Exception as exc:  # noqa: BLE001
            payload = {
                "results": [],
                "error": f"Could not initialize Bilibili WBI signing: {exc}",
                "hint": "If this is a risk-control failure, set BILIBILI_COOKIE and retry.",
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2

    results = []
    if ups and mixin_key:
        results.extend(collect_for_up(up, mixin_key, args.max_hotspots) for up in ups)
    results.extend(collect_for_video(video, args.max_hotspots) for video in videos)
    print(
        json.dumps(
            {"results": [serialize_result(r) for r in results]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(not r.error for r in results) else 1


def serialize_result(result: CreatorResult) -> dict[str, Any]:
    data = asdict(result)
    data["hotspots"] = [asdict(h) for h in result.hotspots]
    return data


if __name__ == "__main__":
    raise SystemExit(main())
