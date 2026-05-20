package sanitize

import (
	"strings"
	"testing"
)

func TestTextRedactsCommonSecretShapes(t *testing.T) {
	input := `ANTHROPIC_API_KEY=sk-testsecret1234567890\nAuthorization: Bearer secretbearer1234567890`
	got := Text(input)
	if strings.Contains(got, "sk-testsecret") || strings.Contains(got, "secretbearer") {
		t.Fatalf("secret was not redacted: %s", got)
	}
	if !strings.Contains(got, "ANTHROPIC_API_KEY=[REDACTED]") {
		t.Fatalf("missing env redaction marker: %s", got)
	}
	if !strings.Contains(got, "Authorization: Bearer [REDACTED]") {
		t.Fatalf("missing bearer redaction marker: %s", got)
	}
}

func TestValueRedactsSensitiveKeys(t *testing.T) {
	got := Value(map[string]any{
		"env": map[string]string{
			"ANTHROPIC_API_KEY": "sk-testsecret1234567890",
			"NORMAL_VALUE":      "hello",
		},
		"launch_cmd": "printf sk-inline1234567890",
	}).(map[string]any)
	env := got["env"].(map[string]string)
	if env["ANTHROPIC_API_KEY"] != "[REDACTED]" {
		t.Fatalf("api key env was not redacted: %#v", env)
	}
	if env["NORMAL_VALUE"] != "hello" {
		t.Fatalf("normal env was unexpectedly changed: %#v", env)
	}
	if strings.Contains(got["launch_cmd"].(string), "sk-inline") {
		t.Fatalf("inline key was not redacted: %s", got["launch_cmd"])
	}
}
