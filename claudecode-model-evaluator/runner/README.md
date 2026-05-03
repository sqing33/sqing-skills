# Claude Code Model Evaluator (Go)

This repository contains the Go rewrite of the `claudecode-model-evaluator` skill runner.

## Layout

- `cmd/eval-runner`: CLI entrypoint
- `internal/spec`: spec and models parser
- `internal/runner`: benchmark orchestration
- `internal/workspace`: isolated workspace and diff helpers
- `internal/claudestream`: Claude stream-json parser
- `.codex/skills/claudecode-model-evaluator`: distributable skill directory

## Commands

Windows:

```powershell
.\.codex\skills\claudecode-model-evaluator\bin\windows-amd64\eval_runner.exe run-skill --models-file .\.codex\skills\claudecode-model-evaluator\models.yaml --task-id demo --prompt-file prompt.txt --repo-path D:\work\repo --artifacts-dir D:\bench\demo
```

Linux:

```bash
./.codex/skills/claudecode-model-evaluator/bin/linux-amd64/eval_runner run-skill --models-file ./.codex/skills/claudecode-model-evaluator/models.yaml --task-id demo --prompt-file prompt.txt --repo-path /work/repo --artifacts-dir /tmp/bench/demo
```

## Build

Run `./build.ps1` on Windows PowerShell. It will:

1. run `go test ./...`
2. build `windows-amd64` and `linux-amd64`
3. place binaries into `.codex/skills/claudecode-model-evaluator/bin/...`

Keep real secrets only in a local `models.yaml`. The distributable skill directory ships only `models.example.yaml`.
