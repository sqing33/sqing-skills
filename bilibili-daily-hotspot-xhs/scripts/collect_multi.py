#!/usr/bin/env python3
"""Fetch latest N videos from a Bilibili UP master and extract pinned-comment hotspots.

Reuses logic from collect_bili_daily_hotspots.py but supports fetching up to N recent
videos (default 2 = yesterday + today) and emits per-video hotspot entries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Reuse helpers from the skill's main script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_bili_daily_hotspots import (  # noqa: E402
    request_json,
    get_mixin_key,
    resolve_up,
    signed_params,
    top_or_fallback_comment,
    parse_hotspots,
    iso_from_ts,
    API,
)


def latest_videos(mid: int, mixin_key: str, ps: int) -> list[dict[str, Any]]:
    params = signed_params(
        {"mid": mid, "ps": ps, "pn": 1, "order": "pubdate", "platform": "web"},
        mixin_key,
    )
    payload = request_json("/x/space/wbi/arc/search", params)
    vlist = (((payload.get("data") or {}).get("list") or {}).get("vlist") or [])
    return vlist


def video_detail(bvid: str | None = None, aid: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if bvid:
        params["bvid"] = bvid
    elif aid:
        params["aid"] = aid
    payload = request_json("/x/web-interface/view", params)
    return payload["data"]


def collect_for_up_multi(raw_up: str, ps: int, max_hotspots: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"input": raw_up, "videos": [], "warnings": [], "error": None}
    try:
        mixin_key = get_mixin_key()
        mid, resolved_name, warnings = resolve_up(raw_up, mixin_key)
        out["mid"] = mid
        out["up_name"] = resolved_name
        out["warnings"].extend(warnings)

        vlist = latest_videos(mid, mixin_key, ps=ps)
        for v in vlist:
            detail = video_detail(bvid=v.get("bvid"), aid=v.get("aid"))
            owner = detail.get("owner") or {}
            pubdate = iso_from_ts(detail.get("pubdate") or v.get("created"))
            aid = int(detail.get("aid") or v.get("aid"))
            comment, source_type, comment_warnings = top_or_fallback_comment(aid)
            video_entry: dict[str, Any] = {
                "video_title": detail.get("title") or v.get("title"),
                "bvid": detail.get("bvid") or v.get("bvid"),
                "aid": aid,
                "pubdate": pubdate,
                "video_url": f"https://www.bilibili.com/video/{detail.get('bvid') or v.get('bvid')}",
                "cover_url": detail.get("pic") or v.get("pic"),
                "pinned_comment": None,
                "hotspots": [],
                "warnings": list(comment_warnings),
            }
            if comment:
                comment["source_type"] = source_type
                video_entry["pinned_comment"] = comment
                parsed = parse_hotspots(
                    comment.get("message") or "",
                    source_up=resolved_name or owner.get("name"),
                    source_video_url=video_entry["video_url"],
                    source_comment_type=source_type,
                )
                if max_hotspots is not None:
                    parsed = parsed[:max_hotspots]
                video_entry["hotspots"] = [h.__dict__ for h in parsed]
            out["videos"].append(video_entry)
        out["up_name"] = out["up_name"] or (vlist[0].get("author") if vlist else None)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--up", required=True, help="UP mid or space URL.")
    parser.add_argument("--ps", type=int, default=2, help="Number of latest videos to fetch.")
    parser.add_argument("--max-hotspots", type=int, default=None)
    args = parser.parse_args()

    result = collect_for_up_multi(args.up, ps=args.ps, max_hotspots=args.max_hotspots)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())