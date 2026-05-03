package spec

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const (
	WorkspaceGitWorktree = "git-worktree"
	WorkspaceCopy        = "copy"
)

type SpecError struct {
	Message string
}

func (e *SpecError) Error() string { return e.Message }

type TaskSpec struct {
	ID              string   `json:"id"`
	Prompt          string   `json:"prompt"`
	AcceptanceNotes string   `json:"acceptance_notes"`
	AllowedPaths    []string `json:"allowed_paths"`
}

type TargetSpec struct {
	RepoPath string  `json:"repo_path"`
	SetupCmd *string `json:"setup_cmd"`
	TestCmd  *string `json:"test_cmd"`
}

type LauncherSpec struct {
	Type      string   `json:"type"`
	Model     string   `json:"model"`
	MaxTurns  int      `json:"max_turns"`
	ExtraArgs []string `json:"extra_args"`
}

type ModelSpec struct {
	ID             string            `json:"id"`
	Label          string            `json:"label"`
	LaunchCmd      *string           `json:"launch_cmd"`
	Launcher       *LauncherSpec     `json:"launcher"`
	Env            map[string]string `json:"env"`
	TimeoutMinutes float64           `json:"timeout_minutes"`
	BudgetUSD      *float64          `json:"budget_usd"`
}

type ExecutionSpec struct {
	ArtifactsDir  string `json:"artifacts_dir"`
	MaxParallel   int    `json:"max_parallel"`
	WorkspaceMode string `json:"workspace_mode"`
}

type RubricSpec struct {
	Profile string `json:"profile"`
}

type BenchmarkSpec struct {
	Task       TaskSpec      `json:"task"`
	Target     TargetSpec    `json:"target"`
	Models     []ModelSpec   `json:"models"`
	Execution  ExecutionSpec `json:"execution"`
	Rubric     RubricSpec    `json:"rubric"`
	RawText    string        `json:"-"`
	SourcePath string        `json:"-"`
}

type BuildSpecFromModelsFileInput struct {
	ModelsPath      string
	TaskID          string
	Prompt          string
	RepoPath        string
	ArtifactsDir    string
	AcceptanceNotes string
	AllowedPaths    []string
	SetupCmd        *string
	TestCmd         *string
	WorkspaceMode   *string
	RubricProfile   *string
}

type modelsFile struct {
	Models    []ModelSpec   `json:"models"`
	Execution ExecutionSpec `json:"execution"`
	Rubric    RubricSpec    `json:"rubric"`
}

func LoadSpec(path string) (*BenchmarkSpec, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, &SpecError{Message: fmt.Sprintf("Spec file not found: %s", path)}
	}
	var cfg BenchmarkSpec
	if err := unmarshalStructured(raw, &cfg); err != nil {
		return nil, err
	}
	cfg.RawText = string(raw)
	cfg.SourcePath, _ = filepath.Abs(path)
	if err := validateBenchmarkSpec(&cfg, true); err != nil {
		return nil, err
	}
	return &cfg, nil
}

func LoadModelsFile(path string) ([]ModelSpec, ExecutionSpec, RubricSpec, string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, ExecutionSpec{}, RubricSpec{}, "", &SpecError{Message: fmt.Sprintf("Models file not found: %s", path)}
	}
	var cfg modelsFile
	if err := unmarshalStructured(raw, &cfg); err != nil {
		return nil, ExecutionSpec{}, RubricSpec{}, "", err
	}
	if cfg.Execution.MaxParallel <= 0 {
		cfg.Execution.MaxParallel = 1
	}
	if cfg.Execution.WorkspaceMode == "" {
		cfg.Execution.WorkspaceMode = WorkspaceGitWorktree
	}
	if cfg.Rubric.Profile == "" {
		cfg.Rubric.Profile = "coding-default"
	}
	if err := validateModels(cfg.Models); err != nil {
		return nil, ExecutionSpec{}, RubricSpec{}, "", err
	}
	if err := validateWorkspaceMode(cfg.Execution.WorkspaceMode); err != nil {
		return nil, ExecutionSpec{}, RubricSpec{}, "", err
	}
	return cfg.Models, cfg.Execution, cfg.Rubric, string(raw), nil
}

