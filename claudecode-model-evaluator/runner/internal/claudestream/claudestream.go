package claudestream

import (
	"encoding/json"
	"strings"
)

const AnalysisExcerptLimit = 4000

type Parsed struct {
	SessionID      any              `json:"session_id"`
	NumTurns       any              `json:"num_turns"`
	ResultSubtype  any              `json:"result_subtype"`
	ResultText     string           `json:"result_text"`
	DurationMS     any              `json:"duration_ms"`
	DurationAPIMS  any              `json:"duration_api_ms"`
	TotalCostUSD   any              `json:"total_cost_usd"`
	PermissionMode any              `json:"permission_mode"`
	Tools          any              `json:"tools"`
	AssistantTexts []string         `json:"assistant_texts"`
	ToolUses       []map[string]any `json:"tool_uses"`
	Usage          map[string]any   `json:"usage"`
}

func Parse(stdout string) Parsed {
	parsed := Parsed{
		Usage: map[string]any{
			"input_tokens":                nil,
			"output_tokens":               nil,
			"cache_read_input_tokens":     nil,
			"cache_creation_input_tokens": nil,
		},
	}
	for _, rawLine := range strings.Split(stdout, "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" {
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal([]byte(line), &payload); err != nil {
			continue
		}
		eventType, _ := payload["type"].(string)
		subtype, _ := payload["subtype"].(string)
		switch eventType {
		case "system":
			if subtype == "init" {
				parsed.SessionID = coalesce(payload["session_id"], parsed.SessionID)
				parsed.PermissionMode = coalesce(payload["permissionMode"], payload["permission_mode"], parsed.PermissionMode)
				parsed.Tools = coalesce(payload["tools"], parsed.Tools)
			}
		case "assistant":
			assistantText := ExtractAssistantText(payload)
			if strings.TrimSpace(assistantText) != "" {
				parsed.AssistantTexts = append(parsed.AssistantTexts, TrimText(assistantText, 3000))
			}
			if message, ok := payload["message"].(map[string]any); ok {
				if content, ok := message["content"].([]any); ok {
					for _, item := range content {
						entry, ok := item.(map[string]any)
						if !ok {
							continue
						}
						if entry["type"] == "tool_use" {
							name, _ := entry["name"].(string)
							parsed.ToolUses = append(parsed.ToolUses, map[string]any{"name": name, "input": entry["input"]})
						}
					}
				}
			}
		case "result":
			parsed.ResultSubtype = subtype
			parsed.SessionID = coalesce(payload["session_id"], parsed.SessionID)
			parsed.NumTurns = coalesce(payload["num_turns"], parsed.NumTurns)
			parsed.DurationMS = coalesce(payload["duration_ms"], parsed.DurationMS)
			parsed.DurationAPIMS = coalesce(payload["duration_api_ms"], parsed.DurationAPIMS)
			parsed.TotalCostUSD = coalesce(payload["total_cost_usd"], parsed.TotalCostUSD)
			if text, ok := payload["result"].(string); ok {
				parsed.ResultText = strings.TrimSpace(text)
			}
			parsed.Usage = summarizeUsage(payload)
		}
	}
	if strings.TrimSpace(parsed.ResultText) == "" {
		parsed.ResultText = strings.TrimSpace(strings.Join(parsed.AssistantTexts, "\n\n"))
	}
	return parsed
}

func ExtractAssistantText(payload map[string]any) string {
	if message, ok := payload["message"].(string); ok {
		return message
	}
	if message, ok := payload["message"].(map[string]any); ok {
		if content, ok := message["content"].([]any); ok {
			parts := make([]string, 0)
			for _, item := range content {
				entry, ok := item.(map[string]any)
				if !ok {
					continue
				}
				if text, ok := entry["text"].(string); ok {
					parts = append(parts, text)
				}
			}
			if len(parts) > 0 {
				return strings.Join(parts, "\n")
			}
		}
	}
	if content, ok := payload["content"].([]any); ok {
		parts := make([]string, 0)
		for _, item := range content {
			entry, ok := item.(map[string]any)
			if !ok {
				continue
			}
			if text, ok := entry["text"].(string); ok {
				parts = append(parts, text)
			}
		}
		if len(parts) > 0 {
			return strings.Join(parts, "\n")
		}
	}
	if text, ok := payload["text"].(string); ok {
		return text
	}
	return ""
}

func ExtractAnalysisTextFromStdout(stdout string) string {
	best := ""
	for _, rawLine := range strings.Split(stdout, "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" {
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal([]byte(line), &payload); err != nil {
			continue
		}
		if payload["type"] == "result" {
			if text, ok := payload["result"].(string); ok {
				text = strings.TrimSpace(text)
				if LooksLikeAnalysisText(text) {
					return text
				}
				if len(text) > len(best) {
					best = text
				}
			}
		}
		if payload["type"] == "assistant" {
			text := strings.TrimSpace(ExtractAssistantText(payload))
			if len(text) > len(best) {
				best = text
			}
		}
	}
	if LooksLikeAnalysisText(best) {
		return best
	}
	return strings.TrimSpace(best)
}

func LooksLikeAnalysisText(text string) bool {
	stripped := strings.TrimSpace(text)
	if len([]rune(stripped)) < 280 {
		return false
	}
	keywords := []string{"vulnerability", "risk", "impact", "exploit", "recommend", "security", "review", "finding", "issue", "漏洞", "风险", "修复", "影响", "成因", "利用", "审查", "审计", "建议"}
	lowered := strings.ToLower(stripped)
	hits := 0
	for _, keyword := range keywords {
		if strings.Contains(lowered, keyword) {
			hits++
		}
	}
	lines := 0
	for _, line := range strings.Split(stripped, "\n") {
		if strings.TrimSpace(line) != "" {
			lines++
		}
	}
	return hits >= 3 || lines >= 8
}

func TrimText(text string, limit int) string {
	text = strings.TrimSpace(text)
	if len([]rune(text)) <= limit {
		return text
	}
	runes := []rune(text)
	return strings.TrimSpace(string(runes[:limit-3])) + "..."
}

func summarizeUsage(payload map[string]any) map[string]any {
	usage := map[string]any{
		"input_tokens":                nil,
		"output_tokens":               nil,
		"cache_read_input_tokens":     nil,
		"cache_creation_input_tokens": nil,
	}
	var source map[string]any
	if raw, ok := payload["usage"].(map[string]any); ok {
		source = raw
	}
	if raw, ok := payload["modelUsage"].(map[string]any); ok && len(raw) > 0 {
		for _, value := range raw {
			if entry, ok := value.(map[string]any); ok {
				source = entry
				break
			}
		}
	}
	usage["input_tokens"] = readNumber(source, "inputTokens", "input_tokens")
	usage["output_tokens"] = readNumber(source, "outputTokens", "output_tokens")
	usage["cache_read_input_tokens"] = readNumber(source, "cacheReadInputTokens", "cache_read_input_tokens")
	usage["cache_creation_input_tokens"] = readNumber(source, "cacheCreationInputTokens", "cache_creation_input_tokens")
	return usage
}

func readNumber(source map[string]any, keys ...string) any {
	if source == nil {
		return nil
	}
	for _, key := range keys {
		if value, ok := source[key]; ok {
			switch num := value.(type) {
			case float64:
				return int(num)
			case int:
				return num
			}
		}
	}
	return nil
}

func coalesce(values ...any) any {
	for _, value := range values {
		if value != nil {
			return value
		}
	}
	return nil
}
