#!/usr/bin/env bash
set -euo pipefail

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${RUNNER_DIR}/.." && pwd)"

cd "${RUNNER_DIR}"

export GO111MODULE=on
go test ./...

build_target() {
  local goos="$1"
  local goarch="$2"
  local output="$3"
  local output_path="${SKILL_DIR}/${output}"

  mkdir -p "$(dirname "${output_path}")"
  echo "Building ${goos}/${goarch} -> ${output}"
  GOOS="${goos}" GOARCH="${goarch}" go build -o "${output_path}" ./cmd/eval-runner
}

build_target linux amd64 "bin/linux-amd64/eval_runner"
build_target darwin arm64 "bin/macos-arm64/eval_runner"
build_target windows amd64 "bin/windows-amd64/eval_runner.exe"
