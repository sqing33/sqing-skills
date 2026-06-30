package execx

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// resolveBatchShim 应把 npm 的 claude.cmd 包装解析到底层 claude.exe,
// 以绕开 cmd.exe %* 对含换行参数的截断(多行 -p task packet 只剩第一行的根因)。
func TestResolveBatchShimPrefersExe(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅 Windows 相关")
	}
	dir := t.TempDir()
	cmdPath := filepath.Join(dir, "claude.cmd")
	if err := os.WriteFile(cmdPath, []byte("@echo off\r\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	exeDir := filepath.Join(dir, "node_modules", "@anthropic-ai", "claude-code", "bin")
	if err := os.MkdirAll(exeDir, 0o755); err != nil {
		t.Fatal(err)
	}
	exePath := filepath.Join(exeDir, "claude.exe")
	if err := os.WriteFile(exePath, []byte("MZ"), 0o644); err != nil {
		t.Fatal(err)
	}

	got := resolveBatchShim(cmdPath)
	if got != exePath {
		t.Errorf("期望解析到底层 exe %q,实际 %q", exePath, got)
	}
}

// 没有可用 .exe 时应原样返回,不破坏其它 launcher。
func TestResolveBatchShimFallsBack(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅 Windows 相关")
	}
	dir := t.TempDir()
	cmdPath := filepath.Join(dir, "other.cmd")
	if err := os.WriteFile(cmdPath, []byte("@echo off\r\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := resolveBatchShim(cmdPath); got != cmdPath {
		t.Errorf("无 exe 时应原样返回 %q,实际 %q", cmdPath, got)
	}
}

// 非 .cmd/.bat 路径不应被改写。
func TestResolveBatchShimIgnoresNonBatch(t *testing.T) {
	in := filepath.Join("some", "dir", "claude.exe")
	if got := resolveBatchShim(in); got != in {
		t.Errorf("非 batch 路径应原样返回 %q,实际 %q", in, got)
	}
}
