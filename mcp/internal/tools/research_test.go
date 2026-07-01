package tools

import (
	"context"
	"errors"
	"strings"
	"testing"

	mcplib "github.com/mark3labs/mcp-go/mcp"

	"github.com/mvanhorn/last30days-skill/mcp/internal/engine"
)

func newCallToolRequest(args map[string]any) mcplib.CallToolRequest {
	var req mcplib.CallToolRequest
	req.Params.Arguments = args
	return req
}

// resultText pulls text content out of a tool result so tests can assert on
// the body Claude will see. Returns empty string when the result is nil or
// has no text content.
func resultText(res *mcplib.CallToolResult) string {
	if res == nil {
		return ""
	}
	var out strings.Builder
	for _, item := range res.Content {
		if tc, ok := item.(mcplib.TextContent); ok {
			out.WriteString(tc.Text)
		}
	}
	return out.String()
}

func TestRequireStringRejectsMissingAndBlank(t *testing.T) {
	if _, err := requireString(map[string]any{}, "topic"); err == nil {
		t.Fatal("expected error for missing topic")
	}
	if _, err := requireString(map[string]any{"topic": ""}, "topic"); err == nil {
		t.Fatal("expected error for empty topic")
	}
	if _, err := requireString(map[string]any{"topic": "   "}, "topic"); err == nil {
		t.Fatal("expected error for whitespace-only topic")
	}
	if _, err := requireString(map[string]any{"topic": 42}, "topic"); err == nil {
		t.Fatal("expected error for non-string topic")
	}
	v, err := requireString(map[string]any{"topic": "OpenAI"}, "topic")
	if err != nil || v != "OpenAI" {
		t.Fatalf("requireString ok = %q, %v", v, err)
	}
}

func TestEmitArgumentDefaultsAndValidates(t *testing.T) {
	cases := []struct {
		name    string
		args    map[string]any
		want    string
		wantErr bool
	}{
		{"missing defaults to compact", map[string]any{}, "compact", false},
		{"empty string defaults to compact", map[string]any{"emit": ""}, "compact", false},
		{"compact passes through", map[string]any{"emit": "compact"}, "compact", false},
		{"html passes through", map[string]any{"emit": "html"}, "html", false},
		{"invalid value rejected", map[string]any{"emit": "json"}, "", true},
		{"non-string rejected", map[string]any{"emit": 7}, "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := emitArgument(tc.args)
			if (err != nil) != tc.wantErr {
				t.Fatalf("err = %v, wantErr = %v", err, tc.wantErr)
			}
			if got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestBoolArgument(t *testing.T) {
	v, err := boolArgument(map[string]any{}, "save")
	if err != nil || v {
		t.Fatalf("missing: %v, %v", v, err)
	}
	v, err = boolArgument(map[string]any{"save": true}, "save")
	if err != nil || !v {
		t.Fatalf("true: %v, %v", v, err)
	}
	v, err = boolArgument(map[string]any{"save": false}, "save")
	if err != nil || v {
		t.Fatalf("false: %v, %v", v, err)
	}
	if _, err := boolArgument(map[string]any{"save": "true"}, "save"); err == nil {
		t.Fatal("expected error for string value")
	}
}

func TestResearchRunArgsIncludesNoBrowserCookies(t *testing.T) {
	args := researchRunArgs("OpenAI", "compact", false)
	want := []string{"OpenAI", "--emit=compact", "--no-browser-cookies"}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestResearchRunArgsSaveUsesSupportedSaveDir(t *testing.T) {
	t.Setenv("LAST30DAYS_MEMORY_DIR", "")
	args := researchRunArgs("OpenAI", "html", true)
	got := strings.Join(args, "\x00")
	if strings.Contains(got, "--save\x00") || strings.HasSuffix(got, "--save") {
		t.Fatalf("args still include unsupported --save: %#v", args)
	}
	want := []string{"OpenAI", "--emit=html", "--no-browser-cookies", "--save-dir", "~/Documents/Last30Days"}
	if got != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestResearchRunArgsSaveUsesMemoryDirEnvOverride(t *testing.T) {
	t.Setenv("LAST30DAYS_MEMORY_DIR", "/tmp/last30days-reports")
	args := researchRunArgs("OpenAI", "html", true)
	want := []string{"OpenAI", "--emit=html", "--no-browser-cookies", "--save-dir", "/tmp/last30days-reports"}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestResearchHandlerValidationErrorsAreToolErrors(t *testing.T) {
	// Validation failures are returned as MCP tool errors (not Go errors)
	// so Claude sees a structured failure with a readable message rather
	// than a transport-level fault.
	handler := makeResearchHandler(Config{Version: "test"})

	cases := []struct {
		name    string
		args    map[string]any
		wantSub string
	}{
		{"missing topic", map[string]any{}, "topic is required"},
		{"blank topic", map[string]any{"topic": "   "}, "non-empty string"},
		{"invalid emit", map[string]any{"topic": "OpenAI", "emit": "json"}, "must be 'compact' or 'html'"},
		{"non-bool save", map[string]any{"topic": "OpenAI", "save": "yes"}, "save must be a boolean"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res, err := handler(context.Background(), newCallToolRequest(tc.args))
			if err != nil {
				t.Fatalf("handler should not return Go error for validation; got %v", err)
			}
			if res == nil || !res.IsError {
				t.Fatalf("expected IsError result, got %+v", res)
			}
			if !strings.Contains(resultText(res), tc.wantSub) {
				t.Fatalf("result text %q missing substring %q", resultText(res), tc.wantSub)
			}
		})
	}
}

func TestFormatRunErrorIncludesStderr(t *testing.T) {
	res := &engine.RunResult{Stderr: []byte("engine exploded\n")}
	msg := formatRunError(errors.New("boom"), res)
	if !strings.Contains(msg, "boom") || !strings.Contains(msg, "engine exploded") {
		t.Fatalf("formatRunError missed pieces: %q", msg)
	}
}

func TestFormatRunErrorHandlesNilResult(t *testing.T) {
	msg := formatRunError(errors.New("boom"), nil)
	if msg != "boom" {
		t.Fatalf("nil result: got %q, want %q", msg, "boom")
	}
}
