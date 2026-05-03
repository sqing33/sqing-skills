package runner

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"claudecode-model-evaluator-go/internal/claudestream"
	"claudecode-model-evaluator-go/internal/execx"
	"claudecode-model-evaluator-go/internal/report"
	"claudecode-model-evaluator-go/internal/spec"
	"claudecode-model-evaluator-go/internal/workspace"
)

type eventLogger struct {
	path string
	mu   sync.Mutex
}

func newEventLogger(path string) *eventLogger {
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	return &eventLogger{path: path}
}
func (e *eventLogger) emit(event string, payload map[string]any) {
	e.mu.Lock()
	defer e.mu.Unlock()
	record := map[string]any{"event": event, "timestamp": time.Now().UTC().Format(time.RFC3339Nano)}
	for k, v := range payload {
		record[k] = v
	}
	data, _ := json.Marshal(record)
	f, _ := os.OpenFile(e.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	defer f.Close()
	_, _ = f.Write(append(data, '\n'))
}

func RunBenchmark(cfg *spec.BenchmarkSpec) (map[string]any, error) {
	if err := workspace.PrepareArtifactRoot(cfg); err != nil {
		return nil, err
	}
	results := make([]map[string]any, len(cfg.Models))
	if cfg.Execution.MaxParallel <= 1 || len(cfg.Models) <= 1 {
		for i, model := range cfg.Models {
			result, err := runOneModel(cfg, model)
			if err != nil {
				return nil, err
			}
			results[i] = result
		}
	} else {
		var wg sync.WaitGroup
		sem := make(chan struct{}, cfg.Execution.MaxParallel)
		errCh := make(chan error, len(cfg.Models))
		for i, model := range cfg.Models {
			wg.Add(1)
			go func(idx int, m spec.ModelSpec) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()
				result, err := runOneModel(cfg, m)
				if err != nil {
					errCh <- err
					return
				}
				results[idx] = result
			}(i, model)
		}
		wg.Wait()
		close(errCh)
		if err := <-errCh; err != nil {
			return nil, err
		}
	}
	summary := buildSummary(cfg, results)
	if err := report.PersistResults(cfg.Execution.ArtifactsDir, summary); err != nil {
		return nil, err
	}
	return summary, nil
}

func SumarizeRecoveryStream(modelDir string) map[string]any {
	path := filepath.Join(modelDir, "claude_stream.jsonl")
	if _, err := os.Stat(path); err != nil {
		path = filepath.Join(modelDir, "stdout.log")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return map[string]any{}
	}
	parsed := claudestream.Parse(string(raw))
	return map[string]any{
		"session_id":      parsed.SessionID,
		"num_turns":       parsed.NumTurns,
		"result_subtype":  parsed.ResultSubtype,
		"duration_ms":     parsed.DurationMS,
		"duration_api_ms": parsed.DurationAPIMS,
		"total_cost_usd":  parsed.TotalCostUSD,
		"usage":           parsed.Usage,
		"result_text":     parsed.ResultText,
	}
}