func BuildSpecFromModelsFile(input BuildSpecFromModelsFileInput) (*BenchmarkSpec, error) {
	models, execution, rubric, rawText, err := LoadModelsFile(input.ModelsPath)
	if err != nil {
		return nil, err
	}
	repoPath, _ := filepath.Abs(input.RepoPath)
	if _, err := os.Stat(repoPath); err != nil {
		return nil, &SpecError{Message: fmt.Sprintf("target.repo_path does not exist: %s", repoPath)}
	}
	artifactsDir, _ := filepath.Abs(input.ArtifactsDir)
	workspaceMode := execution.WorkspaceMode
	if input.WorkspaceMode != nil {
		workspaceMode = *input.WorkspaceMode
	}
	if err := validateWorkspaceMode(workspaceMode); err != nil {
		return nil, err
	}
	profile := rubric.Profile
	if input.RubricProfile != nil && *input.RubricProfile != "" {
		profile = *input.RubricProfile
	}
	cfg := &BenchmarkSpec{
		Task:      TaskSpec{ID: input.TaskID, Prompt: input.Prompt, AcceptanceNotes: input.AcceptanceNotes, AllowedPaths: normalizeAllowedPaths(input.AllowedPaths)},
		Target:    TargetSpec{RepoPath: repoPath, SetupCmd: input.SetupCmd, TestCmd: input.TestCmd},
		Models:    models,
		Execution: ExecutionSpec{ArtifactsDir: artifactsDir, MaxParallel: execution.MaxParallel, WorkspaceMode: workspaceMode},
		Rubric:    RubricSpec{Profile: profile},
		RawText:   rawText,
	}
	cfg.SourcePath, _ = filepath.Abs(input.ModelsPath)
	if err := validateBenchmarkSpec(cfg, false); err != nil {
		return nil, err
	}
	return cfg, nil
}

func LoadTextArg(value, filePath, argName string) (string, error) {
	if value != "" && filePath != "" {
		return "", &SpecError{Message: fmt.Sprintf("Use only one of --%s or --%s-file.", argName, argName)}
	}
	if filePath != "" {
		raw, err := os.ReadFile(filePath)
		if err != nil {
			return "", &SpecError{Message: fmt.Sprintf("%s file not found: %s", argName, filePath)}
		}
		return strings.TrimPrefix(string(raw), "\ufeff"), nil
	}
	return value, nil
}

func SpecToMap(cfg *BenchmarkSpec) map[string]any {
	return map[string]any{
		"task":      map[string]any{"id": cfg.Task.ID, "prompt": cfg.Task.Prompt, "acceptance_notes": cfg.Task.AcceptanceNotes, "allowed_paths": toAnySlice(cfg.Task.AllowedPaths)},
		"target":    map[string]any{"repo_path": cfg.Target.RepoPath, "setup_cmd": cfg.Target.SetupCmd, "test_cmd": cfg.Target.TestCmd},
		"models":    modelsToAny(cfg.Models),
		"execution": map[string]any{"artifacts_dir": cfg.Execution.ArtifactsDir, "max_parallel": cfg.Execution.MaxParallel, "workspace_mode": cfg.Execution.WorkspaceMode},
		"rubric":    map[string]any{"profile": cfg.Rubric.Profile},
	}
}

func MarshalSpecJSON(cfg *BenchmarkSpec) ([]byte, error) {
	return json.MarshalIndent(SpecToMap(cfg), "", "  ")
}
func MarshalSpecYAML(cfg *BenchmarkSpec) ([]byte, error) {
	return []byte(marshalYAML(SpecToMap(cfg), 0) + "\n"), nil
}

func unmarshalStructured(raw []byte, out any) error {
	trimmed := strings.TrimSpace(strings.TrimPrefix(string(raw), "\ufeff"))
	if trimmed == "" {
		return &SpecError{Message: "empty YAML/JSON input"}
	}
	if strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "[") {
		if err := json.Unmarshal([]byte(trimmed), out); err != nil {
			return &SpecError{Message: fmt.Sprintf("invalid JSON: %v", err)}
		}
		return nil
	}
	parsed, err := parseYAML(trimmed)
	if err != nil {
		return err
	}
	serialized, err := json.Marshal(parsed)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(serialized, out); err != nil {
		return &SpecError{Message: fmt.Sprintf("invalid YAML shape: %v", err)}
	}
	return nil
}

