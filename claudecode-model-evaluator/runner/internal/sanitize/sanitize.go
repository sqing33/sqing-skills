package sanitize

import (
	"fmt"
	"regexp"
	"strings"
)

const redacted = "[REDACTED]"

var secretPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(ANTHROPIC_API_KEY\s*=\s*)[^\s\\;"']+`),
	regexp.MustCompile(`(?i)(OPENAI_API_KEY\s*=\s*)[^\s\\;"']+`),
	regexp.MustCompile(`(?i)(api[_-]?key["']?\s*[:=]\s*["']?)[A-Za-z0-9._\-]{12,}`),
	regexp.MustCompile(`(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-]+`),
	regexp.MustCompile(`(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+`),
	regexp.MustCompile(`\bsk-[A-Za-z0-9_\-]{12,}\b`),
}

func Text(value string) string {
	out := value
	for _, pattern := range secretPatterns {
		out = pattern.ReplaceAllString(out, "${1}"+redacted)
	}
	return out
}

func Value(value any) any {
	return valueWithKey("", value)
}

func valueWithKey(key string, value any) any {
	if isSensitiveKey(key) {
		if value == nil {
			return nil
		}
		return redacted
	}
	switch v := value.(type) {
	case string:
		return Text(v)
	case map[string]any:
		out := make(map[string]any, len(v))
		for k, item := range v {
			out[k] = valueWithKey(k, item)
		}
		return out
	case map[string]string:
		out := make(map[string]string, len(v))
		for k, item := range v {
			if isSensitiveKey(k) {
				out[k] = redacted
			} else {
				out[k] = Text(item)
			}
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = valueWithKey("", item)
		}
		return out
	case []string:
		out := make([]string, len(v))
		for i, item := range v {
			out[i] = Text(item)
		}
		return out
	case []map[string]any:
		out := make([]map[string]any, len(v))
		for i, item := range v {
			redactedMap := valueWithKey("", item)
			if m, ok := redactedMap.(map[string]any); ok {
				out[i] = m
			}
		}
		return out
	default:
		return value
	}
}

func isSensitiveKey(key string) bool {
	normalized := strings.ToLower(strings.ReplaceAll(key, "-", "_"))
	for _, marker := range []string{"api_key", "apikey", "secret", "token", "password", "authorization", "credential"} {
		if strings.Contains(normalized, marker) {
			return true
		}
	}
	return false
}

func StringMap(value map[string]string) map[string]string {
	if value == nil {
		return nil
	}
	out := make(map[string]string, len(value))
	for key, item := range value {
		if isSensitiveKey(key) {
			out[key] = redacted
		} else {
			out[key] = Text(item)
		}
	}
	return out
}

func AnyMap(value map[string]any) map[string]any {
	if value == nil {
		return nil
	}
	redactedValue := Value(value)
	if out, ok := redactedValue.(map[string]any); ok {
		return out
	}
	return map[string]any{"value": fmt.Sprintf("%v", redactedValue)}
}
