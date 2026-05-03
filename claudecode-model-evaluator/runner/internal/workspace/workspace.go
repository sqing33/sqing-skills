package workspace

import (
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"claudecode-model-evaluator-go/internal/execx"
	"claudecode-model-evaluator-go/internal/spec"
)

var ignoreDirNames = map[string]bool{
	".cache": true, ".git": true, ".hg": true, ".mypy_cache": true, ".next": true,
	".pytest_cache": true, ".ruff_cache": true, ".svn": true, ".turbo": true, ".venv": true,
	"__pycache__": true, "build": true, "dist": true, "node_modules": true, "target": true, "venv": true,
}

type FileSnapshot struct {
	Path   string `json:"path"`
	Digest string `json:"digest"`
	Size   int64  `json:"size"`
	Binary bool   `json:"binary"`
	Text   string `json:"text,omitempty"`
}

func PrepareArtifactRoot(cfg *spec.BenchmarkSpec) error {
	if err := os.MkdirAll(cfg.Execution.ArtifactsDir, 0o755); err != nil {
		return err
	}
	runSpecJSON, err := spec.MarshalSpecJSON(cfg)
	if err != nil {
		return err
	}
	runSpecYAML, err := spec.MarshalSpecYAML(cfg)
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(cfg.Execution.ArtifactsDir, "run_spec.json"), append(runSpecJSON, '\n'), 0o644); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(cfg.Execution.ArtifactsDir, "run_spec.yaml"), runSpecYAML, 0o644)
}

func EnsureCleanDirectory(path string) error {
	if err := os.RemoveAll(path); err != nil {
		return err
	}
	return os.MkdirAll(path, 0o755)
}

func PrepareWorkspace(cfg *spec.BenchmarkSpec, model spec.ModelSpec) (string, string, error) {
	modelDir := filepath.Join(cfg.Execution.ArtifactsDir, "models", model.ID)
	workspace := filepath.Join(modelDir, "workspace")
	if cfg.Execution.WorkspaceMode == spec.WorkspaceGitWorktree {
		gitRoot := MaybeGitRoot(cfg.Target.RepoPath)
		if gitRoot != "" {
			res := execx.RunProcess([]string{"git", "worktree", "add", "--detach", workspace, "HEAD"}, gitRoot, nil, 0)
			if res.ExitCode != nil && *res.ExitCode == 0 {
				return workspace, spec.WorkspaceGitWorktree, nil
			}
		}
	}
	if err := CopyTree(cfg.Target.RepoPath, workspace, cfg.Execution.ArtifactsDir); err != nil {
		return "", "", err
	}
	return workspace, spec.WorkspaceCopy, nil
}

func MaybeGitRoot(path string) string {
	res := execx.RunProcess([]string{"git", "rev-parse", "--show-toplevel"}, path, nil, 0)
	if res.ExitCode == nil || *res.ExitCode != 0 {
		return ""
	}
	return strings.TrimSpace(res.Stdout)
}

func CopyTree(src, dst, artifactsDir string) error {
	return filepath.WalkDir(src, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return os.MkdirAll(dst, 0o755)
		}
		name := d.Name()
		if d.IsDir() && ignoreDirNames[name] {
			return filepath.SkipDir
		}
		target := filepath.Join(dst, rel)
		if strings.HasPrefix(target, artifactsDir) {
			return nil
		}
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		return copyFile(path, target)
	})
}

func SnapshotWorkspace(root string) (map[string]FileSnapshot, error) {
	items := map[string]FileSnapshot{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() && ignoreDirNames[d.Name()] {
			return filepath.SkipDir
		}
		if d.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = normalizeRelPath(rel)
		info, err := d.Info()
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		hash := sha1.Sum(data)
		binary := isBinary(data)
		entry := FileSnapshot{Path: rel, Digest: hex.EncodeToString(hash[:]), Size: info.Size(), Binary: binary}
		if !binary && len(data) <= 512000 {
			entry.Text = string(data)
		}
		items[rel] = entry
		return nil
	})
	return items, err
}

