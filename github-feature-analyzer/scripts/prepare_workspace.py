#!/usr/bin/env python3
"""Create deterministic local workspace paths for repository feature analysis."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse


class RepoUrlError(ValueError):
    """Raised when a repository URL is invalid for this skill."""


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Return (owner, repo) extracted from supported GitHub URL forms."""
    raw = repo_url.strip()
    if not raw:
        raise RepoUrlError("repo_url is empty")

    if raw.startswith("git@github.com:"):
        path_part = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise RepoUrlError("repo_url must use http(s) or git@github.com format")
        if parsed.netloc.lower() != "github.com":
            raise RepoUrlError("only github.com repositories are supported")
        path_part = parsed.path.lstrip("/")

    if path_part.endswith(".git"):
        path_part = path_part[:-4]

    parts = [part for part in path_part.split("/") if part]
    if len(parts) < 2:
        raise RepoUrlError("repo_url must include owner and repo")

    owner = parts[0]
    repo = parts[1]

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        raise RepoUrlError(f"invalid owner segment: {owner}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise RepoUrlError(f"invalid repo segment: {repo}")

    return owner, repo


def slugify_name(value: str) -> str:
    """Convert arbitrary text to a safe path slug."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unknown"


def discover_project_root() -> Path:
    """Best-effort project root detection to keep outputs in repository root."""
    starts = [Path.cwd().resolve(), Path(__file__).resolve().parent]
    visited: set[Path] = set()

    for start in starts:
        for candidate in [start, *start.parents]:
            if candidate in visited:
                continue
            visited.add(candidate)
            if (candidate / ".git").exists() or (candidate / "PROJECT_STRUCTURE.md").exists():
                return candidate

    return Path.cwd().resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", required=True, help="GitHub repository URL")
    parser.add_argument("--ref", default="main", help="Git ref (default: main)")
    parser.add_argument(
        "--agent-mode",
        default="multi",
        choices=["multi", "single", "auto"],
        help="Analysis agent mode metadata",
    )
    parser.add_argument(
        "--max-parallel-agents",
        default="auto",
        help="Parallel sub-agent cap metadata (auto or positive integer)",
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help="Unified analyzer storage root (default: <project-root>/.github-feature-analyzer)",
    )
    parser.add_argument(
        "--downloads-root",
        default=None,
        help="Deprecated compatibility option; prefer --storage-root",
    )
    parser.add_argument(
        "--reports-root",
        default=None,
        help="Deprecated compatibility option; prefer --storage-root",
    )
    args = parser.parse_args()

    owner, repo = parse_repo_url(args.repo_url)
    ref = args.ref.strip() or "main"
    project_key = slugify_name(f"{owner}-{repo}")
    notes: list[str] = []

    if args.storage_root:
        storage_root = Path(args.storage_root).resolve()
    elif args.downloads_root or args.reports_root:
        legacy_root = args.downloads_root or args.reports_root
        storage_root = Path(legacy_root).resolve() if legacy_root else discover_project_root() / ".github-feature-analyzer"
        notes.append("legacy path flag detected; prefer --storage-root or default project root storage")
    else:
        storage_root = (discover_project_root() / ".github-feature-analyzer").resolve()

    project_dir = storage_root / project_key
    source_dir = project_dir / "source"
    artifacts_dir = project_dir / "artifacts"
    subagents_dir = artifacts_dir / "subagents"
    report_path = project_dir / "report.md"

    for path in (storage_root, project_dir, source_dir, artifacts_dir, subagents_dir):
        path.mkdir(parents=True, exist_ok=True)

    result = {
        "repo_url": args.repo_url,
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "agent_mode": args.agent_mode,
        "max_parallel_agents": args.max_parallel_agents,
        "storage_root": str(storage_root),
        "project_key": project_key,
        "project_dir": str(project_dir),
        "source_dir": str(source_dir),
        "artifacts_dir": str(artifacts_dir),
        "subagents_dir": str(subagents_dir),
        "report_path": str(report_path),
        # Backward compatibility fields for older orchestrations.
        "workspace_dir": str(project_dir),
        "workspace_name": project_key,
        "downloads_root": str(storage_root),
        "reports_root": str(storage_root),
        "notes": notes,
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
