#!/usr/bin/env python3
"""Clean cached download workspaces created by github-feature-analyzer."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--downloads-root",
        default="./.github-feature-analyzer",
        help="Analyzer storage root (default: ./.github-feature-analyzer)",
    )
    parser.add_argument("--older-than-days", type=int, default=None, help="Remove directories older than N days")
    parser.add_argument("--all", action="store_true", help="Remove all workspace directories")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    return parser.parse_args()


def dir_size_bytes(path: Path) -> int:
    total = 0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            try:
                total += file_path.stat().st_size
            except OSError:
                pass
    return total


def main() -> int:
    args = parse_args()

    if not args.all and args.older_than_days is None:
        raise SystemExit("choose --all or --older-than-days")

    root = Path(args.downloads_root).resolve()
    if not root.exists():
        print(json.dumps({"storage_root": str(root), "removed": [], "reclaimed_bytes": 0}, indent=2))
        return 0

    if args.older_than_days is not None and args.older_than_days < 0:
        raise SystemExit("--older-than-days must be >= 0")

    cutoff = None
    if args.older_than_days is not None:
        cutoff = datetime.now() - timedelta(days=args.older_than_days)

    removed: list[dict[str, object]] = []
    reclaimed = 0

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue

        should_remove = args.all
        if cutoff is not None:
            modified = datetime.fromtimestamp(child.stat().st_mtime)
            if modified < cutoff:
                should_remove = True

        if not should_remove:
            continue

        size = dir_size_bytes(child)
        removed.append({"path": str(child), "size_bytes": size})
        reclaimed += size

        if not args.dry_run:
            shutil.rmtree(child, ignore_errors=True)

    result = {
        "storage_root": str(root),
        "dry_run": args.dry_run,
        "removed_count": len(removed),
        "reclaimed_bytes": reclaimed,
        "removed": removed,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
