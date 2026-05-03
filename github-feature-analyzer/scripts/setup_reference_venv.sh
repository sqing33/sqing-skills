#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENV_DIR="${SKILL_DIR}/.venv-reference"
LOCK_FILE="${SCRIPT_DIR}/requirements-vector.lock.txt"
PY_VERSION="${UV_REFERENCE_PYTHON_VERSION:-3.12}"
TORCH_CPU_INDEX="${UV_TORCH_CPU_INDEX:-https://download.pytorch.org/whl/cpu}"
PYPI_INDEX="${UV_DEFAULT_INDEX:-https://pypi.org/simple}"

# Keep installation memory footprint predictable on low-memory hosts.
export UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-1}"
export UV_CONCURRENT_BUILDS="${UV_CONCURRENT_BUILDS:-1}"
export UV_CONCURRENT_INSTALLS="${UV_CONCURRENT_INSTALLS:-1}"

compute_sha256() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file_path}" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file_path}" | awk '{print $1}'
    return
  fi
  echo "[gha-reference] error: missing sha256 tool (need sha256sum or shasum)." >&2
  exit 1
}

if [[ ! -f "${LOCK_FILE}" ]]; then
  echo "[gha-reference] error: lock file not found: ${LOCK_FILE}" >&2
  echo "[gha-reference] regenerate lock with:" >&2
  echo "uv pip compile --python-version 3.12 --managed-python --torch-backend cpu ${SCRIPT_DIR}/requirements-vector.txt -o ${LOCK_FILE}" >&2
  exit 1
fi

"${SCRIPT_DIR}/ensure_uv_unix.sh"

# Force uv-managed Python to avoid system Python drift.
export UV_MANAGED_PYTHON=1

echo "[gha-reference] ensuring uv-managed Python ${PY_VERSION}..."
uv python install "${PY_VERSION}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "[gha-reference] creating venv: ${VENV_DIR}"
  uv venv --python "${PY_VERSION}" "${VENV_DIR}"
fi

PY_BIN="${VENV_DIR}/bin/python"
if [[ ! -x "${PY_BIN}" ]]; then
  echo "[gha-reference] error: python not found in venv: ${PY_BIN}" >&2
  exit 1
fi

LOCK_HASH="$(compute_sha256 "${LOCK_FILE}")"
STAMP_FILE="${VENV_DIR}/.reference-lock.sha256"
FORCE_SYNC="${UV_REFERENCE_FORCE_SYNC:-0}"
SKIP_SYNC=0

if [[ "${FORCE_SYNC}" != "1" && -f "${STAMP_FILE}" ]]; then
  INSTALLED_HASH="$(<"${STAMP_FILE}")"
  if [[ "${INSTALLED_HASH}" == "${LOCK_HASH}" ]]; then
    SKIP_SYNC=1
  fi
fi

if [[ "${SKIP_SYNC}" == "1" ]]; then
  echo "[gha-reference] lock unchanged; skip dependency sync."
else
  echo "[gha-reference] syncing dependencies from lock file..."
  uv pip sync \
    --python "${PY_BIN}" \
    --default-index "${PYPI_INDEX}" \
    --index "${TORCH_CPU_INDEX}" \
    --index-strategy unsafe-best-match \
    "${LOCK_FILE}"
  printf '%s\n' "${LOCK_HASH}" > "${STAMP_FILE}"
fi

"${PY_BIN}" - <<'PY'
import importlib
import platform
import sys

pkgs = ["numpy", "sentence_transformers"]
versions = {}
for name in pkgs:
    module = importlib.import_module(name)
    versions[name] = getattr(module, "__version__", "unknown")

print("[gha-reference] venv ready")
print(f"[gha-reference] python={sys.version.split()[0]} platform={platform.platform()}")
print(f"[gha-reference] numpy={versions['numpy']} sentence-transformers={versions['sentence_transformers']}")
PY
