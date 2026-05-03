#!/usr/bin/env bash
set -euo pipefail

OS="$(uname -s)"
case "${OS}" in
  Linux|Darwin)
    ;;
  *)
    echo "[gha-reference] error: unsupported OS '${OS}'." >&2
    echo "[gha-reference] this bootstrap currently supports Linux and macOS only." >&2
    exit 1
    ;;
esac

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
  echo "[gha-reference] uv already available: ${UV_BIN}"
  uv --version
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "[gha-reference] error: curl is required to install uv." >&2
  exit 1
fi

echo "[gha-reference] installing uv via official installer..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# Ensure uv is discoverable in current shell even before user restarts terminal.
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "[gha-reference] error: uv install finished but binary not found on PATH." >&2
  echo "[gha-reference] expected locations: ~/.local/bin or ~/.cargo/bin" >&2
  exit 1
fi

echo "[gha-reference] uv installed: $(command -v uv)"
uv --version

