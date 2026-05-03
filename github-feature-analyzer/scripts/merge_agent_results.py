#!/usr/bin/env python3
"""Merge sub-agent JSON outputs into a normalized artifact for report rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ALLOWED_ROLES = {"overview", "architecture", "feature"}
ALLOWED_STATUS = {"ok", "partial", "failed"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Directory containing sub-agent json files")
    parser.add_argument("--output", required=True, help="Path to merged output json")
    return parser.parse_args()


def load_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"invalid json: {exc}"

    if not isinstance(payload, dict):
        return None, "payload must be a JSON object"
    return payload, None


def normalize_evidence(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue

        path = item.get("path")
        line = item.get("line")
        snippet = item.get("snippet")
        for_dimension = item.get("for_dimension")

        if not isinstance(path, str) or not path:
            continue
        if not isinstance(line, int) or line <= 0:
            continue
        if not isinstance(snippet, str):
            snippet = ""
        if not isinstance(for_dimension, str) or not for_dimension:
            for_dimension = "overview"

        key = (path, line, for_dimension)
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "path": path,
                "line": line,
                "snippet": " ".join(snippet.strip().split())[:180],
                "for_dimension": for_dimension,
            }
        )

    return normalized


def normalize_principles(principles: Any) -> dict[str, dict[str, Any]]:
    required = {
        "runtime_control_flow",
        "data_flow",
        "state_lifecycle",
        "failure_recovery",
        "concurrency_timing",
    }

    result: dict[str, dict[str, Any]] = {}
    payload = principles if isinstance(principles, dict) else {}

    for key in sorted(required):
        raw = payload.get(key)
        if not isinstance(raw, dict):
            result[key] = {
                "conclusion": "No direct sub-agent conclusion.",
                "confidence": "low",
                "inference": True,
            }
            continue

        conclusion = raw.get("conclusion")
        confidence = raw.get("confidence")
        inference = raw.get("inference")

        if not isinstance(conclusion, str) or not conclusion.strip():
            conclusion = "No direct sub-agent conclusion."
        if confidence not in ALLOWED_CONFIDENCE:
            confidence = "low"
        if not isinstance(inference, bool):
            inference = True

        result[key] = {
            "conclusion": conclusion.strip(),
            "confidence": confidence,
            "inference": inference,
        }

    return result


def merge_payloads(files: list[Path]) -> dict[str, Any]:
    merge_notes: list[str] = []
    overview: dict[str, Any] | None = None
    architecture: dict[str, Any] | None = None
    feature_items: list[dict[str, Any]] = []

    for path in files:
        payload, error = load_json_file(path)
        if error:
            merge_notes.append(f"{path.name}: {error}")
            continue

        role = payload.get("agent_role")
        if role not in ALLOWED_ROLES:
            merge_notes.append(f"{path.name}: missing/invalid agent_role")
            continue

        status = payload.get("status")
        if status not in ALLOWED_STATUS:
            status = "partial"

        confidence = payload.get("confidence")
        if confidence not in ALLOWED_CONFIDENCE:
            confidence = "low"

        summary = payload.get("summary")
        if not isinstance(summary, str):
            summary = ""

        normalized = {
            "agent_role": role,
            "status": status,
            "confidence": confidence,
            "summary": summary.strip(),
            "evidence": normalize_evidence(payload.get("evidence")),
            "notes": payload.get("notes") if isinstance(payload.get("notes"), list) else [],
            "unknowns": payload.get("unknowns") if isinstance(payload.get("unknowns"), list) else [],
            "conflicts": payload.get("conflicts") if isinstance(payload.get("conflicts"), list) else [],
        }

        if role == "feature":
            feature = payload.get("feature")
            if not isinstance(feature, str) or not feature.strip():
                merge_notes.append(f"{path.name}: feature role missing feature field")
                continue
            normalized["feature"] = feature.strip()
            normalized["principles"] = normalize_principles(payload.get("principles"))
            feature_items.append(normalized)
            continue

        if role == "overview":
            if overview is None:
                overview = normalized
            else:
                merge_notes.append(f"{path.name}: duplicate overview, kept first")
            continue

        if role == "architecture":
            if architecture is None:
                architecture = normalized
            else:
                merge_notes.append(f"{path.name}: duplicate architecture, kept first")
            continue

    return {
        "analysis_mode": "multi-agent",
        "overview": overview,
        "architecture": architecture,
        "features": feature_items,
        "merge_notes": merge_notes,
    }


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"input dir does not exist: {input_dir}")

    files = sorted([path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".json"])
    if not files:
        raise SystemExit(f"no json files found in: {input_dir}")

    merged = merge_payloads(files)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(output_path),
                "input_files": len(files),
                "feature_results": len(merged.get("features", [])),
                "has_overview": merged.get("overview") is not None,
                "has_architecture": merged.get("architecture") is not None,
                "merge_notes": len(merged.get("merge_notes", [])),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
