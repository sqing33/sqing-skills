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
	if err := os.MkdirAll(repoPath, 0o755); err != nil { t.Fatal(err) }
	content := "models:\n  - id: demo\n    label: Demo\n    launcher:\n      type: claude-cli\n      model: demo-model\n      max_turns: 10\n      extra_args: []\n    env:\n      ANTHROPIC_BASE_URL: https://example.com\n    timeout_minutes: 5\nexecution:\n  max_parallel: 2\n  workspace_mode: copy\nrubric:\n  profile: coding-default\n"
	if err := os.WriteFile(modelsPath, []byte(content), 0o644); err != nil { t.Fatal(err) }
	cfg, err := BuildSpecFromModelsFile(BuildSpecFromModelsFileInput{ModelsPath: modelsPath, TaskID: "task-1", Prompt: "Fix it", RepoPath: repoPath, ArtifactsDir: filepath.Join(dir, "artifacts"), AllowedPaths: []string{"src\\app.go"}})
	if err != nil { t.Fatal(err) }
	if cfg.Execution.WorkspaceMode != WorkspaceCopy { t.Fatalf("unexpected workspace mode: %s", cfg.Execution.WorkspaceMode) }
	if len(cfg.Models) != 1 || cfg.Models[0].Launcher == nil || cfg.Models[0].Launcher.Model != "demo-model" { t.Fatalf("unexpected models: %#v", cfg.Models) }
	if got := cfg.Task.AllowedPaths[0]; got != "src/app.go" { t.Fatalf("unexpected normalized path: %s", got) }
}
