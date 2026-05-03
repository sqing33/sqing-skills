#!/usr/bin/env python3
"""Fetch repository source for local analysis with archive-first and git fallback."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class FetchError(RuntimeError):
    """Raised for fetch failures."""


def parse_repo_url(repo_url: str) -> tuple[str, str, str]:
    raw = repo_url.strip()
    if not raw:
        raise FetchError("repo_url is empty")

    if raw.startswith("git@github.com:"):
        path_part = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise FetchError("repo_url must use http(s) or git@github.com format")
        if parsed.netloc.lower() != "github.com":
            raise FetchError("only github.com repositories are supported")
        path_part = parsed.path.lstrip("/")

    if path_part.endswith(".git"):
        path_part = path_part[:-4]

    parts = [part for part in path_part.split("/") if part]
    if len(parts) < 2:
        raise FetchError("repo_url must include owner and repo")

    owner, repo = parts[0], parts[1]
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return owner, repo, clone_url


def bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def run_cmd(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"command timed out: {' '.join(cmd)}") from exc


def ensure_clean_source_dir(source_dir: Path) -> str | None:
    """Ensure source_dir is clean for latest-run overwrite behavior."""
    if source_dir.exists() and not source_dir.is_dir():
        raise FetchError(f"source_dir is not a directory: {source_dir}")

    if source_dir.exists():
        if any(source_dir.iterdir()):
            shutil.rmtree(source_dir, ignore_errors=True)
            source_dir.mkdir(parents=True, exist_ok=True)
            return f"source_dir was reset for latest run: {source_dir}"
        return None

    source_dir.mkdir(parents=True, exist_ok=True)
    return None


def safe_extract_tar(tar: tarfile.TarFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in tar.getmembers():
        member_path = destination / member.name
        try:
            member_resolved = member_path.resolve()
        except FileNotFoundError:
            member_resolved = member_path.parent.resolve() / member_path.name
        if not str(member_resolved).startswith(str(destination_resolved)):
            raise FetchError(f"archive contains unsafe path: {member.name}")
    tar.extractall(destination)


def query_repo_metadata(owner: str, repo: str, timeout: int) -> tuple[dict | None, str | None]:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    request = Request(url, headers={"User-Agent": "github-feature-analyzer"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict):
            return payload, None
        return None, "repository metadata payload is not an object"
    except HTTPError as exc:
        if exc.code == 404:
            return None, "repository not found or not publicly accessible"
        return None, f"visibility check failed: HTTP {exc.code}"
    except URLError as exc:
        return None, f"visibility check failed: {exc.reason}"


def query_commit_sha(owner: str, repo: str, ref: str, timeout: int) -> tuple[str | None, str | None]:
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(ref, safe='')}"
    request = Request(url, headers={"User-Agent": "github-feature-analyzer"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        sha = payload.get("sha")
        if isinstance(sha, str) and sha:
            return sha, None
        return None, "commit SHA not found in API response"
    except HTTPError as exc:
        return None, f"commit query failed: HTTP {exc.code}"
    except URLError as exc:
        return None, f"commit query failed: {exc.reason}"


def extract_archive_to_source(owner: str, repo: str, ref: str, source_dir: Path, timeout: int) -> str:
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{quote(ref, safe='')}"
    request = Request(url, headers={"User-Agent": "github-feature-analyzer"})
    temp_root = Path(tempfile.mkdtemp(prefix="github-feature-analyzer-"))
    archive_path = temp_root / "repo.tar.gz"
    extract_root = temp_root / "extract"

    try:
        with urlopen(request, timeout=timeout) as response, archive_path.open("wb") as out_f:
            shutil.copyfileobj(response, out_f)

        extract_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "r:gz") as tar:
            safe_extract_tar(tar, extract_root)

        top_dirs = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(top_dirs) != 1:
            raise FetchError("unexpected archive layout")

        archive_repo_root = top_dirs[0]
        for child in archive_repo_root.iterdir():
            destination = source_dir / child.name
            shutil.move(str(child), destination)

        return f"archive download succeeded from {url}"
    except HTTPError as exc:
        raise FetchError(f"archive download failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise FetchError(f"archive download failed: {exc.reason}") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def clone_with_git(clone_url: str, ref: str, source_dir: Path, timeout: int) -> tuple[str, str]:
    shallow_cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        ref,
        clone_url,
        str(source_dir),
    ]
    shallow = run_cmd(shallow_cmd, timeout)
    if shallow.returncode == 0:
        sha = git_output(source_dir, ["rev-parse", "HEAD"], timeout)
        resolved_ref = git_output(source_dir, ["rev-parse", "--abbrev-ref", "HEAD"], timeout)
        return sha, resolved_ref

    shutil.rmtree(source_dir, ignore_errors=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    full_cmd = ["git", "clone", clone_url, str(source_dir)]
    full = run_cmd(full_cmd, timeout)
    if full.returncode != 0:
        raise FetchError(f"git clone failed: {full.stderr.strip() or full.stdout.strip()}")

    checkout = run_cmd(["git", "-C", str(source_dir), "checkout", ref], timeout)
    if checkout.returncode != 0:
        raise FetchError(
            f"git checkout failed for ref '{ref}': {checkout.stderr.strip() or checkout.stdout.strip()}"
        )

    sha = git_output(source_dir, ["rev-parse", "HEAD"], timeout)
    resolved_ref = git_output(source_dir, ["rev-parse", "--abbrev-ref", "HEAD"], timeout)
    return sha, resolved_ref


def git_output(source_dir: Path, args: list[str], timeout: int) -> str:
    completed = run_cmd(["git", "-C", str(source_dir), *args], timeout)
    if completed.returncode != 0:
        raise FetchError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout.strip()


def emit(result: dict) -> None:
    print(json.dumps(result, indent=2, sort_keys=True))


def build_ref_candidates(requested_ref: str, metadata: dict | None) -> list[str]:
    refs = [requested_ref]
    if metadata:
        default_branch = metadata.get("default_branch")
        if isinstance(default_branch, str):
            default_branch = default_branch.strip()
            if default_branch and default_branch not in refs:
                refs.append(default_branch)
    return refs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", required=True, help="GitHub repository URL")
    parser.add_argument("--ref", default="main", help="Git reference to analyze")
    parser.add_argument("--source-dir", required=True, help="Destination source directory")
    parser.add_argument(
        "--mode",
        default="mcp-first",
        choices=["mcp-first", "git-only", "api-only"],
        help="Fetch mode: archive-first with git fallback, git only, or archive only",
    )
    parser.add_argument(
        "--public-only",
        default="true",
        type=bool_arg,
        help="Reject private repositories when true",
    )
    parser.add_argument("--timeout", default=60, type=int, help="Network and command timeout in seconds")
    args = parser.parse_args()

    owner, repo, clone_url = parse_repo_url(args.repo_url)
    ref = args.ref.strip() or "main"
    source_dir = Path(args.source_dir).resolve()
    source_dir.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "repo_url": args.repo_url,
        "owner": owner,
        "repo": repo,
        "requested_ref": ref,
        "resolved_ref": ref,
        "mode": args.mode,
        "fetch_mode": None,
        "mcp_status": "skipped",
        "git_status": "skipped",
        "commit_sha": None,
        "source_dir": str(source_dir),
        "notes": [],
    }

    try:
        reset_note = ensure_clean_source_dir(source_dir)
        if reset_note:
            result["notes"].append(reset_note)

        metadata, visibility_note = query_repo_metadata(owner, repo, timeout=args.timeout)
        if visibility_note:
            result["notes"].append(visibility_note)

        if args.public_only and metadata and metadata.get("private") is True:
            raise FetchError("private repositories are out of scope for this skill")

        ref_candidates = build_ref_candidates(ref, metadata)
        if len(ref_candidates) > 1:
            result["notes"].append(
                f"requested ref '{ref}' will fall back to default branch '{ref_candidates[1]}' if needed"
            )

        archive_allowed = args.mode in {"mcp-first", "api-only"}
        git_allowed = args.mode in {"mcp-first", "git-only"}

        if archive_allowed:
            result["mcp_status"] = "running"
            for candidate_ref in ref_candidates:
                try:
                    note = extract_archive_to_source(
                        owner, repo, candidate_ref, source_dir, timeout=args.timeout
                    )
                    result["mcp_status"] = "success"
                    result["fetch_mode"] = "archive-download"
                    result["resolved_ref"] = candidate_ref
                    result["notes"].append(note)

                    commit_sha, commit_note = query_commit_sha(
                        owner, repo, candidate_ref, timeout=args.timeout
                    )
                    if commit_sha:
                        result["commit_sha"] = commit_sha
                    if commit_note:
                        result["notes"].append(commit_note)
                    break
                except FetchError as exc:
                    result["notes"].append(
                        f"archive attempt for ref '{candidate_ref}' failed: {exc}"
                    )
                    shutil.rmtree(source_dir, ignore_errors=True)
                    source_dir.mkdir(parents=True, exist_ok=True)
            if result["fetch_mode"] is None:
                result["mcp_status"] = "failed"

        if result["fetch_mode"] is None and git_allowed:
            result["git_status"] = "running"
            for candidate_ref in ref_candidates:
                try:
                    sha, resolved_ref = clone_with_git(
                        clone_url, candidate_ref, source_dir, timeout=args.timeout
                    )
                    result["git_status"] = "success"
                    result["fetch_mode"] = "git-clone"
                    result["commit_sha"] = sha
                    result["resolved_ref"] = (
                        candidate_ref if resolved_ref == "HEAD" else resolved_ref
                    )
                    break
                except FetchError as exc:
                    result["notes"].append(f"git attempt for ref '{candidate_ref}' failed: {exc}")
                    shutil.rmtree(source_dir, ignore_errors=True)
                    source_dir.mkdir(parents=True, exist_ok=True)
            if result["fetch_mode"] is None:
                result["git_status"] = "failed"

        if result["fetch_mode"] is None:
            raise FetchError("all retrieval paths failed")

        emit(result)
        return 0

    except FetchError as exc:
        result["error"] = str(exc)
        emit(result)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
