#!/usr/bin/env python3
"""Bootstrap optional dependencies for github-feature-analyzer with plain Python."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def skill_dir() -> Path:
    return script_dir().parent


def default_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("[github-feature-analyzer] " + " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def create_venv(venv_dir: Path, force: bool) -> Path:
    if force and venv_dir.exists():
        shutil.rmtree(venv_dir)

    python_bin = default_python(venv_dir)
    if not python_bin.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"[github-feature-analyzer] creating venv: {venv_dir}")
        venv.EnvBuilder(with_pip=True, clear=False).create(venv_dir)

    if not python_bin.exists():
        raise SystemExit(f"venv python not found: {python_bin}")
    return python_bin


def choose_requirements(use_lock: str) -> Path:
    scripts = script_dir()
    lock_file = scripts / "requirements-vector.lock.txt"
    loose_file = scripts / "requirements-vector.txt"

    if use_lock == "never":
        return loose_file
    if use_lock == "always":
        return lock_file

    # The bundled lock was generated for Python 3.12. For older Python versions,
    # use the loose requirements so pip can resolve compatible wheels.
    if sys.version_info >= (3, 12) and lock_file.exists():
        return lock_file
    return loose_file


def verify(python_bin: Path) -> dict[str, str]:
    code = r"""
import importlib
import json
import platform
import sys

mods = ["numpy", "sentence_transformers"]
out = {
    "python": sys.version.split()[0],
    "platform": platform.platform(),
}
for name in mods:
    module = importlib.import_module(name)
    out[name] = getattr(module, "__version__", "unknown")
print(json.dumps(out, ensure_ascii=False))
"""
    completed = subprocess.run(
        [str(python_bin), "-c", code],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv-dir", default=str(skill_dir() / ".venv-reference"))
    parser.add_argument("--force", action="store_true", help="Recreate venv and reinstall dependencies")
    parser.add_argument(
        "--use-lock",
        choices=["auto", "always", "never"],
        default="auto",
        help="Use locked requirements. auto uses the lock on Python 3.12+.",
    )
    parser.add_argument(
        "--torch-cpu-index",
        default=os.environ.get("GHA_TORCH_CPU_INDEX", "https://download.pytorch.org/whl/cpu"),
    )
    parser.add_argument("--skip-install", action="store_true", help="Only verify existing venv")
    args = parser.parse_args()

    venv_dir = Path(args.venv_dir).resolve()
    python_bin = create_venv(venv_dir, args.force)
    req_file = choose_requirements(args.use_lock).resolve()

    if not req_file.exists():
        raise SystemExit(f"requirements file not found: {req_file}")

    stamp = venv_dir / ".github-feature-analyzer-deps.json"
    desired = {
        "python": sys.version.split()[0],
        "requirements": str(req_file),
        "requirements_sha256": hash_file(req_file),
        "torch_cpu_index": args.torch_cpu_index,
    }

    installed = None
    if stamp.exists():
        try:
            installed = json.loads(stamp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            installed = None

    if args.skip_install:
        info = verify(python_bin)
    elif not args.force and installed == desired:
        print("[github-feature-analyzer] dependencies unchanged; skip install.")
        info = verify(python_bin)
    else:
        env = os.environ.copy()
        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], env=env)
        run(
            [
                str(python_bin),
                "-m",
                "pip",
                "install",
                "--extra-index-url",
                args.torch_cpu_index,
                "-r",
                str(req_file),
            ],
            env=env,
        )
        stamp.write_text(json.dumps(desired, indent=2, ensure_ascii=False), encoding="utf-8")
        info = verify(python_bin)

    result = {
        "venv_dir": str(venv_dir),
        "python_bin": str(python_bin),
        "requirements": str(req_file),
        "system_python": sys.version.split()[0],
        "system_platform": platform.platform(),
        "verified": info,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
