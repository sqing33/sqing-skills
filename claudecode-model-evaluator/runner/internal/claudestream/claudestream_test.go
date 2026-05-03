package claudestream

import "testing"

func TestParseStreamJSON(t *testing.T) {
	stdout := "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"abc\",\"permissionMode\":\"bypassPermissions\"}\n{\"type\":\"assistant\",\"message\":{\"content\":[{\"text\":\"checking files\"}]}}\n{\"type\":\"result\",\"subtype\":\"success\",\"session_id\":\"abc\",\"num_turns\":3,\"duration_ms\":1200,\"usage\":{\"inputTokens\":10,\"outputTokens\":5},\"result\":\"done\"}"
	parsed := Parse(stdout)
	if parsed.ResultText != "done" { t.Fatalf("unexpected result text: %q", parsed.ResultText) }
	if parsed.SessionID != "abc" { t.Fatalf("unexpected session id: %#v", parsed.SessionID) }
	if parsed.Usage["input_tokens"] != 10 { t.Fatalf("unexpected usage: %#v", parsed.Usage) }
}