func BuildPatchAndChanges(before, after map[string]FileSnapshot) ([]map[string]any, string) {
	keys := map[string]bool{}
	for k := range before {
		keys[k] = true
	}
	for k := range after {
		keys[k] = true
	}
	all := make([]string, 0, len(keys))
	for k := range keys {
		all = append(all, k)
	}
	sort.Strings(all)
	changes := make([]map[string]any, 0)
	patches := make([]string, 0)
	for _, key := range all {
		oldFile, hadOld := before[key]
		newFile, hadNew := after[key]
		if hadOld && hadNew && oldFile.Digest == newFile.Digest {
			continue
		}
		status := "modified"
		if !hadOld {
			status = "added"
		}
		if !hadNew {
			status = "deleted"
		}
		added, removed := computeLineDelta(oldFile.Text, newFile.Text)
		changes = append(changes, map[string]any{
			"path":          key,
			"status":        status,
			"added_lines":   added,
			"removed_lines": removed,
			"binary":        ternaryBool((hadNew && newFile.Binary) || (hadOld && oldFile.Binary)),
		})
		patches = append(patches, fmt.Sprintf("diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n", key, key, key, key))
		if oldFile.Text != "" || newFile.Text != "" {
			patches = append(patches, simpleUnified(oldFile.Text, newFile.Text))
		}
	}
	return changes, strings.Join(patches, "")
}

func SummarizeDiff(changes []map[string]any) map[string]any {
	summary := map[string]any{"files_changed": len(changes), "added_lines": 0, "removed_lines": 0}
	for _, change := range changes {
		if n, ok := change["added_lines"].(int); ok {
			summary["added_lines"] = summary["added_lines"].(int) + n
		}
		if n, ok := change["removed_lines"].(int); ok {
			summary["removed_lines"] = summary["removed_lines"].(int) + n
		}
	}
	return summary
}

func DetectConstraintFlags(changes []map[string]any, allowedPaths []string) []string {
	flags := []string{}
	if len(changes) == 0 {
		flags = append(flags, "no_diff")
	}
	if len(allowedPaths) == 0 {
		return flags
	}
	for _, change := range changes {
		path, _ := change["path"].(string)
		ok := false
		for _, allowed := range allowedPaths {
			if path == allowed || strings.HasPrefix(path, strings.TrimSuffix(allowed, "/")+"/") || strings.HasPrefix(path, allowed) {
				ok = true
				break
			}
		}
		if !ok {
			flags = append(flags, "out_of_scope_changes")
			break
		}
	}
	return flags
}

func normalizeRelPath(path string) string {
	return strings.TrimLeft(strings.ReplaceAll(path, "\\", "/"), "./")
}

func copyFile(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	if _, err := io.Copy(out, in); err != nil {
		return err
	}
	return out.Close()
}

func isBinary(data []byte) bool {
	limit := len(data)
	if limit > 8000 {
		limit = 8000
	}
	for _, b := range data[:limit] {
		if b == 0 {
			return true
		}
	}
	return false
}

func computeLineDelta(oldText, newText string) (int, int) {
	oldLines := splitLines(oldText)
	newLines := splitLines(newText)
	oldSet := map[string]int{}
	newSet := map[string]int{}
	for _, line := range oldLines {
		oldSet[line]++
	}
	for _, line := range newLines {
		newSet[line]++
	}
	added, removed := 0, 0
	for line, count := range newSet {
		if count > oldSet[line] {
			added += count - oldSet[line]
		}
	}
	for line, count := range oldSet {
		if count > newSet[line] {
			removed += count - newSet[line]
		}
	}
	return added, removed
}

func splitLines(text string) []string {
	if text == "" {
		return nil
	}
	return strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
}

func simpleUnified(oldText, newText string) string {
	oldLines := splitLines(oldText)
	newLines := splitLines(newText)
	parts := []string{"@@\n"}
	for _, line := range oldLines {
		parts = append(parts, "-"+line+"\n")
	}
	for _, line := range newLines {
		parts = append(parts, "+"+line+"\n")
	}
	return strings.Join(parts, "")
}

func ternaryBool(value bool) bool { return value }
