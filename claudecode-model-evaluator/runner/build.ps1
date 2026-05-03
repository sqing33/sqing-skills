$ErrorActionPreference = 'Stop'

$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir = Split-Path -Parent $RunnerDir
Set-Location $RunnerDir

$env:GO111MODULE = 'on'
& go test ./...

$targets = @(
    @{ GOOS = 'linux';   GOARCH = 'amd64'; Out = 'bin/linux-amd64/eval_runner' },
    @{ GOOS = 'darwin';  GOARCH = 'arm64'; Out = 'bin/macos-arm64/eval_runner' },
    @{ GOOS = 'windows'; GOARCH = 'amd64'; Out = 'bin/windows-amd64/eval_runner.exe' }
)

foreach ($target in $targets) {
    $outPath = Join-Path $SkillDir $target.Out
    $outDir = Split-Path -Parent $outPath
    if (-not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Force $outDir | Out-Null
    }

    Write-Host "Building $($target.GOOS)/$($target.GOARCH) -> $($target.Out)"
    $env:GOOS = $target.GOOS
    $env:GOARCH = $target.GOARCH
    & go build -o $outPath ./cmd/eval-runner
}

Remove-Item Env:GOOS -ErrorAction SilentlyContinue
Remove-Item Env:GOARCH -ErrorAction SilentlyContinue