func SummarizeExistingArtifacts(artifactsDir string) (map[string]any, error) {
	cfg, err := loadExistingSpec(artifactsDir)
	if err != nil {
		return nil, err
	}
	results := make([]map[string]any, 0, len(cfg.Models))
	taskMode, taskSignals := report.DetectTaskMode(cfg.Task.Prompt, cfg.Task.AcceptanceNotes)
	for _, model := range cfg.Models {
		modelDir := filepath.Join(artifactsDir, "models", model.ID)
		statusPayload, err := readJSONMap(filepath.Join(modelDir, "status.json"))
		if err != nil {
			return nil, err
		}
		metrics, _ := readJSONMap(filepath.Join(modelDir, "metrics.json"))
		testsPayload, _ := readJSONMap(filepath.Join(modelDir, "tests.json"))
		filesPayload, _ := readJSONMap(filepath.Join(modelDir, "files.json"))
		files := getFileList(filesPayload)
		stream := SumarizeRecoveryStream(modelDir)
		analysisText := firstNonEmptyString(statusPayload["analysis_text"], stream["result_text"])
		hasAnalysis := strings.TrimSpace(analysisText) != ""
		flags := uniqueStrings(append(toStringSlice(statusPayload["constraint_flags"]), detectAnalysisFlags(hasAnalysis)...))
		if taskMode == "readonly_review" {
			flags = append(flags, "readonly_task")
		}
		resultSubtype := firstNonNil(statusPayload["result_subtype"], metrics["result_subtype"], stream["result_subtype"])
		launch := map[string]any{"timed_out": metrics["launch_timed_out"], "error": nil}
		testStatus := nestedStatus(statusPayload, testsPayload)
		status := report.PickStatus(taskMode, launch, false, testStatus, files, resultSubtype, hasAnalysis)
		statusPayload["task_mode"] = taskMode
		statusPayload["task_mode_signals"] = taskSignals
		statusPayload["files_touched"] = collectFilePaths(files)
		statusPayload["tests"] = map[string]any{"status": testStatus}
		statusPayload["constraint_flags"] = uniqueStrings(flags)
		statusPayload["analysis_text"] = claudestream.TrimText(analysisText, claudestream.AnalysisExcerptLimit)
		statusPayload["analysis_excerpt"] = statusPayload["analysis_text"]
		statusPayload["process_summary"] = buildProcessSummary(taskMode, modelDir, collectFilePaths(files))
		statusPayload["usage"] = mergeUsage(metrics["usage"], statusPayload["usage"], stream["usage"])
		statusPayload["status"] = status
		statusPayload["correctness_state"] = report.BuildCorrectnessState(status, testStatus, len(files) > 0, hasAnalysis)
		statusPayload["failure_labels"] = report.BuildFailureLabels(status, testStatus, uniqueStrings(flags), files)
		statusPayload["artifacts"] = relativeArtifactPaths(artifactsDir, modelDir)
		results = append(results, statusPayload)
	}
	summary := buildSummary(cfg, results)
	if err := report.PersistResults(cfg.Execution.ArtifactsDir, summary); err != nil {
		return nil, err
	}
	return summary, nil
}

