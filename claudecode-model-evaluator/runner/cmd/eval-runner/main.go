package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"claudecode-model-evaluator-go/internal/runner"
	"claudecode-model-evaluator-go/internal/spec"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		if _, ok := err.(*spec.SpecError); ok {
			fmt.Fprintf(os.Stderr, "[spec-error] %v\n", err)
		} else {
			fmt.Fprintf(os.Stderr, "[runner-error] %v\n", err)
		}
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("expected subcommand: run, run-skill, summarize")
	}
	switch args[0] {
	case "run":
		fs := flag.NewFlagSet("run", flag.ContinueOnError)
		specPath := fs.String("spec", "", "Path to the benchmark YAML spec.")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if *specPath == "" {
			return &spec.SpecError{Message: "--spec is required"}
		}
		cfg, err := spec.LoadSpec(*specPath)
		if err != nil {
			return err
		}
		summary, err := runner.RunBenchmark(cfg)
		if err != nil {
			return err
		}
		runner.PrintSummary(summary)
		return nil
	case "run-skill":
		fs := flag.NewFlagSet("run-skill", flag.ContinueOnError)
		modelsFile := fs.String("models-file", "", "Path to models.yaml.")
		taskID := fs.String("task-id", "", "Stable task identifier for this benchmark.")
		prompt := fs.String("prompt", "", "Task prompt passed to every model.")
		promptFile := fs.String("prompt-file", "", "Optional UTF-8 text file containing the task prompt.")
		repoPath := fs.String("repo-path", "", "Target repository or directory path.")
		artifactsDir := fs.String("artifacts-dir", "", "Output directory for benchmark artifacts.")
		acceptanceNotes := fs.String("acceptance-notes", "", "Optional acceptance notes.")
		acceptanceNotesFile := fs.String("acceptance-notes-file", "", "Optional UTF-8 text file containing acceptance notes.")
		setupCmd := fs.String("setup-cmd", "", "Optional setup command.")
		testCmd := fs.String("test-cmd", "", "Optional test command.")
		workspaceMode := fs.String("workspace-mode", "", "Optional workspace mode override.")
		rubricProfile := fs.String("rubric-profile", "", "Optional rubric profile override.")
		var allowedPaths stringList
		fs.Var(&allowedPaths, "allowed-path", "Allowed file or directory path. Repeat to provide multiple values.")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if *modelsFile == "" || *taskID == "" || *repoPath == "" || *artifactsDir == "" {
			return &spec.SpecError{Message: "--models-file, --task-id, --repo-path, and --artifacts-dir are required"}
		}
		promptText, err := spec.LoadTextArg(*prompt, *promptFile, "prompt")
		if err != nil {
			return err
		}
		if promptText == "" {
			return &spec.SpecError{Message: "run-skill requires either --prompt or --prompt-file"}
		}
		acceptanceText, err := spec.LoadTextArg(*acceptanceNotes, *acceptanceNotesFile, "acceptance-notes")
		if err != nil {
			return err
		}
		cfg, err := spec.BuildSpecFromModelsFile(spec.BuildSpecFromModelsFileInput{
			ModelsPath:      *modelsFile,
			TaskID:          *taskID,
			Prompt:          promptText,
			RepoPath:        *repoPath,
			ArtifactsDir:    *artifactsDir,
			AcceptanceNotes: acceptanceText,
			AllowedPaths:    allowedPaths,
			SetupCmd:        emptyToNil(*setupCmd),
			TestCmd:         emptyToNil(*testCmd),
			WorkspaceMode:   emptyToNil(*workspaceMode),
			RubricProfile:   emptyToNil(*rubricProfile),
		})
		if err != nil {
			return err
		}
		summary, err := runner.RunBenchmark(cfg)
		if err != nil {
			return err
		}
		runner.PrintSummary(summary)
		return nil
	case "summarize":
		fs := flag.NewFlagSet("summarize", flag.ContinueOnError)
		artifactsDir := fs.String("artifacts-dir", "", "Path to an existing artifact directory.")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if *artifactsDir == "" {
			return &spec.SpecError{Message: "--artifacts-dir is required"}
		}
		summary, err := runner.SummarizeExistingArtifacts(filepath.Clean(*artifactsDir))
		if err != nil {
			return err
		}
		runner.PrintSummary(summary)
		return nil
	default:
		return fmt.Errorf("unknown subcommand %q", args[0])
	}
}

type stringList []string

func (s *stringList) String() string {
	return fmt.Sprintf("%v", []string(*s))
}

func (s *stringList) Set(value string) error {
	*s = append(*s, value)
	return nil
}

func emptyToNil(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}