func validateBenchmarkSpec(cfg *BenchmarkSpec, requireArtifacts bool) error {
	if strings.TrimSpace(cfg.Task.ID) == "" {
		return &SpecError{Message: "task.id must not be empty"}
	}
	if strings.TrimSpace(cfg.Task.Prompt) == "" {
		return &SpecError{Message: "task.prompt must not be empty"}
	}
	cfg.Task.AllowedPaths = normalizeAllowedPaths(cfg.Task.AllowedPaths)
	repoPath, _ := filepath.Abs(cfg.Target.RepoPath)
	cfg.Target.RepoPath = repoPath
	if _, err := os.Stat(repoPath); err != nil {
		return &SpecError{Message: fmt.Sprintf("target.repo_path does not exist: %s", repoPath)}
	}
	if requireArtifacts && strings.TrimSpace(cfg.Execution.ArtifactsDir) == "" {
		return &SpecError{Message: "execution.artifacts_dir must not be empty"}
	}
	if cfg.Execution.MaxParallel <= 0 {
		cfg.Execution.MaxParallel = 1
	}
	if cfg.Execution.WorkspaceMode == "" {
		cfg.Execution.WorkspaceMode = WorkspaceGitWorktree
	}
	if cfg.Rubric.Profile == "" {
		cfg.Rubric.Profile = "coding-default"
	}
	if err := validateWorkspaceMode(cfg.Execution.WorkspaceMode); err != nil {
		return err
	}
	return validateModels(cfg.Models)
}

func validateModels(models []ModelSpec) error {
	if len(models) == 0 {
		return &SpecError{Message: "models must not be empty"}
	}
	for i := range models {
		m := &models[i]
		if strings.TrimSpace(m.ID) == "" {
			return &SpecError{Message: fmt.Sprintf("models[%d].id must not be empty", i)}
		}
		if strings.TrimSpace(m.Label) == "" {
			m.Label = m.ID
		}
		if m.Env == nil {
			m.Env = map[string]string{}
		}
		if m.TimeoutMinutes <= 0 {
			m.TimeoutMinutes = 20
		}
		if m.Launcher != nil {
			if m.Launcher.Type != "claude-cli" {
				return &SpecError{Message: fmt.Sprintf("models[%s].launcher.type must be 'claude-cli'", m.ID)}
			}
			if strings.TrimSpace(m.Launcher.Model) == "" {
				return &SpecError{Message: fmt.Sprintf("models[%s].launcher.model must not be empty", m.ID)}
			}
			if m.Launcher.MaxTurns <= 0 {
				m.Launcher.MaxTurns = 25
			}
			if m.Launcher.ExtraArgs == nil {
				m.Launcher.ExtraArgs = []string{}
			}
		}
		if m.Launcher == nil && (m.LaunchCmd == nil || strings.TrimSpace(*m.LaunchCmd) == "") {
			return &SpecError{Message: fmt.Sprintf("models[%s] must define launcher or launch_cmd", m.ID)}
		}
	}
	return nil
}

func normalizeAllowedPaths(paths []string) []string {
	out := make([]string, 0, len(paths))
	for _, item := range paths {
		item = strings.ReplaceAll(item, "\\", "/")
		item = strings.TrimLeft(item, "./")
		if item == "" {
			continue
		}
		out = append(out, item)
	}
	return out
}

func validateWorkspaceMode(mode string) error {
	if mode != WorkspaceGitWorktree && mode != WorkspaceCopy {
		return &SpecError{Message: "workspace_mode must be 'git-worktree' or 'copy'"}
	}
	return nil
}

func toAnySlice(items []string) []any {
	out := make([]any, 0, len(items))
	for _, item := range items {
		out = append(out, item)
	}
	return out
}
func modelsToAny(models []ModelSpec) []any {
	out := make([]any, 0, len(models))
	for _, model := range models {
		entry := map[string]any{"id": model.ID, "label": model.Label, "env": map[string]any{}, "timeout_minutes": model.TimeoutMinutes, "budget_usd": model.BudgetUSD}
		if model.LaunchCmd != nil {
			entry["launch_cmd"] = *model.LaunchCmd
		}
		if model.Launcher != nil {
			entry["launcher"] = map[string]any{"type": model.Launcher.Type, "model": model.Launcher.Model, "max_turns": model.Launcher.MaxTurns, "extra_args": toAnySlice(model.Launcher.ExtraArgs)}
		}
		env := map[string]any{}
		for k, v := range model.Env {
			env[k] = v
		}
		entry["env"] = env
		out = append(out, entry)
	}
	return out
}
