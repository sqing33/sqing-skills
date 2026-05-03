#!/usr/bin/env python3
"""Run reference retrieval after bootstrapping dependencies with plain Python."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    venv_dir = skill_dir / ".venv-reference"
    python_bin = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    bootstrap = script_dir / "bootstrap_deps.py"
    retrieval = script_dir / "reference_retrieval.py"

    args = sys.argv[1:] or ["--help"]
    if args in (["--help"], ["-h"]):
        return subprocess.run([sys.executable, str(retrieval), *args]).returncode

    subprocess.run([sys.executable, str(bootstrap)], check=True)

    env = os.environ.copy()
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("TRANSFORMERS_VERBOSITY", "error")

    return subprocess.run([str(python_bin), str(retrieval), *args], env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
