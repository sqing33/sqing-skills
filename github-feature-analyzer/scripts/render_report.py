#!/usr/bin/env python3
"""Render a principle-first feature report from repository index and optional sub-agent results."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_SCAN_FILES = 3000
MAX_SCAN_FILE_SIZE = 600 * 1024
MAX_LINE_HITS_PER_FILE = 12
MAX_FEATURE_FILES = 8
MAX_FEATURE_EVIDENCE = 24
MAX_CHARACTERISTICS = 3
MAX_CHARACTERISTIC_EVIDENCE_REFS = 2
MAX_README_SIGNAL_LENGTH = 140

DOC_FILE_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
DOC_PATH_HINTS = ("docs/", "doc/", "documentation/", ".github/")
README_CANDIDATE_FILES = [
    "README.md",
    "README",
    "readme.md",
    "Readme.md",
    "README.rst",
    "README.txt",
]
README_SKIP_TERMS = {
    "license",
    "contributing",
    "contribution",
    "installation",
    "install",
    "quick start",
    "getting started",
    "usage",
    "faq",
    "changelog",
    "roadmap",
    "author",
    "贡献",
    "安装",
    "快速开始",
    "使用",
    "常见问题",
    "许可证",
    "更新日志",
}
README_FEATURE_TERMS = {
    "feature",
    "features",
    "architecture",
    "workflow",
    "pipeline",
    "orchestration",
    "runtime",
    "execution",
    "api",
    "cli",
    "sdk",
    "agent",
    "agents",
    "功能",
    "特性",
    "架构",
    "工作流",
    "机制",
    "实现",
    "执行",
    "接口",
    "调度",
    "智能体",
}
SDK_CLI_HINT_TERMS = {
    "sdk",
    "cli",
    "command",
    "sub-agent",
    "subagent",
    "multi-agent",
    "agent",
    "working_dir",
    "current_dir",
    "workdir",
    "container",
    "路径映射",
    "工作目录",
    "子代理",
    "多代理",
}

STOPWORDS = {
    "how",
    "what",
    "which",
    "is",
    "are",
    "the",
    "and",
    "or",
    "for",
    "with",
    "from",
    "if",
    "then",
    "for",
    "about",
    "feature",
    "features",
    "对于",
    "不同",
    "如果",
    "那么",
    "如何",
    "怎么",
    "功能",
    "还是",
}

DIMENSION_KEYS = [
    "runtime_control_flow",
    "data_flow",
    "state_lifecycle",
    "failure_recovery",
    "concurrency_timing",
]

DIMENSION_TOKENS = {
    "runtime_control_flow": {
        "run",
        "start",
        "handle",
        "dispatch",
        "route",
        "execute",
        "invoke",
        "process",
        "handler",
        "controller",
        "entry",
        "trigger",
    },
    "data_flow": {
        "input",
        "output",
        "request",
        "response",
        "payload",
        "parse",
        "serialize",
        "deserialize",
        "json",
        "yaml",
        "query",
        "sql",
        "transform",
        "mapper",
    },
    "state_lifecycle": {
        "state",
        "status",
        "init",
        "create",
        "update",
        "delete",
        "open",
        "close",
        "lifecycle",
        "session",
        "cache",
        "persist",
        "store",
        "registry",
    },
    "failure_recovery": {
        "error",
        "err",
        "failed",
        "failure",
        "retry",
        "fallback",
        "timeout",
        "exception",
        "recover",
        "rollback",
        "panic",
        "abort",
    },
    "concurrency_timing": {
        "async",
        "await",
        "parallel",
        "concurrent",
        "thread",
        "goroutine",
        "tokio",
        "spawn",
        "queue",
        "lock",
        "mutex",
        "semaphore",
        "channel",
        "race",
    },
}

LABELS = {
    "zh": {
        "title": "GitHub 功能实现原理报告",
        "metadata": "元数据",
        "repo": "仓库",
        "ref": "请求 Ref",
        "resolved_ref": "解析 Ref",
        "commit": "Commit",
        "generated_at": "生成时间",
        "depth": "分析深度",
        "analysis_mode": "分析模式",
        "source_dir": "源码目录",
        "index_file": "索引文件",
        "subagent_file": "子代理结果",
        "overview": "仓库结构心智模型",
        "total_files": "文件总数",
        "indexed_text_files": "可检索文本文件",
        "top_languages": "主要语言",
        "entrypoints": "运行入口线索",
        "module_boundaries": "模块边界线索",
        "project_characteristics": "项目特点与标志实现",
        "characteristic": "项目特点",
        "characteristic_source": "来源",
        "characteristic_signal": "README 线索",
        "characteristic_mechanism": "实现机制",
        "executive_summary": "面向人的功能说明",
        "feature": "功能",
        "direct_answer": "直接回答",
        "confidence": "置信度",
        "key_evidence_refs": "关键证据引用",
        "feature_details": "面向 AI 的实现细节",
        "runtime_control_flow": "运行时控制流",
        "data_flow": "数据流",
        "state_lifecycle": "状态与生命周期",
        "failure_recovery": "失败与恢复",
        "concurrency_timing": "并发与时序",
        "invocation_classification": "调用路径分类",
        "invocation_type": "调用路径类型",
        "working_dir_resolution": "工作目录确定方式",
        "key_evidence": "关键证据",
        "unknowns": "推断与未知点",
        "deep_audit": "深度审计",
        "global_risks": "跨功能耦合与系统风险",
        "section_one": "第一部分：项目参数与结构解析",
        "section_two": "第二部分：面向人的功能说明",
        "section_three": "第三部分：面向 AI 的实现细节与证据链",
        "human_function_role": "功能作用",
        "human_special_capability": "特殊功能",
        "human_implementation_idea": "实现想法",
        "none": "无",
        "inference": "inference",
        "high": "high",
        "medium": "medium",
        "low": "low",
    },
    "en": {
        "title": "GitHub Feature Principle Report",
        "metadata": "Metadata",
        "repo": "Repository",
        "ref": "Requested Ref",
        "resolved_ref": "Resolved Ref",
        "commit": "Commit",
        "generated_at": "Generated At",
        "depth": "Analysis Depth",
        "analysis_mode": "Analysis Mode",
        "source_dir": "Source Directory",
        "index_file": "Index File",
        "subagent_file": "Sub-agent Result",
        "overview": "Repository Mental Model",
        "total_files": "Total Files",
        "indexed_text_files": "Indexed Text Files",
        "top_languages": "Top Languages",
        "entrypoints": "Runtime Entrypoint Signals",
        "module_boundaries": "Module Boundary Signals",
        "project_characteristics": "Project Characteristics and Signature Implementations",
        "characteristic": "Characteristic",
        "characteristic_source": "Source",
        "characteristic_signal": "README Signal",
        "characteristic_mechanism": "Implementation Mechanism",
        "executive_summary": "Executive Principle Summary",
        "feature": "Feature",
        "direct_answer": "Direct Answer",
        "confidence": "Confidence",
        "key_evidence_refs": "Key Evidence References",
        "feature_details": "Feature Principle Analysis",
        "runtime_control_flow": "Runtime Control Flow",
        "data_flow": "Data Flow",
        "state_lifecycle": "State and Lifecycle",
        "failure_recovery": "Failure and Recovery",
        "concurrency_timing": "Concurrency and Timing",
        "invocation_classification": "Invocation Path Classification",
        "invocation_type": "Invocation Type",
        "working_dir_resolution": "Working Directory Resolution",
        "key_evidence": "Key Evidence",
        "unknowns": "Inference and Unknowns",
        "deep_audit": "Deep Audit",
        "global_risks": "Cross-feature Coupling and System Risks",
        "section_one": "Part 1: Project Parameters and Structure",
        "section_two": "Part 2: Human-readable Feature Explanation",
        "section_three": "Part 3: AI-facing Mechanism Details and Evidence",
        "human_function_role": "Function Role",
        "human_special_capability": "Special Capability",
        "human_implementation_idea": "Implementation Idea",
        "none": "None",
        "inference": "inference",
        "high": "high",
        "medium": "medium",
        "low": "low",
    },
}


@dataclass
class FileEntry:
    path: str
    size: int
    language: str
    is_test: bool
    is_text: bool


@dataclass
class EvidenceHit:
    path: str
    line: int
    snippet: str
    keyword: str
    dimension: str
    weight: int


@dataclass
class FileScore:
    path: str
    score: int
    language: str
    is_test: bool
    hits: list[EvidenceHit]


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json object expected: {path}")
    return payload


def load_index(index_json: Path) -> dict:
    return load_json(index_json)


def normalize_feature_list(features: list[str]) -> list[str]:
    clean = [feature.strip() for feature in features if feature.strip()]
    if not clean:
        raise ValueError("feature list is empty")
    return clean


def tokenize_feature(feature: str) -> list[str]:
    lowered = feature.lower().strip()
    tokens: list[str] = []
    if lowered:
        tokens.append(lowered)

    ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", feature)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", feature)

    for token in ascii_tokens:
        value = token.lower()
        if value in STOPWORDS:
            continue
        tokens.append(value)

    for token in cjk_tokens:
        if token in STOPWORDS:
            continue
        tokens.append(token)

    deduped: list[str] = []
    seen = set()
    for token in tokens:
        normalized = token.strip().lower()
        if not normalized:
            continue
        if normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)

    return deduped


def safe_snippet(line: str) -> str:
    compact = " ".join(line.strip().split())
    compact = compact.replace("`", "'")
    return compact[:180]


def is_doc_like(path: str) -> bool:
    lowered = path.lower()
    suffix = Path(path).suffix.lower()
    if suffix in DOC_FILE_SUFFIXES:
        return True
    return lowered.startswith(DOC_PATH_HINTS)


def normalize_dedupe_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def normalize_markdown_line(line: str) -> str:
    normalized = line.strip()
    normalized = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", normalized)
    normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", normalized)
    normalized = re.sub(r"^#{1,6}\s*", "", normalized)
    normalized = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", normalized)
    normalized = re.sub(r"`+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def is_readme_noise(line: str) -> bool:
    if not line:
        return True
    if len(line) < 8:
        return True
    lowered = line.lower()
    if "shields.io" in lowered:
        return True
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return True

    has_feature_hint = any(term in lowered for term in README_FEATURE_TERMS)
    has_skip_hint = any(term in lowered for term in README_SKIP_TERMS)
    if has_skip_hint and not has_feature_hint:
        return True
    return False


def load_primary_readme(source_dir: Path) -> tuple[str | None, str | None]:
    for name in README_CANDIDATE_FILES:
        candidate = source_dir / name
        if candidate.exists() and candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            return candidate.relative_to(source_dir).as_posix(), text

    for candidate in sorted(source_dir.iterdir(), key=lambda item: item.name.lower()):
        if not candidate.is_file():
            continue
        if not candidate.name.lower().startswith("readme"):
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore")
        return candidate.relative_to(source_dir).as_posix(), text

    return None, None


def extract_readme_signals(readme_text: str) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    in_code_block = False

    for order, raw_line in enumerate(readme_text.splitlines()):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        is_heading = bool(re.match(r"^\s*#{1,6}\s+", raw_line))
        is_bullet = bool(re.match(r"^\s*(?:[-*+]|\d+\.)\s+", raw_line))
        normalized = normalize_markdown_line(raw_line)
        if is_readme_noise(normalized):
            continue

        if len(normalized) > MAX_README_SIGNAL_LENGTH:
            normalized = normalized[:MAX_README_SIGNAL_LENGTH].rstrip() + "..."

        lowered = normalized.lower()
        score = 0
        if is_heading:
            score += 4
        if is_bullet:
            score += 2
        if any(term in lowered for term in README_FEATURE_TERMS):
            score += 3
        if any(term in lowered for term in README_SKIP_TERMS):
            score -= 2
        if not is_heading and not is_bullet:
            score -= 1
        if score <= 0:
            continue
        scored.append((score, order, normalized))

    deduped: list[str] = []
    seen = set()
    for _score, _order, line in sorted(scored, key=lambda item: (-item[0], item[1])):
        key = normalize_dedupe_key(line)
        if not key or key in seen:
            continue
        deduped.append(line)
        seen.add(key)
        if len(deduped) >= 10:
            break
    return deduped


def infer_characteristics_from_index(index: dict, language: str) -> list[str]:
    key_files = [item for item in index.get("key_files", []) if isinstance(item, str)]
    entrypoints = [item for item in index.get("entry_candidates", []) if isinstance(item, str)]
    directories = [
        item.get("path")
        for item in index.get("directories", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    path_pool = " ".join([*key_files[:40], *entrypoints[:40], *directories[:20]]).lower()

    candidates: list[str] = []

    if language == "zh":
        if entrypoints:
            candidates.append(f"启动入口与运行主链路（{entrypoints[0]}）")
        if any(token in path_pool for token in ["api", "route", "handler", "controller", "server"]):
            candidates.append("外部接口接入、分发与执行机制")
        if any(token in path_pool for token in ["scheduler", "queue", "worker", "job", "task"]):
            candidates.append("任务调度与并发时序控制机制")
        if any(token in path_pool for token in ["store", "state", "db", "cache", "repository"]):
            candidates.append("状态持久化与生命周期管理机制")
        if directories:
            candidates.append(f"模块边界协作与职责划分（{directories[0]}）")
        candidates.append("核心能力闭环与失败恢复机制")
    else:
        if entrypoints:
            candidates.append(f"Runtime bootstrap and entry execution chain ({entrypoints[0]})")
        if any(token in path_pool for token in ["api", "route", "handler", "controller", "server"]):
            candidates.append("External interface ingestion, dispatch, and execution mechanism")
        if any(token in path_pool for token in ["scheduler", "queue", "worker", "job", "task"]):
            candidates.append("Task orchestration and concurrency timing mechanism")
        if any(token in path_pool for token in ["store", "state", "db", "cache", "repository"]):
            candidates.append("State persistence and lifecycle management mechanism")
        if directories:
            candidates.append(f"Module boundaries and responsibility partitioning ({directories[0]})")
        candidates.append("Core capability closure and failure recovery mechanism")

    deduped: list[str] = []
    seen = set()
    for candidate in candidates:
        key = normalize_dedupe_key(candidate)
        if not key or key in seen:
            continue
        deduped.append(candidate)
        seen.add(key)
        if len(deduped) >= MAX_CHARACTERISTICS:
            break
    return deduped


def build_characteristic_candidates(source_dir: Path, index: dict, language: str) -> tuple[list[dict], str | None]:
    readme_path, readme_text = load_primary_readme(source_dir)
    candidates: list[dict] = []
    seen = set()

    if readme_text:
        for signal in extract_readme_signals(readme_text):
            key = normalize_dedupe_key(signal)
            if not key or key in seen:
                continue
            candidates.append(
                {
                    "title": signal,
                    "source": "readme",
                    "readme_signal": signal,
                }
            )
            seen.add(key)
            if len(candidates) >= MAX_CHARACTERISTICS:
                break

    if len(candidates) < MAX_CHARACTERISTICS:
        for fallback in infer_characteristics_from_index(index, language):
            key = normalize_dedupe_key(fallback)
            if not key or key in seen:
                continue
            candidates.append(
                {
                    "title": fallback,
                    "source": "inference",
                    "readme_signal": None,
                }
            )
            seen.add(key)
            if len(candidates) >= MAX_CHARACTERISTICS:
                break

    return candidates, readme_path


def should_classify_invocation_path(feature: str) -> bool:
    lowered = feature.lower()
    return any(term in lowered for term in SDK_CLI_HINT_TERMS)


def classify_invocation_path(feature_result: dict, lang: str) -> dict:
    evidence_items = feature_result.get("evidence_items", [])
    signal_pool_parts = []
    for path, _line, snippet, _dimension in evidence_items:
        signal_pool_parts.append(path.lower())
        signal_pool_parts.append(snippet.lower())
    signal_pool = " ".join(signal_pool_parts)

    sdk_terms = {"sdk", "client", "openai", "anthropic", "httpx", "requests", "grpc"}
    cli_terms = {"cli", "command", "subprocess", "spawn", "exec", "shell", "pty", "argv"}
    wd_terms = {"working_dir", "current_dir", "workdir", "cwd", "chdir", "container", "mount", "volume"}

    has_sdk = any(term in signal_pool for term in sdk_terms)
    has_cli = any(term in signal_pool for term in cli_terms)
    has_workdir = any(term in signal_pool for term in wd_terms)

    if has_sdk and has_cli:
        mode = "hybrid"
    elif has_sdk:
        mode = "sdk"
    elif has_cli:
        mode = "cli"
    else:
        mode = "inference"

    if lang == "zh":
        mode_text = {
            "hybrid": "Hybrid（SDK + CLI）",
            "sdk": "SDK",
            "cli": "CLI",
            "inference": "inference（证据不足）",
        }[mode]
        if has_workdir:
            working_dir_text = "在证据中检测到 `working_dir/current_dir/cwd` 线索，可推断工作目录由运行参数或容器映射共同决定。"
        else:
            working_dir_text = "未发现稳定 `working_dir/current_dir/cwd` 证据，当前仅能做 inference 级别判定。"
    else:
        mode_text = {
            "hybrid": "Hybrid (SDK + CLI)",
            "sdk": "SDK",
            "cli": "CLI",
            "inference": "inference (insufficient direct evidence)",
        }[mode]
        if has_workdir:
            working_dir_text = "Signals for `working_dir/current_dir/cwd` are present; the working directory is likely resolved by runtime args and/or container mapping."
        else:
            working_dir_text = "No stable `working_dir/current_dir/cwd` signal was found; this remains inference-level only."

    return {"mode_text": mode_text, "working_dir_text": working_dir_text}


def classify_dimension(text: str) -> str:
    lowered = text.lower()
    for key in DIMENSION_KEYS:
        for token in DIMENSION_TOKENS[key]:
            if token in lowered:
                return key
    return "runtime_control_flow"


def score_feature_against_files(
    source_dir: Path,
    files: list[FileEntry],
    keywords: list[str],
) -> tuple[list[FileScore], dict]:
    scored: list[FileScore] = []
    truncated_files = 0

    for index, entry in enumerate(files):
        if index >= MAX_SCAN_FILES:
            truncated_files = len(files) - MAX_SCAN_FILES
            break

        if not entry.is_text:
            continue
        if entry.size > MAX_SCAN_FILE_SIZE:
            continue

        full_path = source_dir / entry.path
        if not full_path.exists() or not full_path.is_file():
            continue

        try:
            lines = full_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        hits: list[EvidenceHit] = []
        score = 0
        path_lower = entry.path.lower()

        for keyword in keywords:
            if keyword and keyword in path_lower:
                score += 2

        if is_doc_like(entry.path):
            score -= 2

        for line_no, line in enumerate(lines, start=1):
            lowered = line.lower()
            for keyword in keywords:
                if not keyword:
                    continue
                if keyword in lowered:
                    dimension = classify_dimension(lowered)
                    weight = 2 if keyword == keywords[0] else 1
                    if dimension in {"failure_recovery", "concurrency_timing"}:
                        weight += 1
                    hits.append(
                        EvidenceHit(
                            path=entry.path,
                            line=line_no,
                            snippet=safe_snippet(line),
                            keyword=keyword,
                            dimension=dimension,
                            weight=weight,
                        )
                    )
                    break
            if len(hits) >= MAX_LINE_HITS_PER_FILE:
                break

        if hits:
            score += sum(hit.weight for hit in hits)

        if not hits and score <= 0:
            continue

        if not hits:
            hits.append(
                EvidenceHit(
                    path=entry.path,
                    line=1,
                    snippet="path-level relevance",
                    keyword="path-match",
                    dimension="runtime_control_flow",
                    weight=1,
                )
            )

        scored.append(
            FileScore(
                path=entry.path,
                score=score,
                language=entry.language,
                is_test=entry.is_test,
                hits=hits,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.path))
    return scored, {"scanned_files": min(len(files), MAX_SCAN_FILES), "truncated_files": truncated_files}


def select_top_scores(scored: list[FileScore]) -> list[FileScore]:
    if not scored:
        return []

    non_doc = [item for item in scored if not is_doc_like(item.path)]
    selected = non_doc if non_doc else scored
    return selected[:MAX_FEATURE_FILES]


def collect_dimension_evidence(top_scores: list[FileScore]) -> dict[str, list[tuple[str, int, str]]]:
    result: dict[str, list[tuple[str, int, str]]] = {key: [] for key in DIMENSION_KEYS}
    seen = {key: set() for key in DIMENSION_KEYS}

    for score in top_scores:
        for hit in sorted(score.hits, key=lambda item: (-item.weight, item.line)):
            key = hit.dimension if hit.dimension in result else "runtime_control_flow"
            dedupe = (hit.path, hit.line)
            if dedupe in seen[key]:
                continue
            result[key].append((hit.path, hit.line, hit.snippet))
            seen[key].add(dedupe)
            if len(result[key]) >= 4:
                continue

    return result


def flatten_evidence(by_dimension: dict[str, list[tuple[str, int, str]]]) -> list[tuple[str, int, str, str]]:
    merged: list[tuple[str, int, str, str]] = []
    seen = set()
    for dimension in DIMENSION_KEYS:
        for path, line, snippet in by_dimension.get(dimension, []):
            key = (path, line)
            if key in seen:
                continue
            merged.append((path, line, snippet, dimension))
            seen.add(key)
            if len(merged) >= MAX_FEATURE_EVIDENCE:
                return merged
    return merged


def confidence_for_count(count: int) -> str:
    if count >= 3:
        return "high"
    if count >= 1:
        return "medium"
    return "low"


def build_dimension_conclusion(
    *,
    dimension: str,
    evidence: list[tuple[str, int, str]],
    lang: str,
    labels: dict,
) -> dict:
    confidence = confidence_for_count(len(evidence))
    if evidence:
        lead_path, lead_line, _ = evidence[0]
        zh_templates = {
            "runtime_control_flow": "该功能存在可追踪的触发->分发->执行主链路，关键入口与执行衔接点集中在",
            "data_flow": "该功能的数据边界可见于输入解析、结构转换与输出回写链路，关键证据集中在",
            "state_lifecycle": "该功能的状态演进围绕创建、更新与持久化节点展开，关键状态节点集中在",
            "failure_recovery": "该功能存在显式异常分支与恢复信号，关键失败处理路径集中在",
            "concurrency_timing": "该功能体现出并发/时序控制线索，关键同步或调度点集中在",
        }
        en_templates = {
            "runtime_control_flow": "This feature has a traceable trigger->dispatch->execution chain, with key transitions around",
            "data_flow": "This feature shows concrete input/transform/output boundaries, with key data handoff signals around",
            "state_lifecycle": "This feature's state lifecycle is organized around create/update/persist steps, with key lifecycle signals around",
            "failure_recovery": "This feature includes explicit error/recovery behavior, with key failure-handling signals around",
            "concurrency_timing": "This feature exposes concurrency/timing controls, with key synchronization or scheduling points around",
        }
        if lang == "zh":
            lead = zh_templates.get(dimension, "该维度存在机制性实现线索，关键证据集中在")
            text = f"{lead} `{lead_path}:{lead_line}` 及其相邻实现路径。"
        else:
            lead = en_templates.get(dimension, "This dimension has mechanism-level implementation signals around")
            text = f"{lead} `{lead_path}:{lead_line}` and adjacent implementation paths."
        return {
            "conclusion": text,
            "confidence": confidence,
            "inference": confidence == "low",
        }

    if lang == "zh":
        text = f"未发现该维度的直接代码证据，当前判断为 {labels['inference']}。"
    else:
        text = f"No direct code evidence found for this dimension; current statement is {labels['inference']}."

    return {"conclusion": text, "confidence": "low", "inference": True}


def compute_feature_confidence(principles: dict[str, dict], evidence_count: int) -> str:
    high_dims = sum(1 for item in principles.values() if item.get("confidence") == "high")
    medium_dims = sum(1 for item in principles.values() if item.get("confidence") in {"high", "medium"})
    if high_dims >= 2 and evidence_count >= 8:
        return "high"
    if medium_dims >= 3 and evidence_count >= 4:
        return "medium"
    return "low"


def build_direct_answer(
    feature: str,
    principles: dict[str, dict],
    confidence: str,
    lang: str,
    labels: dict,
) -> str:
    strong_dims = [
        key
        for key, value in principles.items()
        if value.get("confidence") in {"high", "medium"}
    ]

    if not strong_dims:
        if lang == "zh":
            return (
                f"该功能目前未形成稳定、可闭环的机制证据链。"
                f"现有线索不足以确认完整实现路径，当前仅能给出 {labels['inference']} 级别判断。"
                "建议补充更具体的触发入口、数据结构或关键符号后再收敛结论。"
            )
        return f"No stable mechanism evidence found for this feature; only {labels['inference']}-level interpretation is available."

    runtime = str(principles.get("runtime_control_flow", {}).get("conclusion", "")).strip().rstrip("。.")
    data_flow = str(principles.get("data_flow", {}).get("conclusion", "")).strip().rstrip("。.")
    state_lifecycle = str(principles.get("state_lifecycle", {}).get("conclusion", "")).strip().rstrip("。.")
    failure_recovery = str(principles.get("failure_recovery", {}).get("conclusion", "")).strip().rstrip("。.")
    concurrency_timing = str(principles.get("concurrency_timing", {}).get("conclusion", "")).strip().rstrip("。.")

    if lang == "zh":
        confidence_line = (
            "综合五个机制维度，当前证据相互印证程度高，结论可作为较稳定实现解释。"
            if confidence == "high"
            else "综合五个机制维度，主链路已可识别，但局部边界仍需要补充验证。"
        )
        if confidence == "high":
            return (
                "该功能并非由单点函数完成，而是由多阶段机制共同闭环。"
                f"控制流层面，{runtime}。"
                f"数据流层面，{data_flow}。"
                f"状态生命周期层面，{state_lifecycle}。"
                f"失败恢复层面，{failure_recovery}。"
                f"并发与时序层面，{concurrency_timing}。"
                f"{confidence_line}"
            )
        return (
            "该功能已经呈现出可追踪的实现主链，但仍存在待补充证据的边界点。"
            f"控制流层面，{runtime}。"
            f"数据流层面，{data_flow}。"
            f"状态生命周期层面，{state_lifecycle}。"
            f"失败恢复层面，{failure_recovery}。"
            f"并发与时序层面，{concurrency_timing}。"
            f"{confidence_line}"
        )

    confidence_line_en = (
        "Across the five mechanism dimensions, the evidence is mutually reinforcing and supports a stable explanation."
        if confidence == "high"
        else "Across the five mechanism dimensions, the primary chain is visible but some boundaries still need verification."
    )
    return (
        "This feature is not driven by a single function, but by a multi-stage mechanism chain."
        f" At the control-flow level, {runtime}."
        f" At the data-flow level, {data_flow}."
        f" At the state/lifecycle level, {state_lifecycle}."
        f" At the failure/recovery level, {failure_recovery}."
        f" At the concurrency/timing level, {concurrency_timing}."
        f" {confidence_line_en}"
    )


def should_regenerate_summary(summary: str, lang: str) -> bool:
    text = summary.strip()
    if not text:
        return True

    generic_phrases = [
        "关键实现线索集中在",
        "recognizable implementation chain",
        "clear control-flow plus data/state mechanism",
    ]
    if any(phrase in text for phrase in generic_phrases):
        return True

    sentence_count = len([item for item in re.split(r"[。！？.!?；;]\s*", text) if item.strip()])
    min_len = 80 if lang == "zh" else 140
    return sentence_count < 3 or len(text) < min_len


def run_git(source_dir: Path, args: list[str]) -> str | None:
    git_dir = source_dir / ".git"
    if not git_dir.exists():
        return None

    completed = subprocess.run(
        ["git", "-C", str(source_dir), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def format_confidence(confidence: str, labels: dict) -> str:
    if confidence == "high":
        return labels["high"]
    if confidence == "medium":
        return labels["medium"]
    return labels["low"]


def shift_heading_level(line: str, delta: int = 1) -> str:
    match = re.match(r"^(#{1,6})\s+(.*)$", line)
    if not match:
        return line
    level = min(6, len(match.group(1)) + delta)
    return f"{'#' * level} {match.group(2)}"


def next_run_number(existing: str) -> int:
    matches = re.findall(r"^##\s+Run\s+(\d+)\s*$", existing, flags=re.MULTILINE)
    if matches:
        return max(int(value) for value in matches) + 1
    if existing.strip():
        return 2
    return 1


def write_append_report(*, output: Path, title: str, section_lines: list[str]) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = output.read_text(encoding="utf-8") if output.exists() else ""
    run_number = next_run_number(existing)

    shifted_lines = [shift_heading_level(line, delta=1) for line in section_lines]
    run_block = [f"## Run {run_number}", "", *shifted_lines]
    run_text = "\n".join(run_block).rstrip() + "\n"

    if existing.strip():
        merged = existing.rstrip() + "\n\n" + run_text
    else:
        merged = "\n".join([f"# {title}", "", run_text.rstrip()]).rstrip() + "\n"

    output.write_text(merged, encoding="utf-8")
    return run_number


def normalize_subagent_feature(item: dict, fallback_feature: str, lang: str, labels: dict) -> dict:
    feature = item.get("feature")
    if not isinstance(feature, str) or not feature.strip():
        feature = fallback_feature

    principles_raw = item.get("principles") if isinstance(item.get("principles"), dict) else {}
    principles: dict[str, dict] = {}
    for dimension in DIMENSION_KEYS:
        value = principles_raw.get(dimension) if isinstance(principles_raw, dict) else None
        if not isinstance(value, dict):
            principles[dimension] = {
                "conclusion": (
                    f"未提供该维度结论，按 {labels['inference']} 处理。"
                    if lang == "zh"
                    else f"No conclusion provided for this dimension; treated as {labels['inference']}."
                ),
                "confidence": "low",
                "inference": True,
            }
            continue

        conclusion = value.get("conclusion")
        confidence = value.get("confidence")
        inference = value.get("inference")
        if not isinstance(conclusion, str) or not conclusion.strip():
            conclusion = (
                "未提供该维度结论。" if lang == "zh" else "No conclusion provided for this dimension."
            )
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        if not isinstance(inference, bool):
            inference = True

        principles[dimension] = {
            "conclusion": conclusion.strip(),
            "confidence": confidence,
            "inference": inference,
        }

    evidence_items = []
    for raw in item.get("evidence", []):
        if not isinstance(raw, dict):
            continue
        path = raw.get("path")
        line = raw.get("line")
        snippet = raw.get("snippet")
        dimension = raw.get("for_dimension")
        if not isinstance(path, str) or not path:
            continue
        if not isinstance(line, int) or line <= 0:
            continue
        if not isinstance(snippet, str):
            snippet = ""
        if not isinstance(dimension, str) or not dimension:
            dimension = "runtime_control_flow"
        evidence_items.append((path, line, safe_snippet(snippet), dimension))

    confidence = item.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        confidence = compute_feature_confidence(principles, len(evidence_items))

    summary = item.get("summary")
    if not isinstance(summary, str):
        summary = ""
    if should_regenerate_summary(summary, lang):
        summary = build_direct_answer(feature, principles, confidence, lang, labels)

    unknowns = item.get("unknowns") if isinstance(item.get("unknowns"), list) else []
    conflicts = item.get("conflicts") if isinstance(item.get("conflicts"), list) else []
    notes = item.get("notes") if isinstance(item.get("notes"), list) else []

    return {
        "feature": feature,
        "direct_answer": summary.strip(),
        "confidence": confidence,
        "principles": principles,
        "evidence_items": evidence_items[:MAX_FEATURE_EVIDENCE],
        "unknowns": [str(x) for x in unknowns],
        "conflicts": [str(x) for x in conflicts],
        "notes": [str(x) for x in notes],
        "analysis_source": "subagent",
    }


def analyze_feature_single_agent(
    *,
    feature: str,
    source_dir: Path,
    files: list[FileEntry],
    lang: str,
    labels: dict,
) -> dict:
    keywords = tokenize_feature(feature)
    scored, scan_stats = score_feature_against_files(source_dir, files, keywords)
    top_scores = select_top_scores(scored)

    by_dimension = collect_dimension_evidence(top_scores)
    principles = {
        key: build_dimension_conclusion(dimension=key, evidence=by_dimension[key], lang=lang, labels=labels)
        for key in DIMENSION_KEYS
    }
    evidence_items = flatten_evidence(by_dimension)
    confidence = compute_feature_confidence(principles, len(evidence_items))
    direct_answer = build_direct_answer(feature, principles, confidence, lang, labels)

    unknowns: list[str] = []
    if scan_stats.get("truncated_files", 0) > 0:
        remain = scan_stats["truncated_files"]
        unknowns.append(
            f"扫描达到上限，仍有 {remain} 个文件未扫描。"
            if lang == "zh"
            else f"Scan limit reached; {remain} files were not scanned."
        )

    if confidence == "low":
        unknowns.append(
            "当前证据密度偏低，建议补充更具体关键词或关键符号。"
            if lang == "zh"
            else "Evidence density is low; add concrete symbols or narrower feature terms."
        )

    return {
        "feature": feature,
        "direct_answer": direct_answer,
        "confidence": confidence,
        "principles": principles,
        "evidence_items": evidence_items,
        "unknowns": unknowns,
        "conflicts": [],
        "notes": [],
        "analysis_source": "single-agent",
        "top_paths": [score.path for score in top_scores[:4]],
    }


def analyze_project_characteristics(
    *,
    source_dir: Path,
    index: dict,
    files: list[FileEntry],
    lang: str,
    labels: dict,
) -> tuple[list[dict], list[str]]:
    candidates, readme_path = build_characteristic_candidates(source_dir, index, lang)
    notes: list[str] = []
    if readme_path:
        notes.append(
            f"README-first: `{readme_path}`"
            if lang == "zh"
            else f"README-first source: `{readme_path}`"
        )
    else:
        notes.append(
            "未发现可用 README，已切换到基于代码索引的 inference 兜底。"
            if lang == "zh"
            else "No usable README found; switched to index-based inference fallback."
        )

    results: list[dict] = []
    for candidate in candidates[:MAX_CHARACTERISTICS]:
        title = candidate.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        analyzed = analyze_feature_single_agent(
            feature=title.strip(),
            source_dir=source_dir,
            files=files,
            lang=lang,
            labels=labels,
        )
        results.append(
            {
                "title": title.strip(),
                "source": candidate.get("source") if candidate.get("source") in {"readme", "inference"} else "inference",
                "readme_signal": candidate.get("readme_signal") if isinstance(candidate.get("readme_signal"), str) else None,
                "direct_answer": analyzed.get("direct_answer", ""),
                "confidence": analyzed.get("confidence", "low"),
                "evidence_items": analyzed.get("evidence_items", [])[:MAX_CHARACTERISTIC_EVIDENCE_REFS],
            }
        )

    return results, notes


def deep_audit_lines(feature_result: dict, lang: str) -> list[str]:
    evidence_pool = " ".join(snippet.lower() for _path, _line, snippet, _dim in feature_result.get("evidence_items", []))
    has_failure = any(token in evidence_pool for token in DIMENSION_TOKENS["failure_recovery"])
    has_concurrency = any(token in evidence_pool for token in DIMENSION_TOKENS["concurrency_timing"])

    lines: list[str] = []
    if lang == "zh":
        lines.append(
            "- 并发/时序信号: "
            + ("存在（建议补做顺序一致性验证）" if has_concurrency else "较弱（并发结论低置信）")
        )
        lines.append(
            "- 失败恢复信号: " + ("存在（建议核对重试/回滚分支）" if has_failure else "较弱（异常路径可能漏检）")
        )
        lines.append(
            "- 证据完整性: "
            + (
                "较好（命中多个维度）"
                if len(feature_result.get("evidence_items", [])) >= 8
                else "一般（建议补充更多直接证据）"
            )
        )
        return lines

    lines.append(
        "- Concurrency/timing signals: "
        + ("present (verify ordering and boundary behavior)" if has_concurrency else "weak (low confidence for concurrency claims)")
    )
    lines.append(
        "- Failure/recovery signals: "
        + ("present (verify retry/rollback branches)" if has_failure else "weak (error paths may be missed)")
    )
    lines.append(
        "- Evidence completeness: "
        + ("good (multiple dimensions covered)" if len(feature_result.get("evidence_items", [])) >= 8 else "moderate (collect more direct evidence)")
    )
    return lines


def build_human_feature_view(feature_result: dict, lang: str, labels: dict) -> dict[str, str]:
    principles = feature_result.get("principles", {}) if isinstance(feature_result.get("principles"), dict) else {}
    runtime = str(principles.get("runtime_control_flow", {}).get("conclusion", labels["none"]))
    data_flow = str(principles.get("data_flow", {}).get("conclusion", labels["none"]))
    state_lifecycle = str(principles.get("state_lifecycle", {}).get("conclusion", labels["none"]))
    failure_recovery = str(principles.get("failure_recovery", {}).get("conclusion", labels["none"]))
    concurrency_timing = str(principles.get("concurrency_timing", {}).get("conclusion", labels["none"]))

    has_failure_signal = failure_recovery and labels["none"] not in failure_recovery
    has_concurrency_signal = concurrency_timing and labels["none"] not in concurrency_timing

    if lang == "zh":
        special_parts: list[str] = []
        if has_failure_signal:
            special_parts.append(f"该功能具备失败恢复能力，{failure_recovery}")
        if has_concurrency_signal:
            special_parts.append(f"该功能具备并发/时序控制能力，{concurrency_timing}")
        if not special_parts:
            special_parts.append("当前未提取到稳定的失败恢复或并发时序特征，特殊能力判断以 inference 为主。")

        implementation_idea = (
            "实现上采用“控制流触发 + 数据流传递 + 状态流转”的组合策略，"
            f"控制流负责串联执行阶段（{runtime}），"
            f"数据流负责边界转换（{data_flow}），"
            f"状态生命周期负责保持一致性（{state_lifecycle}）。"
        )

        return {
            "function_role": feature_result.get("direct_answer", labels["none"]),
            "special_capability": "；".join(special_parts),
            "implementation_idea": implementation_idea,
        }

    special_parts_en: list[str] = []
    if has_failure_signal:
        special_parts_en.append(f"Failure-recovery signals are explicit: {failure_recovery}")
    if has_concurrency_signal:
        special_parts_en.append(f"Concurrency/timing control signals are explicit: {concurrency_timing}")
    if not special_parts_en:
        special_parts_en.append("No stable failure/concurrency signal is extracted yet; special capability remains inference-level.")

    implementation_idea_en = (
        "The implementation uses a combined control-flow + data-flow + state-lifecycle strategy, "
        f"where control flow wires execution stages ({runtime}), "
        f"data flow handles boundary transformation ({data_flow}), "
        f"and state lifecycle preserves consistency ({state_lifecycle})."
    )

    return {
        "function_role": feature_result.get("direct_answer", labels["none"]),
        "special_capability": "; ".join(special_parts_en),
        "implementation_idea": implementation_idea_en,
    }


def render_report(
    *,
    repo_url: str,
    ref: str,
    resolved_ref_override: str | None,
    commit_sha_override: str | None,
    features: list[str],
    depth: str,
    language: str,
    source_dir: Path,
    index_json: Path,
    subagent_results_json: Path | None,
    output: Path,
) -> dict:
    labels = LABELS[language]
    index = load_index(index_json)

    file_entries: list[FileEntry] = []
    for item in index.get("files", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        file_entries.append(
            FileEntry(
                path=path,
                size=int(item.get("size", 0) or 0),
                language=str(item.get("language", "Unknown")),
                is_test=bool(item.get("is_test", False)),
                is_text=bool(item.get("is_text", False)),
            )
        )

    if not file_entries:
        raise ValueError("index contains no files")

    commit_sha = commit_sha_override or run_git(source_dir, ["rev-parse", "HEAD"])
    resolved_ref = resolved_ref_override or run_git(source_dir, ["rev-parse", "--abbrev-ref", "HEAD"]) or ref

    subagent_payload = load_json(subagent_results_json) if subagent_results_json else None
    subagent_features = subagent_payload.get("features", []) if isinstance(subagent_payload, dict) else []
    subagent_map = {}
    if isinstance(subagent_features, list):
        for item in subagent_features:
            if not isinstance(item, dict):
                continue
            feature_name = item.get("feature")
            if isinstance(feature_name, str) and feature_name.strip():
                subagent_map[feature_name.strip()] = item

    feature_results = []
    for feature in features:
        raw = subagent_map.get(feature)
        if raw:
            feature_results.append(normalize_subagent_feature(raw, feature, language, labels))
            continue

        feature_results.append(
            analyze_feature_single_agent(
                feature=feature,
                source_dir=source_dir,
                files=file_entries,
                lang=language,
                labels=labels,
            )
        )

    characteristic_results, characteristic_notes = analyze_project_characteristics(
        source_dir=source_dir,
        index=index,
        files=file_entries,
        lang=language,
        labels=labels,
    )

    analysis_mode = "single-agent"
    if subagent_payload and isinstance(subagent_payload, dict) and subagent_payload.get("analysis_mode") == "multi-agent":
        analysis_mode = "multi-agent"

    overview_notes: list[str] = []
    architecture_notes: list[str] = []
    if subagent_payload and isinstance(subagent_payload, dict):
        overview = subagent_payload.get("overview")
        if isinstance(overview, dict):
            summary = overview.get("summary")
            if isinstance(summary, str) and summary.strip():
                overview_notes.append(summary.strip())

        architecture = subagent_payload.get("architecture")
        if isinstance(architecture, dict):
            summary = architecture.get("summary")
            if isinstance(summary, str) and summary.strip():
                architecture_notes.append(summary.strip())

    global_risks: list[str] = []
    for result in feature_results:
        if result["confidence"] == "low":
            global_risks.append(
                f"{result['feature']}: 证据置信度偏低。"
                if language == "zh"
                else f"{result['feature']}: low evidence confidence."
            )
        for conflict in result.get("conflicts", []):
            global_risks.append(f"{result['feature']}: {conflict}")

    shared_messages: list[str] = []
    for i in range(len(feature_results)):
        for j in range(i + 1, len(feature_results)):
            left = feature_results[i]
            right = feature_results[j]
            left_paths = {path for path, _line, _snippet, _dim in left.get("evidence_items", [])}
            right_paths = {path for path, _line, _snippet, _dim in right.get("evidence_items", [])}
            overlap = sorted(left_paths & right_paths)
            if overlap:
                shared_messages.append(
                    (
                        f"`{left['feature']}` 与 `{right['feature']}` 共享关键路径: {', '.join(overlap[:5])}"
                        if language == "zh"
                        else f"`{left['feature']}` and `{right['feature']}` share key paths: {', '.join(overlap[:5])}"
                    )
                )

    if shared_messages:
        global_risks.extend(shared_messages)

    if subagent_payload and isinstance(subagent_payload, dict):
        for note in subagent_payload.get("merge_notes", []):
            if isinstance(note, str) and note.strip():
                global_risks.append(note.strip())

    if not global_risks:
        global_risks = [labels["none"]]

    languages = index.get("languages", {})
    top_languages = ", ".join([f"{name}:{count}" for name, count in list(languages.items())[:8]])

    entry_candidates = index.get("entry_candidates", [])
    entrypoints = [value for value in entry_candidates if isinstance(value, str)][:8]

    directories = index.get("directories", [])
    boundaries = [item.get("path") for item in directories if isinstance(item, dict) and isinstance(item.get("path"), str)][:8]

    report_lines: list[str] = []

    report_lines.extend([f"## {labels['section_one']}", ""])

    report_lines.extend(
        [
            f"### {labels['metadata']}",
            f"- {labels['repo']}: `{repo_url}`",
            f"- {labels['ref']}: `{ref}`",
            f"- {labels['resolved_ref']}: `{resolved_ref}`",
            f"- {labels['commit']}: `{commit_sha or labels['none']}`",
            f"- {labels['generated_at']}: `{datetime.now(timezone.utc).isoformat()}`",
            f"- {labels['depth']}: `{depth}`",
            f"- {labels['analysis_mode']}: `{analysis_mode}`",
            f"- {labels['source_dir']}: `{source_dir}`",
            f"- {labels['index_file']}: `{index_json}`",
            f"- {labels['subagent_file']}: `{subagent_results_json if subagent_results_json else labels['none']}`",
            "",
        ]
    )

    report_lines.extend(
        [
            f"### {labels['overview']}",
            f"- {labels['total_files']}: `{index.get('total_files', 0)}`",
            f"- {labels['indexed_text_files']}: `{index.get('indexed_text_files', 0)}`",
            f"- {labels['top_languages']}: `{top_languages or labels['none']}`",
            f"- {labels['entrypoints']}: `{', '.join(entrypoints) if entrypoints else labels['none']}`",
            f"- {labels['module_boundaries']}: `{', '.join(boundaries) if boundaries else labels['none']}`",
            "",
        ]
    )

    for item in overview_notes:
        report_lines.append(f"- {item}")
    for item in architecture_notes:
        report_lines.append(f"- {item}")
    if overview_notes or architecture_notes:
        report_lines.append("")

    report_lines.extend([f"### {labels['project_characteristics']}", ""])
    for note in characteristic_notes:
        report_lines.append(f"- {note}")
    if characteristic_notes:
        report_lines.append("")

    if characteristic_results:
        for idx, item in enumerate(characteristic_results, start=1):
            report_lines.append(f"#### {labels['characteristic']} {idx}: {item['title']}")
            report_lines.append(
                f"- {labels['characteristic_source']}: `{item['source']}`"
            )
            if item.get("readme_signal"):
                report_lines.append(
                    f"- {labels['characteristic_signal']}: `{item['readme_signal']}`"
                )
            report_lines.append(
                f"- {labels['characteristic_mechanism']}: {item['direct_answer']}"
            )
            report_lines.append(
                f"- {labels['confidence']}: `{format_confidence(item['confidence'], labels)}`"
            )
            report_lines.append(f"- {labels['key_evidence_refs']}:")
            evidence_items = item.get("evidence_items", [])
            if evidence_items:
                for path, line, _snippet, _dim in evidence_items[:MAX_CHARACTERISTIC_EVIDENCE_REFS]:
                    report_lines.append(f"  - `{path}:{line}`")
            else:
                report_lines.append(f"  - {labels['none']}")
            report_lines.append("")
    else:
        report_lines.append(f"- {labels['none']}")
        report_lines.append("")

    report_lines.extend([f"## {labels['section_two']}", ""])

    for idx, result in enumerate(feature_results, start=1):
        human_view = build_human_feature_view(result, language, labels)
        report_lines.append(f"### {labels['feature']} {idx}: {result['feature']}")
        report_lines.append(f"- {labels['human_function_role']}: {human_view['function_role']}")
        report_lines.append(f"- {labels['human_special_capability']}: {human_view['special_capability']}")
        report_lines.append(f"- {labels['human_implementation_idea']}: {human_view['implementation_idea']}")
        report_lines.append(f"- {labels['confidence']}: `{format_confidence(result['confidence'], labels)}`")
        report_lines.append(f"- {labels['key_evidence_refs']}:")
        evidence_items = result.get("evidence_items", [])
        if evidence_items:
            for path, line, _snippet, _dim in evidence_items[:5]:
                report_lines.append(f"  - `{path}:{line}`")
        else:
            report_lines.append(f"  - {labels['none']}")
        report_lines.append("")

    report_lines.extend([f"## {labels['section_three']}", ""])
    report_lines.extend([f"### {labels['feature_details']}", ""])

    for idx, result in enumerate(feature_results, start=1):
        report_lines.append(f"#### {labels['feature']} {idx}: {result['feature']}")
        report_lines.append("")

        principles = result.get("principles", {})
        for key in DIMENSION_KEYS:
            section_title = labels[key]
            detail = principles.get(key, {}) if isinstance(principles, dict) else {}
            conclusion = detail.get("conclusion") if isinstance(detail.get("conclusion"), str) else labels["none"]
            confidence = detail.get("confidence") if detail.get("confidence") in {"high", "medium", "low"} else "low"
            inference = bool(detail.get("inference", True))

            report_lines.append(f"##### {section_title}")
            report_lines.append(f"- {conclusion}")
            report_lines.append(f"- {labels['confidence']}: `{format_confidence(confidence, labels)}`")
            report_lines.append(f"- {labels['inference']}: `{str(inference).lower()}`")
            report_lines.append("")

        report_lines.append(f"##### {labels['key_evidence']}")
        evidence_items = result.get("evidence_items", [])
        if evidence_items:
            for path, line, snippet, dimension in evidence_items:
                report_lines.append(f"- `{path}:{line}` [{dimension}] - `{snippet or labels['inference']}`")
        else:
            report_lines.append(f"- {labels['none']}")
        report_lines.append("")

        report_lines.append(f"##### {labels['unknowns']}")
        unknowns = result.get("unknowns", [])
        notes = result.get("notes", [])
        if unknowns or notes:
            for item in unknowns:
                report_lines.append(f"- {item}")
            for item in notes:
                report_lines.append(f"- {item}")
        else:
            report_lines.append(f"- {labels['none']}")
        report_lines.append("")

        if should_classify_invocation_path(result["feature"]):
            invocation = classify_invocation_path(result, language)
            report_lines.append(f"##### {labels['invocation_classification']}")
            report_lines.append(f"- {labels['invocation_type']}: `{invocation['mode_text']}`")
            report_lines.append(f"- {labels['working_dir_resolution']}: {invocation['working_dir_text']}")
            report_lines.append("")

        if depth == "deep":
            report_lines.append(f"##### {labels['deep_audit']}")
            for line in deep_audit_lines(result, language):
                report_lines.append(line)
            report_lines.append("")

    report_lines.append(f"### {labels['global_risks']}")
    for risk in global_risks:
        report_lines.append(f"- {risk}")
    report_lines.append("")

    run_number = write_append_report(output=output, title=labels["title"], section_lines=report_lines)

    return {
        "report_path": str(output),
        "run_number": run_number,
        "characteristic_count": len(characteristic_results),
        "feature_count": len(features),
        "depth": depth,
        "language": language,
        "analysis_mode": analysis_mode,
        "commit_sha": commit_sha,
        "resolved_ref": resolved_ref,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--index-json", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resolved-ref", default=None, help="Resolved ref from fetch step")
    parser.add_argument("--commit-sha", default=None, help="Resolved commit SHA from fetch step")
    parser.add_argument("--subagent-results", default=None, help="Merged sub-agent JSON path")
    parser.add_argument("--depth", default="standard", choices=["standard", "deep"])
    parser.add_argument("--language", default="zh", choices=["zh", "en"])
    parser.add_argument("--feature", action="append", required=True, help="Repeat for each feature")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    source_dir = Path(args.source_dir).resolve()
    index_json = Path(args.index_json).resolve()
    output = Path(args.output).resolve()
    subagent_results_json = Path(args.subagent_results).resolve() if args.subagent_results else None

    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"source-dir does not exist: {source_dir}")
    if not index_json.exists() or not index_json.is_file():
        raise SystemExit(f"index-json does not exist: {index_json}")
    if subagent_results_json and (not subagent_results_json.exists() or not subagent_results_json.is_file()):
        raise SystemExit(f"subagent-results does not exist: {subagent_results_json}")

    features = normalize_feature_list(args.feature)

    summary = render_report(
        repo_url=args.repo_url,
        ref=args.ref,
        resolved_ref_override=args.resolved_ref,
        commit_sha_override=args.commit_sha,
        features=features,
        depth=args.depth,
        language=args.language,
        source_dir=source_dir,
        index_json=index_json,
        subagent_results_json=subagent_results_json,
        output=output,
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