func runOneModel(cfg *spec.BenchmarkSpec, model spec.ModelSpec) (map[string]any, error) {
	modelDir := filepath.Join(cfg.Execution.ArtifactsDir, "models", model.ID)
	if err := workspace.EnsureCleanDirectory(modelDir); err != nil {
		return nil, err
	}
	logger := newEventLogger(filepath.Join(modelDir, "events.jsonl"))
	taskMode, taskSignals := report.DetectTaskMode(cfg.Task.Prompt, cfg.Task.AcceptanceNotes)
	logger.emit("run_started", map[string]any{"model_id": model.ID, "model_label": model.Label})
	workdir, actualMode, err := workspace.PrepareWorkspace(cfg, model)
	if err != nil {
		return nil, err
	}
	taskPacket := makeTaskPacket(cfg, model)
	taskPacketPath := filepath.Join(modelDir, "task_packet.md")
	if err := os.WriteFile(taskPacketPath, []byte(taskPacket), 0o644); err != nil {
		return nil, err
	}
	before, err := workspace.SnapshotWorkspace(workdir)
	if err != nil {
		return nil, err
	}
	env := map[string]string{}
	for k, v := range model.Env {
		env[k] = v
	}
	resultFile := filepath.Join(modelDir, "model_eval_result.json")
	env["MODEL_EVAL_TASK_PACKET"] = taskPacketPath
	env["MODEL_EVAL_ARTIFACT_DIR"] = modelDir
	env["MODEL_EVAL_WORKSPACE"] = workdir
	env["MODEL_EVAL_MODEL_ID"] = model.ID
	env["MODEL_EVAL_TASK_ID"] = cfg.Task.ID
	env["MODEL_EVAL_RESULT_FILE"] = resultFile
	setupResult := runOptionalShell(cfg.Target.SetupCmd, workdir, env, logger, "setup")
	if setupResult != nil && setupResult.Error != "" {
		return writeSetupFailure(cfg, model, modelDir, actualMode, taskMode, taskSignals, *setupResult), nil
	}
	logger.emit("task_packet_ready", map[string]any{"task_packet": taskPacketPath})
	launchResult, launchMetadata, extraFiles := executeModelLaunch(model, workdir, env, taskPacket, logger)
	after, err := workspace.SnapshotWorkspace(workdir)
	if err != nil {
		return nil, err
	}
	changes, patchText := workspace.BuildPatchAndChanges(before, after)
	flags := workspace.DetectConstraintFlags(changes, cfg.Task.AllowedPaths)
	if launchResult.ExitCode != nil && *launchResult.ExitCode != 0 {
		flags = append(flags, "agent_nonzero_exit")
	}
	analysisText := firstNonEmptyString(launchMetadata["analysis_text"], claudestream.ExtractAnalysisTextFromStdout(launchResult.Stdout))
	analysisText = claudestream.TrimText(analysisText, claudestream.AnalysisExcerptLimit)
	hasAnalysis := strings.TrimSpace(analysisText) != ""
	flags = append(flags, detectAnalysisFlags(hasAnalysis)...)
	if taskMode == "readonly_review" {
		flags = append(flags, "readonly_task")
	}
	testStatus, verificationPayload := buildTests(cfg.Target.TestCmd, workdir, env, logger, launchResult, launchMetadata, changes)
	setupPayload := buildSetupPayload(cfg.Target.SetupCmd, setupResult)
	testsPayload := map[string]any{"setup": setupPayload, "verification": verificationPayload, "status": verificationPayload["status"]}
	extraPayload := readOptionalResultFile(resultFile)
	usage := mergeUsage(launchMetadata["usage"], nil, nil)
	durationAPISeconds := durationSeconds(launchMetadata["duration_api_ms"])
	elapsedSeconds := chooseElapsed(launchResult.DurationSeconds, launchMetadata["duration_ms"])
	processSummary := buildProcessSummary(taskMode, modelDir, collectFilePaths(changes))
	_ = os.WriteFile(filepath.Join(modelDir, "analysis_excerpt.txt"), []byte(analysisText), 0o644)
	status := report.PickStatus(taskMode, map[string]any{"timed_out": launchResult.TimedOut, "error": launchResult.Error}, false, testStatus, changes, launchMetadata["result_subtype"], hasAnalysis)
	result := map[string]any{
		"model_id":             model.ID,
		"label":                model.Label,
		"task_mode":            taskMode,
		"task_mode_signals":    taskSignals,
		"status":               status,
		"correctness_state":    report.BuildCorrectnessState(status, testStatus, len(changes) > 0, hasAnalysis),
		"elapsed_seconds":      elapsedSeconds,
		"duration_api_seconds": durationAPISeconds,
		"cost_usd":             coalesceFloat(launchMetadata["total_cost_usd"], extraPayload["cost_usd"]),
		"files_touched":        collectFilePaths(changes),
		"diff_stats":           workspace.SummarizeDiff(changes),
		"tests":                map[string]any{"status": testStatus},
		"constraint_flags":     uniqueStrings(flags),
		"failure_labels":       report.BuildFailureLabels(status, testStatus, uniqueStrings(flags), changes),
		"score":                map[string]any{},
		"artifacts":            relativeArtifactPaths(cfg.Execution.ArtifactsDir, modelDir),
		"workspace_mode":       actualMode,
		"budget_usd":           model.BudgetUSD,
		"notes":                extraPayload["notes"],
		"session_id":           launchMetadata["session_id"],
		"num_turns":            launchMetadata["num_turns"],
		"result_subtype":       launchMetadata["result_subtype"],
		"analysis_text":        analysisText,
		"analysis_excerpt":     analysisText,
		"process_summary":      processSummary,
		"core_findings_count":  estimateCoreFindings(analysisText),
		"usage":                usage,
		"configured_base_url":  env["ANTHROPIC_BASE_URL"],
		"configured_model":     launchMetadata["configured_model"],
		"auth_env_source":      authEnvSource(env),
		"launch":               map[string]any{"launcher_type": launchMetadata["launcher_type"], "command": launchResult.Command, "args": launchResult.Args, "exit_code": launchResult.ExitCode, "timed_out": launchResult.TimedOut, "shell": launchResult.Shell},
	}
	metrics := map[string]any{"model_id": model.ID, "label": model.Label, "workspace_mode_requested": cfg.Execution.WorkspaceMode, "workspace_mode_used": actualMode, "task_mode": taskMode, "elapsed_seconds": elapsedSeconds, "duration_api_seconds": durationAPISeconds, "cost_usd": result["cost_usd"], "shell": launchResult.Shell, "launch_exit_code": launchResult.ExitCode, "launch_timed_out": launchResult.TimedOut, "budget_usd": model.BudgetUSD, "diff_stats": result["diff_stats"], "launcher_type": launchMetadata["launcher_type"], "duration_ms": launchMetadata["duration_ms"], "duration_api_ms": launchMetadata["duration_api_ms"], "total_cost_usd": launchMetadata["total_cost_usd"], "session_id": launchMetadata["session_id"], "num_turns": launchMetadata["num_turns"], "result_subtype": launchMetadata["result_subtype"], "usage": usage, "configured_base_url": env["ANTHROPIC_BASE_URL"], "configured_model": launchMetadata["configured_model"], "auth_env_source": authEnvSource(env)}
	if err := writeModelArtifacts(modelDir, &launchResult, changes, patchText, metrics, testsPayload, result, extraFiles); err != nil {
		return nil, err
	}
	logger.emit("run_finished", map[string]any{"status": status})
	return result, nil
}

