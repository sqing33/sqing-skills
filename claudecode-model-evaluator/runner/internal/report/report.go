package report

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

var ScoreWeights = map[string]float64{"correctness": 40, "scope_control": 20, "recovery": 15, "code_quality": 15, "efficiency": 10}
var ReadonlyScoreWeights = map[string]float64{"coverage": 35, "reasoning_quality": 25, "structure": 20, "scope_control": 10, "efficiency": 10}
var StatusPriority = map[string]int{"success": 0, "test_failed": 1, "no_output": 2, "timed_out": 3, "launch_failed": 4, "runner_error": 5}

func DetectTaskMode(prompt, acceptance string) (string, []string) {
	text := strings.ToLower(prompt + "\n" + acceptance)
	patterns := []string{"readonly", "read-only", "security review", "security audit", "audit", "analysis only", "do not modify", "do not edit", "cannot modify", "can't modify", "keep existing code unchanged", "只读", "审查", "审计", "分析", "不能修改", "不要修改", "保持现有代码不变", "不允许修改", "不允许创建", "不允许删除", "只输出审查结果"}
	hits := []string{}
	for _, pattern := range patterns {
		if strings.Contains(text, pattern) {
			hits = append(hits, pattern)
		}
	}
	if len(hits) > 0 {
		return "readonly_review", hits
	}
	return "code_change", hits
}

func PickStatus(taskMode string, launch map[string]any, setupFailed bool, testStatus string, changes []map[string]any, resultSubtype any, hasAnalysis bool) string {
	if setupFailed {
		return "runner_error"
	}
	if timedOut, _ := launch["timed_out"].(bool); timedOut {
		return "timed_out"
	}
	if launchErr, _ := launch["error"].(string); launchErr != "" && len(changes) == 0 && !hasAnalysis {
		return "launch_failed"
	}
	if taskMode == "readonly_review" {
		if hasAnalysis {
			return "success"
		}
		return "no_output"
	}
	if len(changes) == 0 {
		if subtype, _ := resultSubtype.(string); subtype == "error_max_turns" {
			return "timed_out"
		}
		return "no_output"
	}
	if testStatus == "failed" {
		return "test_failed"
	}
	return "success"
}

func BuildCorrectnessState(status, testStatus string, hasDiff bool, hasAnalysis bool) string {
	if status == "success" && testStatus == "passed" {
		return "verified"
	}
	if hasAnalysis || hasDiff {
		return "partially_verified"
	}
	return "unverified"
}

func BuildFailureLabels(status, testStatus string, flags []string, changes []map[string]any) []string {
	labels := []string{}
	switch status {
	case "launch_failed":
		labels = append(labels, "launch-failed")
	case "no_output":
		labels = append(labels, "no-output")
	case "timed_out":
		labels = append(labels, "stuck-no-recovery")
	case "test_failed":
		labels = append(labels, "confident-but-wrong")
	}
	if contains(flags, "out_of_scope_changes") {
		labels = append(labels, "broad-changes")
	}
	if testStatus == "failed" && len(changes) == 0 {
		labels = append(labels, "misread-task")
	}
	return unique(labels)
}

func ApplyScores(results []map[string]any, taskMode string) {
	elapsed := make([]float64, 0, len(results))
	for _, result := range results {
		elapsed = append(elapsed, asFloat(result["elapsed_seconds"]))
	}
	for _, result := range results {
		score := map[string]float64{}
		status, _ := result["status"].(string)
		testStatus, _ := result["tests"].(map[string]any)["status"].(string)
		if taskMode == "readonly_review" {
			score["coverage"] = ternary(status == "success", 85, 20)
			score["reasoning_quality"] = ternary(result["analysis_excerpt"] != "", 82, 15)
			score["structure"] = ternary(result["analysis_excerpt"] != "", 80, 10)
			score["scope_control"] = ternary(!containsAny(result["constraint_flags"], []string{"out_of_scope_changes"}), 100, 20)
			score["efficiency"] = relativeScore(asFloat(result["elapsed_seconds"]), elapsed)
			total := weighted(score, ReadonlyScoreWeights)
			score["total"] = total
			result["score"] = score
			continue
		}
		score["correctness"] = 0
		if testStatus == "passed" {
			score["correctness"] = 100
		} else if testStatus == "not_configured" && len(asSlice(result["files_touched"])) > 0 {
			score["correctness"] = 60
		}
		score["scope_control"] = ternary(!containsAny(result["constraint_flags"], []string{"out_of_scope_changes"}), 100, 20)
		score["recovery"] = map[string]float64{"success": 100, "test_failed": 70, "no_output": 10, "timed_out": 20, "launch_failed": 0, "runner_error": 0}[status]
		score["code_quality"] = ternary(len(asSlice(result["files_touched"])) > 0, 75, 10)
		score["efficiency"] = relativeScore(asFloat(result["elapsed_seconds"]), elapsed)
		total := weighted(score, ScoreWeights)
		score["total"] = total
		result["score"] = score
	}
	sort.SliceStable(results, func(i, j int) bool {
		li := results[i]["score"].(map[string]float64)["total"]
		lj := results[j]["score"].(map[string]float64)["total"]
		if li == lj {
			return StatusPriority[results[i]["status"].(string)] < StatusPriority[results[j]["status"].(string)]
		}
		return li > lj
	})
}

