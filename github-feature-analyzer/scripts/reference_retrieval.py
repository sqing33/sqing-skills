#!/usr/bin/env python3
"""Retrieve historical feature-analysis references from .github-feature-analyzer using local embeddings."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MANIFEST_VERSION = 1

H2_PATTERN = re.compile(r"^##\s+(?!#)(.+?)\s*$")
H3_PATTERN = re.compile(r"^###\s+(?!#)(.+?)\s*$")
H4_PATTERN = re.compile(r"^####\s+(?!#)(.+?)\s*$")
EVIDENCE_PATTERN = re.compile(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+:\d+)")

REPORT_H2_SECTIONS = {
    "Project Characteristics and Signature Implementations": "report.project_characteristic",
    "Executive Principle Summary": "report.executive_summary",
    "Feature Principle Analysis": "report.feature_analysis",
    "Cross-feature Coupling and System Risks": "report.cross_feature_risks",
}

PRINCIPLE_KEYS = [
    "runtime_control_flow",
    "data_flow",
    "state_lifecycle",
    "failure_recovery",
    "concurrency_timing",
]


class RetrievalError(RuntimeError):
    """Raised when retrieval command should hard-fail."""


@dataclass
class IndexPaths:
    root: Path
    manifest: Path
    chunks: Path
    vectors: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_project_root() -> Path:
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


def default_storage_root() -> Path:
    return (discover_project_root() / ".github-feature-analyzer").resolve()


def resolve_roots(storage_root_arg: str | None, index_root_arg: str | None) -> tuple[Path, Path]:
    storage_root = Path(storage_root_arg).resolve() if storage_root_arg else default_storage_root()
    index_root = Path(index_root_arg).resolve() if index_root_arg else (storage_root / ".knowledge-index")
    return storage_root, index_root


def build_index_paths(index_root: Path) -> IndexPaths:
    return IndexPaths(
        root=index_root,
        manifest=index_root / "manifest.json",
        chunks=index_root / "chunks.jsonl",
        vectors=index_root / "vectors.npy",
    )


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_text(raw: str) -> str:
    text = "\n".join(line.rstrip() for line in raw.splitlines()).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def compact_excerpt(raw: str, max_chars: int = 420) -> str:
    text = " ".join(raw.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def extract_evidence_refs(raw: str) -> list[str]:
    refs = [m.group(1) for m in EVIDENCE_PATTERN.finditer(raw)]
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return deduped


def slugify(raw: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "section"


def split_h2_blocks(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current_title: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        match = H2_PATTERN.match(line)
        if match:
            if current_title is not None:
                blocks[current_title] = current_lines
            current_title = match.group(1).strip()
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        blocks[current_title] = current_lines
    return blocks


def split_by_heading(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_body: list[str] = []

    for line in lines:
        match = pattern.match(line)
        if match:
            if current_title is not None:
                blocks.append((current_title, current_body))
            current_title = match.group(1).strip()
            current_body = []
            continue
        if current_title is not None:
            current_body.append(line)

    if current_title is not None:
        blocks.append((current_title, current_body))
    return blocks


def make_chunk(
    *,
    repo: str,
    source_file: str,
    section_type: str,
    section_title: str,
    text: str,
    evidence_refs: list[str],
    order: int,
    timestamp: str,
) -> dict[str, Any]:
    normalized_text = normalize_text(text)
    if not normalized_text:
        raise RetrievalError(f"empty chunk text for {repo}/{source_file}/{section_title}")

    content_hash = sha256_bytes(normalized_text.encode("utf-8"))
    chunk_seed = "|".join([repo, source_file, section_type, section_title, content_hash, str(order)])
    chunk_id = hashlib.sha1(chunk_seed.encode("utf-8")).hexdigest()[:20]
    dedup_refs = []
    seen: set[str] = set()
    for ref in evidence_refs:
        if ref in seen:
            continue
        seen.add(ref)
        dedup_refs.append(ref)

    return {
        "chunk_id": chunk_id,
        "repo": repo,
        "source_file": source_file,
        "section_type": section_type,
        "section_title": section_title,
        "text": normalized_text,
        "evidence_refs": dedup_refs[:20],
        "updated_at": timestamp,
        "content_hash": content_hash,
    }


def build_report_chunks(repo: str, report_path: Path, timestamp: str) -> list[dict[str, Any]]:
    report_text = report_path.read_text(encoding="utf-8", errors="ignore")
    h2_blocks = split_h2_blocks(report_text)
    chunks: list[dict[str, Any]] = []
    order = 0

    for h2_title, section_type in REPORT_H2_SECTIONS.items():
        lines = h2_blocks.get(h2_title)
        if not lines:
            continue

        if section_type in {"report.project_characteristic", "report.executive_summary"}:
            h3_blocks = split_by_heading(lines, H3_PATTERN)
            if not h3_blocks:
                order += 1
                text = normalize_text("\n".join(lines))
                if text:
                    chunks.append(
                        make_chunk(
                            repo=repo,
                            source_file="report.md",
                            section_type=section_type,
                            section_title=h2_title,
                            text=text,
                            evidence_refs=extract_evidence_refs(text),
                            order=order,
                            timestamp=timestamp,
                        )
                    )
                continue

            for title, body in h3_blocks:
                body_text = normalize_text("\n".join(body))
                if not body_text:
                    continue
                order += 1
                chunk_text = f"{title}\n\n{body_text}"
                chunks.append(
                    make_chunk(
                        repo=repo,
                        source_file="report.md",
                        section_type=section_type,
                        section_title=title,
                        text=chunk_text,
                        evidence_refs=extract_evidence_refs(chunk_text),
                        order=order,
                        timestamp=timestamp,
                    )
                )
            continue

        if section_type == "report.feature_analysis":
            h3_blocks = split_by_heading(lines, H3_PATTERN)
            if not h3_blocks:
                order += 1
                text = normalize_text("\n".join(lines))
                if text:
                    chunks.append(
                        make_chunk(
                            repo=repo,
                            source_file="report.md",
                            section_type=section_type,
                            section_title=h2_title,
                            text=text,
                            evidence_refs=extract_evidence_refs(text),
                            order=order,
                            timestamp=timestamp,
                        )
                    )
                continue

            for feature_title, feature_body in h3_blocks:
                h4_blocks = split_by_heading(feature_body, H4_PATTERN)
                if not h4_blocks:
                    feature_text = normalize_text("\n".join(feature_body))
                    if not feature_text:
                        continue
                    order += 1
                    chunk_text = f"{feature_title}\n\n{feature_text}"
                    chunks.append(
                        make_chunk(
                            repo=repo,
                            source_file="report.md",
                            section_type=section_type,
                            section_title=feature_title,
                            text=chunk_text,
                            evidence_refs=extract_evidence_refs(chunk_text),
                            order=order,
                            timestamp=timestamp,
                        )
                    )
                    continue

                for subsection_title, subsection_body in h4_blocks:
                    subsection_text = normalize_text("\n".join(subsection_body))
                    if not subsection_text:
                        continue
                    order += 1
                    full_title = f"{feature_title} / {subsection_title}"
                    chunk_text = f"{full_title}\n\n{subsection_text}"
                    chunks.append(
                        make_chunk(
                            repo=repo,
                            source_file="report.md",
                            section_type=section_type,
                            section_title=full_title,
                            text=chunk_text,
                            evidence_refs=extract_evidence_refs(chunk_text),
                            order=order,
                            timestamp=timestamp,
                        )
                    )
            continue

        if section_type == "report.cross_feature_risks":
            section_text = normalize_text("\n".join(lines))
            if section_text:
                order += 1
                chunks.append(
                    make_chunk(
                        repo=repo,
                        source_file="report.md",
                        section_type=section_type,
                        section_title=h2_title,
                        text=section_text,
                        evidence_refs=extract_evidence_refs(section_text),
                        order=order,
                        timestamp=timestamp,
                    )
                )

    return chunks


def collect_subagent_evidence(raw: Any) -> list[str]:
    refs: list[str] = []
    if not isinstance(raw, list):
        return refs
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        line = item.get("line")
        if isinstance(path, str) and isinstance(line, int) and line > 0:
            refs.append(f"{path}:{line}")
    return refs


def build_subagent_chunks(repo: str, subagent_path: Path, timestamp: str) -> list[dict[str, Any]]:
    try:
        payload = load_json(subagent_path)
    except json.JSONDecodeError as exc:
        raise RetrievalError(f"invalid subagent json in {subagent_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RetrievalError(f"subagent payload must be json object: {subagent_path}")

    chunks: list[dict[str, Any]] = []
    order = 0
    source_file = "artifacts/subagent_results.json"

    overview = payload.get("overview")
    if isinstance(overview, dict):
        summary = overview.get("summary", "")
        if isinstance(summary, str) and summary.strip():
            notes = overview.get("notes", [])
            note_text = ""
            if isinstance(notes, list):
                note_items = [f"- {str(item)}" for item in notes if str(item).strip()]
                if note_items:
                    note_text = "\n\nNotes:\n" + "\n".join(note_items[:8])
            text = f"Overview\n\n{summary.strip()}{note_text}"
            order += 1
            chunks.append(
                make_chunk(
                    repo=repo,
                    source_file=source_file,
                    section_type="subagent.overview",
                    section_title="overview.summary",
                    text=text,
                    evidence_refs=collect_subagent_evidence(overview.get("evidence")),
                    order=order,
                    timestamp=timestamp,
                )
            )

    architecture = payload.get("architecture")
    if isinstance(architecture, dict):
        summary = architecture.get("summary", "")
        if isinstance(summary, str) and summary.strip():
            text = f"Architecture\n\n{summary.strip()}"
            order += 1
            chunks.append(
                make_chunk(
                    repo=repo,
                    source_file=source_file,
                    section_type="subagent.architecture",
                    section_title="architecture.summary",
                    text=text,
                    evidence_refs=collect_subagent_evidence(architecture.get("evidence")),
                    order=order,
                    timestamp=timestamp,
                )
            )

    features = payload.get("features")
    if isinstance(features, list):
        for idx, item in enumerate(features, start=1):
            if not isinstance(item, dict):
                continue
            feature_name = item.get("feature")
            summary = item.get("summary")
            if not isinstance(feature_name, str) or not feature_name.strip():
                continue
            if not isinstance(summary, str):
                summary = ""

            principles = item.get("principles")
            principle_lines: list[str] = []
            if isinstance(principles, dict):
                for key in PRINCIPLE_KEYS:
                    detail = principles.get(key)
                    if not isinstance(detail, dict):
                        continue
                    conclusion = detail.get("conclusion")
                    if isinstance(conclusion, str) and conclusion.strip():
                        principle_lines.append(f"{key}: {conclusion.strip()}")

            body_parts = [f"Feature: {feature_name.strip()}"]
            if summary.strip():
                body_parts.append(summary.strip())
            if principle_lines:
                body_parts.append("Principles:\n" + "\n".join(f"- {line}" for line in principle_lines))
            text = "\n\n".join(body_parts)
            if not text.strip():
                continue

            order += 1
            chunks.append(
                make_chunk(
                    repo=repo,
                    source_file=source_file,
                    section_type="subagent.feature",
                    section_title=f"feature.{idx}: {feature_name.strip()}",
                    text=text,
                    evidence_refs=collect_subagent_evidence(item.get("evidence")),
                    order=order,
                    timestamp=timestamp,
                )
            )

    return chunks


def collect_repo_dirs(storage_root: Path) -> list[Path]:
    if not storage_root.exists() or not storage_root.is_dir():
        return []
    repo_dirs = []
    for candidate in sorted(storage_root.iterdir(), key=lambda p: p.name):
        if not candidate.is_dir():
            continue
        if candidate.name.startswith("."):
            continue
        repo_dirs.append(candidate)
    return repo_dirs


def repo_hashes(repo_dir: Path) -> dict[str, str | None]:
    report_path = repo_dir / "report.md"
    subagent_path = repo_dir / "artifacts" / "subagent_results.json"
    return {
        "report_hash": file_sha256(report_path),
        "subagent_hash": file_sha256(subagent_path),
    }


def gather_repo_chunks(repo_dir: Path, timestamp: str) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    repo = repo_dir.name
    report_path = repo_dir / "report.md"
    subagent_path = repo_dir / "artifacts" / "subagent_results.json"
    hashes = repo_hashes(repo_dir)

    chunks: list[dict[str, Any]] = []
    if report_path.exists():
        chunks.extend(build_report_chunks(repo, report_path, timestamp))
    if subagent_path.exists():
        try:
            chunks.extend(build_subagent_chunks(repo, subagent_path, timestamp))
        except RetrievalError as exc:
            sys.stderr.write(
                f"[reference-retrieval] warning: skip subagent chunks for {repo}: {exc}\n"
            )

    return chunks, hashes


def load_manifest(paths: IndexPaths) -> dict[str, Any]:
    if not paths.manifest.exists():
        raise RetrievalError(f"manifest not found: {paths.manifest}")
    payload = load_json(paths.manifest)
    if not isinstance(payload, dict):
        raise RetrievalError(f"manifest must be json object: {paths.manifest}")
    return payload


def load_chunks(paths: IndexPaths) -> list[dict[str, Any]]:
    if not paths.chunks.exists():
        raise RetrievalError(f"chunks file not found: {paths.chunks}")
    rows: list[dict[str, Any]] = []
    with paths.chunks.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RetrievalError(f"invalid jsonl at {paths.chunks}:{line_no}: {exc}") from exc
            if not isinstance(item, dict):
                raise RetrievalError(f"invalid chunk row at {paths.chunks}:{line_no}")
            rows.append(item)
    return rows


def write_chunks(paths: IndexPaths, chunks: list[dict[str, Any]]) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    with paths.chunks.open("w", encoding="utf-8") as handle:
        for item in chunks:
            handle.write(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n")


def load_vector_stack(model_name: str) -> tuple[Any, Any]:
    scripts_dir = Path(__file__).resolve().parent
    setup_script = scripts_dir / "setup_reference_venv.sh"
    wrapper_script = scripts_dir / "reference_retrieval_uv.sh"
    install_hint = (
        "use UV bootstrap/setup before running retrieval:\n"
        f"bash {setup_script}\n"
        "then run:\n"
        f"bash {wrapper_script} query --query \"<your query>\""
    )

    try:
        import numpy as np  # type: ignore
    except ModuleNotFoundError as exc:
        raise RetrievalError(
            "missing dependency: numpy\n"
            f"{install_hint}"
        ) from exc

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ModuleNotFoundError as exc:
        raise RetrievalError(
            "missing dependency: sentence-transformers\n"
            f"{install_hint}"
        ) from exc

    try:
        model = SentenceTransformer(model_name)
    except Exception as exc:  # noqa: BLE001
        raise RetrievalError(f"failed to load embedding model '{model_name}': {exc}") from exc

    return np, model


def encode_texts(np: Any, model: Any, texts: list[str], batch_size: int = 32) -> Any:
    if not texts:
        return np.zeros((0, 0), dtype="float32")
    try:
        vectors = model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise RetrievalError(f"failed to encode texts: {exc}") from exc
    return vectors.astype("float32")


def validate_index_alignment(chunks: list[dict[str, Any]], vectors: Any) -> None:
    if len(chunks) != int(vectors.shape[0]):
        raise RetrievalError(
            f"index corrupted: chunks={len(chunks)} but vectors={int(vectors.shape[0])}"
        )


def collect_current_repo_hashes(storage_root: Path) -> dict[str, dict[str, str | None]]:
    hashes: dict[str, dict[str, str | None]] = {}
    for repo_dir in collect_repo_dirs(storage_root):
        h = repo_hashes(repo_dir)
        if not h.get("report_hash") and not h.get("subagent_hash"):
            continue
        hashes[repo_dir.name] = h
    return hashes


def repo_hash_changed(old_hash: dict[str, Any] | None, new_hash: dict[str, str | None] | None) -> bool:
    if old_hash is None and new_hash is None:
        return False
    if old_hash is None or new_hash is None:
        return True
    return (
        old_hash.get("report_hash") != new_hash.get("report_hash")
        or old_hash.get("subagent_hash") != new_hash.get("subagent_hash")
    )


def detect_stale_repos(storage_root: Path, manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    current_hashes = collect_current_repo_hashes(storage_root)
    manifest_repos = manifest.get("repos", {})
    old_hashes = manifest_repos if isinstance(manifest_repos, dict) else {}
    all_repos = sorted(set(current_hashes) | set(old_hashes))

    changed: list[str] = []
    for repo in all_repos:
        if repo_hash_changed(old_hashes.get(repo), current_hashes.get(repo)):
            changed.append(repo)
    return (len(changed) > 0), changed


def sort_entries(entries: list[tuple[dict[str, Any], Any]]) -> list[tuple[dict[str, Any], Any]]:
    return sorted(
        entries,
        key=lambda item: (
            item[0].get("repo", ""),
            item[0].get("source_file", ""),
            item[0].get("section_type", ""),
            item[0].get("section_title", ""),
            item[0].get("chunk_id", ""),
        ),
    )


def build_index(
    *,
    storage_root: Path,
    index_root: Path,
    model_name: str,
    force: bool,
) -> dict[str, Any]:
    paths = build_index_paths(index_root)
    timestamp = now_iso()
    current_hashes = collect_current_repo_hashes(storage_root)
    if not current_hashes:
        raise RetrievalError(
            f"no report.md or artifacts/subagent_results.json found under {storage_root}"
        )

    np, model = load_vector_stack(model_name)
    changed_repos: list[str] = []
    build_mode = "full"

    entries: list[tuple[dict[str, Any], Any]] = []
    old_manifest: dict[str, Any] | None = None
    old_chunks: list[dict[str, Any]] = []
    old_vectors: Any = None

    if not force and paths.manifest.exists() and paths.chunks.exists() and paths.vectors.exists():
        old_manifest = load_manifest(paths)
        if old_manifest.get("model_name") == model_name:
            old_chunks = load_chunks(paths)
            old_vectors = np.load(paths.vectors)
            validate_index_alignment(old_chunks, old_vectors)
            old_repo_hashes = old_manifest.get("repos", {})
            if not isinstance(old_repo_hashes, dict):
                old_repo_hashes = {}
            all_repos = sorted(set(current_hashes) | set(old_repo_hashes))
            for repo in all_repos:
                if repo_hash_changed(old_repo_hashes.get(repo), current_hashes.get(repo)):
                    changed_repos.append(repo)

            if changed_repos:
                build_mode = "incremental"
                for idx, chunk in enumerate(old_chunks):
                    repo = chunk.get("repo")
                    if not isinstance(repo, str):
                        continue
                    if repo in changed_repos:
                        continue
                    if repo not in current_hashes:
                        continue
                    entries.append((chunk, old_vectors[idx]))
            else:
                build_mode = "unchanged"
                for idx, chunk in enumerate(old_chunks):
                    entries.append((chunk, old_vectors[idx]))

    if build_mode == "full":
        repos_to_build = sorted(current_hashes)
    elif build_mode == "incremental":
        repos_to_build = sorted([repo for repo in changed_repos if repo in current_hashes])
    else:
        repos_to_build = []

    new_chunks: list[dict[str, Any]] = []
    if repos_to_build:
        for repo in repos_to_build:
            repo_dir = storage_root / repo
            repo_chunks, _hashes = gather_repo_chunks(repo_dir, timestamp)
            new_chunks.extend(repo_chunks)

        if not new_chunks and build_mode == "full":
            raise RetrievalError("no chunk extracted from current reports")

        if new_chunks:
            vectors = encode_texts(np, model, [item["text"] for item in new_chunks], batch_size=32)
            for idx, chunk in enumerate(new_chunks):
                entries.append((chunk, vectors[idx]))

    entries = sort_entries(entries)
    if not entries:
        raise RetrievalError("no index entries available after build")

    chunks = [chunk for chunk, _vec in entries]
    vector_rows = [vec for _chunk, vec in entries]
    vector_matrix = np.vstack(vector_rows).astype("float32")

    repo_counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        repo = chunk.get("repo")
        if isinstance(repo, str):
            repo_counts[repo] += 1

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "model_name": model_name,
        "embedding_dim": int(vector_matrix.shape[1]),
        "created_at": (old_manifest.get("created_at") if isinstance(old_manifest, dict) else timestamp),
        "updated_at": timestamp,
        "build_mode": build_mode,
        "repos": {
            repo: {
                "report_hash": hashes.get("report_hash"),
                "subagent_hash": hashes.get("subagent_hash"),
                "chunk_count": int(repo_counts.get(repo, 0)),
                "updated_at": timestamp,
            }
            for repo, hashes in sorted(current_hashes.items())
        },
    }

    paths.root.mkdir(parents=True, exist_ok=True)
    write_json(paths.manifest, manifest)
    write_chunks(paths, chunks)
    np.save(paths.vectors, vector_matrix)

    return {
        "status": "ok",
        "build_mode": build_mode,
        "storage_root": str(storage_root),
        "index_root": str(paths.root),
        "manifest_path": str(paths.manifest),
        "chunks_path": str(paths.chunks),
        "vectors_path": str(paths.vectors),
        "model_name": model_name,
        "repo_count": len(current_hashes),
        "chunk_count": len(chunks),
        "embedding_dim": int(vector_matrix.shape[1]),
        "changed_repos": changed_repos,
    }


def ensure_index(
    *,
    storage_root: Path,
    index_root: Path,
    model_name: str,
    refresh: str,
) -> dict[str, Any] | None:
    paths = build_index_paths(index_root)

    if refresh == "force":
        return build_index(storage_root=storage_root, index_root=index_root, model_name=model_name, force=True)

    if not paths.manifest.exists() or not paths.chunks.exists() or not paths.vectors.exists():
        if refresh == "never":
            raise RetrievalError(f"index not found under {index_root}, run build first")
        return build_index(storage_root=storage_root, index_root=index_root, model_name=model_name, force=False)

    if refresh == "never":
        return None

    manifest = load_manifest(paths)
    if manifest.get("model_name") != model_name:
        return build_index(storage_root=storage_root, index_root=index_root, model_name=model_name, force=True)

    stale, changed = detect_stale_repos(storage_root, manifest)
    if stale:
        _ = changed
        return build_index(storage_root=storage_root, index_root=index_root, model_name=model_name, force=False)
    return None


def score_hits(
    *,
    np: Any,
    model: Any,
    chunks: list[dict[str, Any]],
    vectors: Any,
    query: str,
    top_k: int,
    min_score: float,
    repo_filters: set[str],
) -> list[dict[str, Any]]:
    if not query.strip():
        raise RetrievalError("query cannot be empty")
    if vectors.shape[0] == 0:
        raise RetrievalError("index has zero vectors")
    validate_index_alignment(chunks, vectors)

    query_vec = encode_texts(np, model, [query.strip()], batch_size=1)
    if query_vec.shape[0] == 0:
        raise RetrievalError("failed to encode query")
    query_row = query_vec[0]

    scores = vectors @ query_row
    if top_k <= 0:
        top_k = 1
    k = min(top_k, int(scores.shape[0]))

    ranked_idx = np.argpartition(-scores, k - 1)[:k]
    ranked_idx = ranked_idx[np.argsort(scores[ranked_idx])[::-1]]

    hits: list[dict[str, Any]] = []
    for idx in ranked_idx.tolist():
        chunk = chunks[idx]
        repo = chunk.get("repo")
        if not isinstance(repo, str):
            continue
        if repo_filters and repo not in repo_filters:
            continue

        score = float(scores[idx])
        if score < min_score:
            continue

        text = chunk.get("text", "")
        if not isinstance(text, str):
            text = ""

        evidence_refs = chunk.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            evidence_refs = []

        hits.append(
            {
                "score": round(score, 6),
                "repo": repo,
                "chunk_id": chunk.get("chunk_id"),
                "source_file": chunk.get("source_file"),
                "section_type": chunk.get("section_type"),
                "section_title": chunk.get("section_title"),
                "evidence_refs": [str(item) for item in evidence_refs[:10]],
                "text_excerpt": compact_excerpt(text),
                "text": text,
            }
        )

    return hits


def format_mode_hits(hits: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "compact":
        return hits[:12]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in hits:
        grouped[item["repo"]].append(item)

    per_repo_limit = 6 if mode == "semi" else 20
    repo_order = sorted(
        grouped.keys(),
        key=lambda repo: max((entry["score"] for entry in grouped[repo]), default=0.0),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    for repo in repo_order:
        repo_items = sorted(grouped[repo], key=lambda item: item["score"], reverse=True)
        selected.extend(repo_items[:per_repo_limit])
    return selected


def render_markdown(
    *,
    query: str,
    mode: str,
    model_name: str,
    selected_hits: list[dict[str, Any]],
    total_hits: int,
) -> str:
    lines: list[str] = []
    lines.append("# Historical Reference Retrieval")
    lines.append("")
    lines.append(f"- Query: `{query}`")
    lines.append(f"- Mode: `{mode}`")
    lines.append(f"- Embedding Model: `{model_name}`")
    lines.append(f"- Retrieved Hits (before mode shaping): `{total_hits}`")
    lines.append(f"- Output Hits: `{len(selected_hits)}`")
    lines.append("")

    if not selected_hits:
        lines.append("No hit above threshold.")
        return "\n".join(lines).strip() + "\n"

    if mode == "compact":
        lines.append("## Compact Results")
        lines.append("")
        for item in selected_hits:
            evidence = ", ".join(f"`{ref}`" for ref in item["evidence_refs"][:4]) or "`none`"
            lines.append(f"### [{item['score']:.4f}] {item['repo']} / {item['section_title']}")
            lines.append(f"- Source: `{item['source_file']}` (`{item['section_type']}`)")
            lines.append(f"- Evidence: {evidence}")
            lines.append(f"- Excerpt: {item['text_excerpt']}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected_hits:
        grouped[item["repo"]].append(item)

    repo_order = sorted(
        grouped.keys(),
        key=lambda repo: max((entry["score"] for entry in grouped[repo]), default=0.0),
        reverse=True,
    )

    for repo in repo_order:
        repo_hits = sorted(grouped[repo], key=lambda item: item["score"], reverse=True)
        max_score = repo_hits[0]["score"] if repo_hits else 0.0
        avg_score = sum(item["score"] for item in repo_hits) / max(len(repo_hits), 1)
        lines.append(f"## Repo: `{repo}`")
        lines.append(f"- Max Score: `{max_score:.4f}`")
        lines.append(f"- Avg Score: `{avg_score:.4f}`")
        lines.append(f"- Hit Count: `{len(repo_hits)}`")
        lines.append("")

        for item in repo_hits:
            evidence = ", ".join(f"`{ref}`" for ref in item["evidence_refs"][:6]) or "`none`"
            lines.append(f"### [{item['score']:.4f}] {item['section_title']}")
            lines.append(f"- Source: `{item['source_file']}` (`{item['section_type']}`)")
            lines.append(f"- Evidence: {evidence}")
            lines.append(f"- Excerpt: {item['text_excerpt']}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_json(
    *,
    query: str,
    mode: str,
    model_name: str,
    selected_hits: list[dict[str, Any]],
    total_hits: int,
) -> str:
    payload = {
        "query": query,
        "mode": mode,
        "model_name": model_name,
        "retrieved_hits": total_hits,
        "output_hits": len(selected_hits),
        "hits": selected_hits,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def cmd_build(args: argparse.Namespace) -> int:
    storage_root, index_root = resolve_roots(args.storage_root, args.index_root)
    summary = build_index(
        storage_root=storage_root,
        index_root=index_root,
        model_name=args.model,
        force=args.force,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    storage_root, index_root = resolve_roots(args.storage_root, args.index_root)
    refresh_summary = ensure_index(
        storage_root=storage_root,
        index_root=index_root,
        model_name=args.model,
        refresh=args.refresh,
    )

    paths = build_index_paths(index_root)
    manifest = load_manifest(paths)
    np, model = load_vector_stack(args.model)
    chunks = load_chunks(paths)
    vectors = np.load(paths.vectors)
    validate_index_alignment(chunks, vectors)

    repo_filters = {item.strip() for item in args.repo if item.strip()}
    hits = score_hits(
        np=np,
        model=model,
        chunks=chunks,
        vectors=vectors,
        query=args.query,
        top_k=args.top_k,
        min_score=args.min_score,
        repo_filters=repo_filters,
    )
    selected_hits = format_mode_hits(hits, args.mode)

    if args.format == "json":
        output = render_json(
            query=args.query,
            mode=args.mode,
            model_name=args.model,
            selected_hits=selected_hits,
            total_hits=len(hits),
        )
    else:
        output = render_markdown(
            query=args.query,
            mode=args.mode,
            model_name=args.model,
            selected_hits=selected_hits,
            total_hits=len(hits),
        )

    if refresh_summary:
        sys.stderr.write(
            "[reference-retrieval] index refreshed: "
            f"mode={refresh_summary.get('build_mode')} chunks={refresh_summary.get('chunk_count')}\n"
        )
    elif args.refresh != "never":
        stale, changed = detect_stale_repos(storage_root, manifest)
        if stale:
            sys.stderr.write(
                "[reference-retrieval] warning: stale index detected after query path "
                f"(changed repos: {', '.join(changed)})\n"
            )

    sys.stdout.write(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser_obj = subparsers.add_parser("build", help="Build or refresh local reference index")
    build_parser_obj.add_argument("--storage-root", default=None)
    build_parser_obj.add_argument("--index-root", default=None)
    build_parser_obj.add_argument("--model", default=DEFAULT_MODEL)
    build_parser_obj.add_argument("--force", action="store_true")
    build_parser_obj.set_defaults(handler=cmd_build)

    query_parser_obj = subparsers.add_parser("query", help="Query reference index")
    query_parser_obj.add_argument("--query", required=True)
    query_parser_obj.add_argument("--storage-root", default=None)
    query_parser_obj.add_argument("--index-root", default=None)
    query_parser_obj.add_argument("--model", default=DEFAULT_MODEL)
    query_parser_obj.add_argument("--mode", choices=["compact", "semi", "full"], default="semi")
    query_parser_obj.add_argument("--format", choices=["markdown", "json"], default="markdown")
    query_parser_obj.add_argument("--repo", action="append", default=[])
    query_parser_obj.add_argument("--top-k", type=int, default=80)
    query_parser_obj.add_argument("--min-score", type=float, default=0.35)
    query_parser_obj.add_argument("--refresh", choices=["auto", "never", "force"], default="auto")
    query_parser_obj.set_defaults(handler=cmd_query)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except RetrievalError as exc:
        sys.stderr.write(f"[reference-retrieval] error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
