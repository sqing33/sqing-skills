#!/usr/bin/env python3
"""Build a lightweight code index to support feature-implementation analysis."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
MAX_SYMBOLS = 800
MAX_SYMBOL_SCAN_LINES = 1600

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".cache",
    "coverage",
    "vendor",
    "tmp",
    "temp",
    "__pycache__",
    ".venv",
    "venv",
}

LANG_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".hpp": "C/C++ Header",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".json": "JSON",
    ".md": "Markdown",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".proto": "Protocol Buffers",
}

LANG_BY_FILENAME = {
    "Dockerfile": "Docker",
    "Makefile": "Make",
    "CMakeLists.txt": "CMake",
    "Jenkinsfile": "Jenkins",
}

KEY_FILE_NAMES = {
    "README.md",
    "README",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "npm-shrinkwrap.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "Gemfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
}

ENTRY_HINTS = [
    "main",
    "index",
    "app",
    "server",
    "router",
    "route",
    "api",
    "controller",
]

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".jar",
    ".war",
    ".dll",
    ".so",
    ".dylib",
    ".exe",
    ".bin",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
}

SYMBOL_PATTERNS = [
    (re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("), "function"),
    (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("), "function"),
    (
        re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\("
        ),
        "function",
    ),
    (re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\("), "function"),
    (re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("), "function"),
    (
        re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
        "type",
    ),
]


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False
    try:
        with path.open("rb") as file_obj:
            sample = file_obj.read(8192)
    except OSError:
        return False
    if b"\x00" in sample:
        return False
    return True


def detect_language(path: Path) -> str:
    by_name = LANG_BY_FILENAME.get(path.name)
    if by_name:
        return by_name
    return LANG_BY_EXTENSION.get(path.suffix.lower(), "Unknown")


def is_test_file(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return any(token in lowered for token in ["/test", "_test.", ".test.", ".spec.", "tests/"])


def should_ignore_dir(name: str) -> bool:
    return name in IGNORED_DIRS or name.startswith(".") and name not in {".github"}


def iter_candidate_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_ignore_dir(directory)]
        root_path = Path(root)
        for file_name in filenames:
            path = root_path / file_name
            try:
                if path.stat().st_size > MAX_FILE_SIZE_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
    return files


def key_file_score(relative_path: str) -> int:
    score = 0
    lowered = relative_path.lower()
    base = Path(relative_path).name

    if base in KEY_FILE_NAMES:
        score += 4
    if relative_path.startswith(".github/workflows/"):
        score += 2
    if relative_path.startswith("src/"):
        score += 1
    if any(f"/{hint}" in lowered or lowered.startswith(hint) for hint in ENTRY_HINTS):
        score += 1

    return score


def extract_symbols(path: Path, relative_path: str) -> list[dict[str, object]]:
    symbols: list[dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file_obj:
            for line_no, line in enumerate(file_obj, start=1):
                if line_no > MAX_SYMBOL_SCAN_LINES:
                    break
                for pattern, symbol_kind in SYMBOL_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        symbols.append(
                            {
                                "name": match.group(1),
                                "kind": symbol_kind,
                                "path": relative_path,
                                "line": line_no,
                            }
                        )
                        break
                if len(symbols) >= 40:
                    break
    except OSError:
        return []
    return symbols


def build_index(source_dir: Path) -> dict[str, object]:
    all_files = iter_candidate_files(source_dir)
    language_counter: Counter[str] = Counter()
    directory_counter: Counter[str] = Counter()

    indexed_files: list[dict[str, object]] = []
    key_files: list[tuple[int, str]] = []
    symbols: list[dict[str, object]] = []

    for file_path in all_files:
        relative_path = file_path.relative_to(source_dir).as_posix()
        parent = str(Path(relative_path).parent)
        directory_counter[parent] += 1

        if not is_text_file(file_path):
            indexed_files.append(
                {
                    "path": relative_path,
                    "size": file_path.stat().st_size,
                    "language": "Binary",
                    "is_test": is_test_file(relative_path),
                    "is_text": False,
                }
            )
            continue

        language = detect_language(file_path)
        language_counter[language] += 1

        indexed_files.append(
            {
                "path": relative_path,
                "size": file_path.stat().st_size,
                "language": language,
                "is_test": is_test_file(relative_path),
                "is_text": True,
            }
        )

        score = key_file_score(relative_path)
        if score > 0:
            key_files.append((score, relative_path))

        if len(symbols) < MAX_SYMBOLS:
            symbols.extend(extract_symbols(file_path, relative_path))

    key_files_sorted = [path for _score, path in sorted(key_files, key=lambda item: (-item[0], item[1]))][:80]
    entry_candidates = [
        item["path"]
        for item in indexed_files
        if item["is_text"] and any(hint in item["path"].lower() for hint in ENTRY_HINTS)
    ][:120]

    top_directories = [
        {"path": path, "file_count": count}
        for path, count in sorted(directory_counter.items(), key=lambda item: (-item[1], item[0]))[:30]
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir.resolve()),
        "total_files": len(all_files),
        "indexed_text_files": sum(1 for item in indexed_files if item["is_text"]),
        "languages": dict(sorted(language_counter.items(), key=lambda item: (-item[1], item[0]))),
        "key_files": key_files_sorted,
        "entry_candidates": entry_candidates,
        "directories": top_directories,
        "symbols": symbols[:MAX_SYMBOLS],
        "files": indexed_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, help="Path to checked-out source directory")
    parser.add_argument("--output", required=True, help="Path to output JSON file")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"source-dir is not a directory: {source_dir}")

    index = build_index(source_dir)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(output_path),
                "total_files": index["total_files"],
                "indexed_text_files": index["indexed_text_files"],
                "languages": index["languages"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