func executeModelLaunch(model spec.ModelSpec, workdir string, env map[string]string, taskPacket string, logger *eventLogger) (execx.Result, map[string]any, map[string]string) {
	if model.Launcher != nil {
		argv := buildClaudeCLIArgv(model, taskPacket)
		result := execx.RunProcess(argv, workdir, env, time.Duration(model.TimeoutMinutes*float64(time.Minute)))
		parsed := claudestream.Parse(result.Stdout)
		return result, map[string]any{"launcher_type": "claude-cli", "session_id": parsed.SessionID, "num_turns": parsed.NumTurns, "result_subtype": parsed.ResultSubtype, "duration_ms": parsed.DurationMS, "duration_api_ms": parsed.DurationAPIMS, "total_cost_usd": parsed.TotalCostUSD, "usage": parsed.Usage, "analysis_text": parsed.ResultText, "configured_model": model.Launcher.Model}, map[string]string{"claude_stream.jsonl": result.Stdout}
	}
	result := execx.RunShell(*model.LaunchCmd, workdir, env, time.Duration(model.TimeoutMinutes*float64(time.Minute)))
	return result, map[string]any{"launcher_type": "launch-cmd", "session_id": nil, "num_turns": nil, "result_subtype": nil, "duration_ms": nil, "duration_api_ms": nil, "total_cost_usd": nil, "usage": map[string]any{"input_tokens": nil, "output_tokens": nil, "cache_read_input_tokens": nil, "cache_creation_input_tokens": nil}, "analysis_text": "", "configured_model": nil}, map[string]string{}
}

func buildClaudeCLIArgv(model spec.ModelSpec, taskPacket string) []string {
	argv := []string{"claude", "-p", taskPacket, "--verbose", "--output-format", "stream-json", "--input-format", "text", "--model", model.Launcher.Model, "--permission-mode", "bypassPermissions", "--setting-sources", "local", "--no-session-persistence", "--max-turns", fmt.Sprintf("%d", model.Launcher.MaxTurns)}
	if model.BudgetUSD != nil {
		argv = append(argv, "--max-budget-usd", fmt.Sprintf("%v", *model.BudgetUSD))
	}
	argv = append(argv, model.Launcher.ExtraArgs...)
	return argv
}

func makeTaskPacket(cfg *spec.BenchmarkSpec, model spec.ModelSpec) string {
	allowed := "(none)"
	if len(cfg.Task.AllowedPaths) > 0 {
		allowed = strings.Join(cfg.Task.AllowedPaths, "\n- ")
		allowed = "- " + allowed
	}
	return strings.Join([]string{
		fmt.Sprintf("# Task %s", cfg.Task.ID),
		"",
		"## Prompt",
		cfg.Task.Prompt,
		"",
		"## Acceptance Notes",
		firstNonEmptyString(cfg.Task.AcceptanceNotes, "(none)"),
		"",
		"## Allowed Paths",
		allowed,
		"",
		"## Expected Outputs",
		"- Apply the requested change or complete the requested review.",
		"- Leave artifacts in the workspace and return a final answer through the launcher.",
		"",
		"## Artifact Contract",
		fmt.Sprintf("- Model ID: %s", model.ID),
		"- Read MODEL_EVAL_* environment variables for artifact paths.",
	}, "\n") + "\n"
}

func buildSummary(cfg *spec.BenchmarkSpec, results []map[string]any) map[string]any {
	taskMode, taskSignals := report.DetectTaskMode(cfg.Task.Prompt, cfg.Task.AcceptanceNotes)
	report.ApplyScores(results, taskMode)
	weights := report.ScoreWeights
	if taskMode == "readonly_review" {
		weights = report.ReadonlyScoreWeights
	}
	return report.BuildSummary(map[string]any{"task_id": cfg.Task.ID, "allowed_paths": cfg.Task.AllowedPaths, "repo_path": cfg.Target.RepoPath, "test_cmd": cfg.Target.TestCmd, "artifacts_dir": cfg.Execution.ArtifactsDir, "workspace_mode": cfg.Execution.WorkspaceMode, "max_parallel": cfg.Execution.MaxParallel, "rubric_profile": cfg.Rubric.Profile}, results, taskMode, taskSignals, weights)
}