func BuildSummary(cfg map[string]any, results []map[string]any, taskMode string, signals []string, weights map[string]float64) map[string]any {
	ranking := make([]string, 0, len(results))
	for _, result := range results {
		ranking = append(ranking, result["model_id"].(string))
	}
	return map[string]any{
		"task":      map[string]any{"id": cfg["task_id"], "allowed_paths": cfg["allowed_paths"], "mode": taskMode, "mode_signals": signals},
		"target":    map[string]any{"repo_path": cfg["repo_path"], "test_cmd": cfg["test_cmd"]},
		"execution": map[string]any{"artifacts_dir": cfg["artifacts_dir"], "workspace_mode": cfg["workspace_mode"], "max_parallel": cfg["max_parallel"]},
		"rubric":    map[string]any{"profile": cfg["rubric_profile"], "weights": weights},
		"results":   results,
		"ranking":   ranking,
	}
}

func BuildReport(summary map[string]any) string {
	results := summary["results"].([]map[string]any)
	lines := []string{
		"# Benchmark Report",
		"",
		"## 横向对比表",
		"| 模型 | 状态 | 测试 | 总分 | 耗时 | Tokens |",
		"| --- | --- | --- | ---: | ---: | ---: |",
	}
	for _, result := range results {
		tests := result["tests"].(map[string]any)
		totalTokens := "-"
		if usage, ok := result["usage"].(map[string]any); ok {
			if usage["total_tokens"] != nil {
				totalTokens = fmt.Sprintf("%v", usage["total_tokens"])
			}
		}
		lines = append(lines, fmt.Sprintf("| %s | %s | %s | %.1f | %.2fs | %s |", result["label"], result["status"], tests["status"], result["score"].(map[string]float64)["total"], asFloat(result["elapsed_seconds"]), totalTokens))
	}
	lines = append(lines, "", "## 模型优劣", "")
	for _, result := range results {
		lines = append(lines, fmt.Sprintf("### %s", result["label"]))
		lines = append(lines, fmt.Sprintf("- 状态：%s；测试：%v；工作区：%v", result["status"], result["tests"].(map[string]any)["status"], result["workspace_mode"]))
		if summaryText, _ := result["process_summary"].(string); summaryText != "" {
			lines = append(lines, fmt.Sprintf("- 流程摘要：%s", summaryText))
		}
		if excerpt, _ := result["analysis_excerpt"].(string); excerpt != "" {
			lines = append(lines, fmt.Sprintf("- 输出摘要：%s", excerpt))
		}
		refs := collectEvidenceRefs(result)
		if len(refs) > 0 {
			lines = append(lines, fmt.Sprintf("- 证据：%s", strings.Join(refs, "；")))
		}
		lines = append(lines, "")
	}
	return strings.Join(lines, "\n") + "\n"
}

func PersistResults(artifactsDir string, summary map[string]any) error {
	reportText := BuildReport(summary)
	if err := writeJSON(filepath.Join(artifactsDir, "summary.json"), summary); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(artifactsDir, "report.md"), []byte(reportText), 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(artifactsDir, "comparison_zh.md"), []byte(reportText), 0o644); err != nil {
		return err
	}
	for _, result := range summary["results"].([]map[string]any) {
		artifacts := result["artifacts"].(map[string]any)
		if err := writeJSON(filepath.Join(artifactsDir, artifacts["status_json"].(string)), result); err != nil {
			return err
		}
	}
	return nil
}

func writeJSON(path string, payload any) error {
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}

func collectEvidenceRefs(result map[string]any) []string {
	artifacts := result["artifacts"].(map[string]any)
	return []string{artifacts["status_json"].(string), artifacts["tests_json"].(string), artifacts["patch_diff"].(string)}
}

func relativeScore(value float64, all []float64) float64 {
	if len(all) == 0 {
		return 50
	}
	minV, maxV := all[0], all[0]
	for _, item := range all {
		if item < minV {
			minV = item
		}
		if item > maxV {
			maxV = item
		}
	}
	if maxV <= minV {
		return 100
	}
	return 100 - ((value-minV)/(maxV-minV))*60
}

func weighted(score map[string]float64, weights map[string]float64) float64 {
	total := 0.0
	for key, weight := range weights {
		total += score[key] * (weight / 100)
	}
	return float64(int(total*10+0.5)) / 10
}

func ternary(cond bool, a, b float64) float64 {
	if cond {
		return a
	}
	return b
}
func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
func unique(values []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, value := range values {
		if value != "" && !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	return out
}
func asFloat(value any) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case int:
		return float64(v)
	default:
		return 0
	}
}
func asSlice(value any) []any {
	switch v := value.(type) {
	case []any:
		return v
	case []string:
		out := make([]any, 0, len(v))
		for _, item := range v {
			out = append(out, item)
		}
		return out
	default:
		return nil
	}
}
func containsAny(value any, targets []string) bool {
	for _, item := range asSlice(value) {
		for _, target := range targets {
			if item == target {
				return true
			}
		}
	}
	return false
}
