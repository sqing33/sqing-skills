package spec

import (
	"os"
	"path/filepath"
	"testing"
)

func TestBuildSpecFromModelsFile(t *testing.T) {
	dir := t.TempDir()
	modelsPath := filepath.Join(dir, "models.yaml")
	repoPath := filepath.Join(dir, "repo")
	if err := os.MkdirAll(repoPath, 0o755); err != nil {
		t.Fatal(err)
	}
	content := "models:\n  - id: demo\n    label: Demo\n    launcher:\n      type: claude-cli\n      model: demo-model\n      max_turns: 10\n      extra_args: []\n    env:\n      ANTHROPIC_BASE_URL: https://example.com\n    headers:\n      X-Provider: demo\n    timeout_minutes: 5\nexecution:\n  max_parallel: 2\n  workspace_mode: copy\nrubric:\n  profile: coding-default\n"
	if err := os.WriteFile(modelsPath, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := BuildSpecFromModelsFile(BuildSpecFromModelsFileInput{ModelsPath: modelsPath, TaskID: "task-1", Prompt: "Fix it", RepoPath: repoPath, ArtifactsDir: filepath.Join(dir, "artifacts"), AllowedPaths: []string{"src\\app.go"}})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Execution.WorkspaceMode != WorkspaceCopy {
		t.Fatalf("unexpected workspace mode: %s", cfg.Execution.WorkspaceMode)
	}
	if len(cfg.Models) != 1 || cfg.Models[0].Launcher == nil || cfg.Models[0].Launcher.Model != "demo-model" {
		t.Fatalf("unexpected models: %#v", cfg.Models)
	}
	if got := cfg.Models[0].Headers["X-Provider"]; got != "demo" {
		t.Fatalf("unexpected header: %s", got)
	}
	if got := cfg.Task.AllowedPaths[0]; got != "src/app.go" {
		t.Fatalf("unexpected normalized path: %s", got)
	}
}

func TestBuildSpecFromShortModelName(t *testing.T) {
	dir := t.TempDir()
	modelsPath := filepath.Join(dir, "models.yaml")
	repoPath := filepath.Join(dir, "repo")
	if err := os.MkdirAll(repoPath, 0o755); err != nil {
		t.Fatal(err)
	}
	content := "models:\n  - model: MiniMax-M3\n    env:\n      ANTHROPIC_BASE_URL: https://example.com\n"
	if err := os.WriteFile(modelsPath, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := BuildSpecFromModelsFile(BuildSpecFromModelsFileInput{ModelsPath: modelsPath, TaskID: "task-1", Prompt: "Fix it", RepoPath: repoPath, ArtifactsDir: filepath.Join(dir, "artifacts")})
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.Models) != 1 {
		t.Fatalf("unexpected model count: %d", len(cfg.Models))
	}
	if cfg.Execution.MaxParallel != 1 || cfg.Execution.WorkspaceMode != WorkspaceGitWorktree {
		t.Fatalf("unexpected default execution: %#v", cfg.Execution)
	}
	if cfg.Rubric.Profile != "coding-default" {
		t.Fatalf("unexpected default rubric: %#v", cfg.Rubric)
	}
	model := cfg.Models[0]
	if model.ID != "minimax-m3" || model.Label != "MiniMax-M3" {
		t.Fatalf("unexpected inferred identity: id=%q label=%q", model.ID, model.Label)
	}
	if model.Launcher == nil || model.Launcher.Type != "claude-cli" || model.Launcher.Model != "MiniMax-M3" {
		t.Fatalf("unexpected launcher: %#v", model.Launcher)
	}
	if model.TimeoutMinutes != 0 {
		t.Fatalf("unexpected default timeout: %v", model.TimeoutMinutes)
	}
	if model.BudgetUSD != nil {
		t.Fatalf("unexpected default budget: %v", *model.BudgetUSD)
	}
}

func TestDefaultMaxParallelUsesModelCount(t *testing.T) {
	dir := t.TempDir()
	modelsPath := filepath.Join(dir, "models.yaml")
	repoPath := filepath.Join(dir, "repo")
	if err := os.MkdirAll(repoPath, 0o755); err != nil {
		t.Fatal(err)
	}
	content := "models:\n  - model: model-a\n  - model: model-b\n  - model: model-c\n"
	if err := os.WriteFile(modelsPath, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := BuildSpecFromModelsFile(BuildSpecFromModelsFileInput{ModelsPath: modelsPath, TaskID: "task-1", Prompt: "Fix it", RepoPath: repoPath, ArtifactsDir: filepath.Join(dir, "artifacts")})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Execution.MaxParallel != 3 {
		t.Fatalf("unexpected default max parallel: %d", cfg.Execution.MaxParallel)
	}
}