func PrintSummary(summary map[string]any) {
	fmt.Printf("Task: %v\n", summary["task"].(map[string]any)["id"])
	for _, item := range summary["results"].([]map[string]any) {
		totalTokens := "-"
		if usage, ok := item["usage"].(map[string]any); ok && usage["total_tokens"] != nil {
			totalTokens = fmt.Sprintf("%v", usage["total_tokens"])
		}
		tests := item["tests"].(map[string]any)
		score := item["score"].(map[string]float64)
		fmt.Printf("%s: status=%s, tests=%v, elapsed=%.2fs, tokens=%s, total=%.1f\n", item["model_id"], item["status"], tests["status"], asFloat(item["elapsed_seconds"]), totalTokens, score["total"])
	}
}

func buildTests(testCmd *string, workdir string, env map[string]string, logger *eventLogger, launchResult execx.Result, launchMetadata map[string]any, changes []map[string]any) (string, map[string]any) {
	if testCmd == nil {
		return "not_configured", map[string]any{"status": "not_configured", "command": nil, "exit_code": nil, "duration_seconds": 0.0, "stdout_tail": "", "stderr_tail": ""}
	}
	if launchResult.TimedOut || launchMetadata["result_subtype"] == "error_max_turns" || len(changes) == 0 {
		return "skipped", map[string]any{"status": "skipped", "command": *testCmd, "exit_code": nil, "duration_seconds": 0.0, "stdout_tail": "", "stderr_tail": ""}
	}
	logger.emit("tests_started", map[string]any{"command": *testCmd})
	result := execx.RunShell(*testCmd, workdir, env, 0)
	status := "failed"
	if result.ExitCode != nil && *result.ExitCode == 0 && !result.TimedOut {
		status = "passed"
	}
	logger.emit("tests_finished", map[string]any{"command": *testCmd, "exit_code": result.ExitCode, "timed_out": result.TimedOut, "duration_seconds": result.DurationSeconds})
	return status, map[string]any{"status": status, "command": *testCmd, "exit_code": result.ExitCode, "duration_seconds": result.DurationSeconds, "stdout_tail": tailText(result.Stdout, 4000), "stderr_tail": tailText(coalesceString(result.Stderr, result.Error), 4000)}
}

func runOptionalShell(command *string, workdir string, env map[string]string, logger *eventLogger, eventName string) *execx.Result {
	if command == nil {
		return nil
	}
	logger.emit(eventName+"_started", map[string]any{"command": *command})
	result := execx.RunShell(*command, workdir, env, 0)
	logger.emit(eventName+"_finished", map[string]any{"command": *command, "exit_code": result.ExitCode, "timed_out": result.TimedOut, "duration_seconds": result.DurationSeconds})
	return &result
}

func buildSetupPayload(setupCmd *string, result *execx.Result) map[string]any {
	if setupCmd == nil || result == nil {
		return map[string]any{"status": "not_configured", "command": setupCmd, "exit_code": nil, "duration_seconds": 0.0, "stdout_tail": "", "stderr_tail": ""}
	}
	status := "passed"
	if result.ExitCode == nil || *result.ExitCode != 0 || result.Error != "" {
		status = "failed"
	}
	return map[string]any{"status": status, "command": *setupCmd, "exit_code": result.ExitCode, "duration_seconds": result.DurationSeconds, "stdout_tail": tailText(result.Stdout, 4000), "stderr_tail": tailText(coalesceString(result.Stderr, result.Error), 4000)}
}

