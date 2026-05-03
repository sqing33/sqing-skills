#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${SKILL_DIR}/.venv-reference"
PY_BIN="${VENV_DIR}/bin/python"
RETRIEVAL_SCRIPT="${SCRIPT_DIR}/reference_retrieval.py"

export HF_HUB_DISABLE_PROGRESS_BARS="${HF_HUB_DISABLE_PROGRESS_BARS:-1}"
export TRANSFORMERS_VERBOSITY="${TRANSFORMERS_VERBOSITY:-error}"

"${SCRIPT_DIR}/setup_reference_venv.sh"

if [[ $# -eq 0 ]]; then
  set -- --help
fi

exec uv run --no-project --python "${PY_BIN}" "${RETRIEVAL_SCRIPT}" "$@"
