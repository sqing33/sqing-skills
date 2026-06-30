package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

// TestCopyTreeCopiesIntoArtifactsSubdir guards the regression where the skip
// guard tested the destination path. Because the workspace destination always
// lives under artifactsDir, the old guard skipped every file and left every
// model workspace empty. Source files must be copied; only paths under
// artifactsDir within the source tree should be skipped.
func TestCopyTreeCopiesIntoArtifactsSubdir(t *testing.T) {
	src := t.TempDir()
	if err := os.WriteFile(filepath.Join(src, "task.go"), []byte("package vibecoding\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "go.mod"), []byte("module vibecoding\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	// Destination workspace lives INSIDE the artifacts dir, which itself lives
	// inside the source tree — exactly the layout used by the runner.
	artifactsDir := filepath.Join(src, "_artifacts")
	dst := filepath.Join(artifactsDir, "models", "m1", "workspace")

	if err := CopyTree(src, dst, artifactsDir); err != nil {
		t.Fatalf("CopyTree: %v", err)
	}

	for _, name := range []string{"task.go", "go.mod"} {
		if _, err := os.Stat(filepath.Join(dst, name)); err != nil {
			t.Errorf("expected %s to be copied into workspace, got: %v", name, err)
		}
	}

	// The artifacts dir within the source must NOT be recursively copied.
	if _, err := os.Stat(filepath.Join(dst, "_artifacts")); !os.IsNotExist(err) {
		t.Errorf("artifacts dir should be skipped during copy, stat err=%v", err)
	}
}