func writeSetupFailure(cfg *spec.BenchmarkSpec, model spec.ModelSpec, modelDir, actualMode, taskMode string, taskSignals []string, setupResult execx.Result) map[string]any {
	testsPayload := map[string]any{"setup": buildSetupPayload(cfg.Target.SetupCmd, &setupResult), "verification": map[string]any{"status": "skipped", "command": cfg.Target.TestCmd, "exit_code": nil, "duration_seconds": 0.0, "stdout_tail": "", "stderr_tail": ""}, "status": "skipped"}
	result := map[string]any{"model_id": model.ID, "label": model.Label, "task_mode": taskMode, "task_mode_signals": taskSignals, "status": "runner_error", "correctness_state": "unverified", "elapsed_seconds": 0.0, "cost_usd": nil, "files_touched": []string{}, "diff_stats": workspace.SummarizeDiff(nil), "tests": map[string]any{"status": "skipped"}, "constraint_flags": []string{"setup_failed", "tests_not_run", "no_diff"}, "failure_labels": []string{}, "score": map[string]any{}, "artifacts": relativeArtifactPaths(cfg.Execution.ArtifactsDir, modelDir), "workspace_mode": actualMode, "budget_usd": model.BudgetUSD, "notes": nil, "session_id": nil, "num_turns": nil, "result_subtype": nil, "analysis_text": "", "analysis_excerpt": "", "process_summary": "", "core_findings_count": nil, "usage": map[string]any{"input_tokens": nil, "output_tokens": nil, "cache_read_input_tokens": nil, "cache_creation_input_tokens": nil, "total_tokens": nil}, "duration_api_seconds": nil, "configured_base_url": model.Env["ANTHROPIC_BASE_URL"], "configured_model": nil, "auth_env_source": authEnvSource(model.Env), "launch": map[string]any{"launcher_type": launcherType(model), "command": firstNonEmptyString(ptrValue(model.LaunchCmd), "claude"), "args": nil, "exit_code": nil, "timed_out": false, "shell": nil}}
	metrics := map[string]any{"model_id": model.ID, "label": model.Label, "workspace_mode_requested": cfg.Execution.WorkspaceMode, "workspace_mode_used": actualMode, "task_mode": taskMode, "elapsed_seconds": 0.0, "cost_usd": nil, "shell": nil, "launch_exit_code": nil, "launch_timed_out": false, "launcher_type": launcherType(model), "duration_ms": nil, "duration_api_ms": nil, "total_cost_usd": nil, "session_id": nil, "num_turns": nil, "result_subtype": nil, "usage": map[string]any{"input_tokens": nil, "output_tokens": nil, "cache_read_input_tokens": nil, "cache_creation_input_tokens": nil, "total_tokens": nil}, "configured_base_url": model.Env["ANTHROPIC_BASE_URL"], "configured_model": nil, "auth_env_source": authEnvSource(model.Env)}
	_ = writeModelArtifacts(modelDir, nil, nil, "", metrics, testsPayload, result, nil)
	return result
}

func writeModelArtifacts(modelDir string, launchResult *execx.Result, changes []map[string]any, patchText string, metrics, testsPayload, result map[string]any, extraFiles map[string]string) error {
	stdout, stderr := "", ""
	if launchResult != nil {
		stdout, stderr = launchResult.Stdout, launchResult.Stderr
	}
	if err := os.WriteFile(filepath.Join(modelDir, "stdout.log"), []byte(stdout), 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(modelDir, "stderr.log"), []byte(stderr), 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(modelDir, "patch.diff"), []byte(patchText), 0o644); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(modelDir, "files.json"), map[string]any{"files": changes, "summary": workspace.SummarizeDiff(changes)}); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(modelDir, "metrics.json"), metrics); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(modelDir, "tests.json"), testsPayload); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(modelDir, "status.json"), result); err != nil {
		return err
	}
	for name, text := range extraFiles {
		if err := os.WriteFile(filepath.Join(modelDir, name), []byte(text), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func loadExistingSpec(artifactsDir string) (*spec.BenchmarkSpec, error) {
	path := filepath.Join(artifactsDir, "run_spec.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, &spec.SpecError{Message: fmt.Sprintf("Missing run_spec.json in %s", artifactsDir)}
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	cfg := &spec.BenchmarkSpec{}
	cfg.Task.ID, _ = payload["task"].(map[string]any)["id"].(string)
	cfg.Task.Prompt, _ = payload["task"].(map[string]any)["prompt"].(string)
	cfg.Task.AcceptanceNotes, _ = payload["task"].(map[string]any)["acceptance_notes"].(string)
	cfg.Task.AllowedPaths = toStringSlice(payload["task"].(map[string]any)["allowed_paths"])
	cfg.Target.RepoPath, _ = payload["target"].(map[string]any)["repo_path"].(string)
	cfg.Target.SetupCmd = toStringPtr(payload["target"].(map[string]any)["setup_cmd"])
	cfg.Target.TestCmd = toStringPtr(payload["target"].(map[string]any)["test_cmd"])
	cfg.Execution.ArtifactsDir = artifactsDir
	cfg.Execution.MaxParallel = int(asFloat(payload["execution"].(map[string]any)["max_parallel"]))
	cfg.Execution.WorkspaceMode, _ = payload["execution"].(map[string]any)["workspace_mode"].(string)
	cfg.Rubric.Profile, _ = payload["rubric"].(map[string]any)["profile"].(string)
	for _, item := range payload["models"].([]any) {
		data := item.(map[string]any)
		model := spec.ModelSpec{ID: data["id"].(string), Label: coalesceString(asString(data["label"]), data["id"].(string)), Env: map[string]string{}, TimeoutMinutes: asFloat(data["timeout_minutes"])}
		if launchCmd, ok := data["launch_cmd"].(string); ok && launchCmd != "" {
			model.LaunchCmd = &launchCmd
		}
		if launcher, ok := data["launcher"].(map[string]any); ok {
			model.Launcher = &spec.LauncherSpec{Type: asString(launcher["type"]), Model: asString(launcher["model"]), MaxTurns: int(asFloat(launcher["max_turns"])), ExtraArgs: toStringSlice(launcher["extra_args"])}
		}
		if env, ok := data["env"].(map[string]any); ok {
			for k, v := range env {
				model.Env[k] = fmt.Sprintf("%v", v)
			}
		}
		cfg.Models = append(cfg.Models, model)
	}
	return cfg, nil
}

func buildProcessSummary(taskMode, modelDir string, filesTouched []string) string {
	stream := SumarizeRecoveryStream(modelDir)
	parts := []string{}
	if len(filesTouched) > 0 {
		parts = append(parts, fmt.Sprintf("修改了 %s", strings.Join(filesTouched[:min(4, len(filesTouched))], ", ")))
	}
	analysis := strings.ToLower(asString(stream["result_text"]))
	if strings.Contains(analysis, "critical") || strings.Contains(analysis, "严重") {
		parts = append(parts, "倾向先抓高风险问题")
	}
	if taskMode == "readonly_review" {
		if len(filesTouched) == 0 {
			parts = append(parts, "严格遵守了只读约束，没有修改工作区文件")
		} else {
			parts = append(parts, "任务要求只读，但仍产生了工作区文件输出")
		}
	}
	if len(parts) == 0 {
		parts = append(parts, "保留了基本执行痕迹，但过程信号有限")
	}
	return strings.Join(parts, "；")
}

func relativeArtifactPaths(artifactsDir, modelDir string) map[string]any {
	rel, _ := filepath.Rel(artifactsDir, modelDir)
	prefix := normalizeRelPath(rel)
	return map[string]any{"artifact_dir": prefix, "status_json": filepath.ToSlash(filepath.Join(prefix, "status.json")), "tests_json": filepath.ToSlash(filepath.Join(prefix, "tests.json")), "patch_diff": filepath.ToSlash(filepath.Join(prefix, "patch.diff")), "metrics_json": filepath.ToSlash(filepath.Join(prefix, "metrics.json")), "files_json": filepath.ToSlash(filepath.Join(prefix, "files.json")), "stdout_log": filepath.ToSlash(filepath.Join(prefix, "stdout.log")), "stderr_log": filepath.ToSlash(filepath.Join(prefix, "stderr.log")), "analysis_excerpt": filepath.ToSlash(filepath.Join(prefix, "analysis_excerpt.txt")), "events_jsonl": filepath.ToSlash(filepath.Join(prefix, "events.jsonl")), "claude_stream_jsonl": filepath.ToSlash(filepath.Join(prefix, "claude_stream.jsonl"))}
}

func writeJSON(path string, payload any) error {
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
func readJSONMap(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	return payload, nil
}
func readOptionalResultFile(path string) map[string]any {
	payload, err := readJSONMap(path)
	if err != nil {
		return map[string]any{}
	}
	return payload
}
func authEnvSource(env map[string]string) string {
	if strings.TrimSpace(env["ANTHROPIC_API_KEY"]) != "" {
		return "ANTHROPIC_API_KEY"
	}
	return "existing"
}
func durationSeconds(value any) any {
	if value == nil {
		return nil
	}
	return float64(int((asFloat(value)/1000)*1000+0.5)) / 1000
}
func chooseElapsed(fallback float64, durationMS any) float64 {
	if durationMS == nil {
		return fallback
	}
	return float64(int((asFloat(durationMS)/1000)*1000+0.5)) / 1000
}
func detectAnalysisFlags(hasAnalysis bool) []string {
	if hasAnalysis {
		return nil
	}
	return []string{"no_output"}
}
func collectFilePaths(changes []map[string]any) []string {
	out := []string{}
	for _, change := range changes {
		if path, ok := change["path"].(string); ok {
			out = append(out, path)
		}
	}
	return out
}
func estimateCoreFindings(text string) any {
	count := 0
	for _, line := range strings.Split(text, "\n") {
		trimmed := strings.TrimSpace(strings.ToLower(line))
		if strings.HasPrefix(trimmed, "#") || strings.HasPrefix(trimmed, "1.") || strings.Contains(trimmed, "finding") || strings.Contains(trimmed, "漏洞") || strings.Contains(trimmed, "风险") {
			count++
		}
	}
	if count == 0 {
		return nil
	}
	if count > 50 {
		count = 50
	}
	return count
}
func mergeUsage(values ...any) map[string]any {
	usage := map[string]any{"input_tokens": nil, "output_tokens": nil, "cache_read_input_tokens": nil, "cache_creation_input_tokens": nil, "total_tokens": nil}
	for _, value := range values {
		if entry, ok := value.(map[string]any); ok {
			for key := range usage {
				if entry[key] != nil {
					usage[key] = entry[key]
				}
			}
		}
	}
	total := 0
	seen := false
	for _, key := range []string{"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"} {
		if usage[key] != nil {
			total += int(asFloat(usage[key]))
			seen = true
		}
	}
	if seen {
		usage["total_tokens"] = total
	}
	return usage
}
func nestedStatus(statusPayload, testsPayload map[string]any) string {
	if tests, ok := statusPayload["tests"].(map[string]any); ok {
		if status, ok := tests["status"].(string); ok && status != "" {
			return status
		}
	}
	if status, ok := testsPayload["status"].(string); ok && status != "" {
		return status
	}
	return "not_configured"
}
func getFileList(filesPayload map[string]any) []map[string]any {
	raw, ok := filesPayload["files"].([]any)
	if !ok {
		return nil
	}
	out := []map[string]any{}
	for _, item := range raw {
		if entry, ok := item.(map[string]any); ok {
			out = append(out, entry)
		}
	}
	return out
}
func tailText(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	return text[len(text)-limit:]
}
func toStringSlice(value any) []string {
	switch v := value.(type) {
	case []string:
		return v
	case []any:
		out := []string{}
		for _, item := range v {
			out = append(out, fmt.Sprintf("%v", item))
		}
		return out
	default:
		return nil
	}
}
func toStringPtr(value any) *string {
	if value == nil {
		return nil
	}
	s := fmt.Sprintf("%v", value)
	if s == "" || s == "<nil>" {
		return nil
	}
	return &s
}
func ptrValue(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
func asFloat(value any) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case int:
		return float64(v)
	case int64:
		return float64(v)
	case json.Number:
		f, _ := v.Float64()
		return f
	default:
		return 0
	}
}
func asString(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprintf("%v", value)
}
func firstNonEmptyString(values ...any) string {
	for _, value := range values {
		if s := strings.TrimSpace(asString(value)); s != "" && s != "<nil>" {
			return s
		}
	}
	return ""
}
func firstNonNil(values ...any) any {
	for _, value := range values {
		if value != nil {
			return value
		}
	}
	return nil
}
func coalesceString(a, b string) string {
	if strings.TrimSpace(a) != "" {
		return a
	}
	return b
}
func coalesceFloat(values ...any) any {
	for _, value := range values {
		if value == nil {
			continue
		}
		if f := asFloat(value); f != 0 {
			return f
		}
	}
	return nil
}
func uniqueStrings(values []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, value := range values {
		if value != "" && !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	sort.Strings(out)
	return out
}
func normalizeRelPath(path string) string {
	return strings.TrimLeft(strings.ReplaceAll(filepath.ToSlash(path), "\\", "/"), "./")
}
func launcherType(model spec.ModelSpec) string {
	if model.Launcher != nil {
		return "claude-cli"
	}
	return "launch-cmd"
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
