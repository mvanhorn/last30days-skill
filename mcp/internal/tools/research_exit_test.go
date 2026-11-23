package tools

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/mvanhorn/last30days-skill/mcp/internal/engine"
)

func TestResearchHandlerDegradedExit(t *testing.T) {
	compact := "Evidence from a healthy source\n\n---\n" + canonicalOutputEnd + "\n"
	html := "<!DOCTYPE html>\n<html><body>Evidence</body></html>\n"
	receipt := "[last30days] strict-exit: degraded sources: x\n"

	for _, tc := range []struct {
		name, emit, stdout, stderr string
		code                       int
		wantStatus                 string
	}{
		{"clean", "compact", compact, "", 0, "complete"},
		{"degraded compact", "compact", compact, receipt, 3, "degraded"},
		{"degraded compact with CRLF", "compact", strings.ReplaceAll(compact, "\n", "\r\n"), strings.ReplaceAll(receipt, "\n", "\r\n"), 3, "degraded"},
		{"degraded HTML", "html", html, receipt, 3, "degraded"},
		{"needs clarification", "compact", "", "Clarify which topic\n", 3, "error"},
		{"complete-looking unrelated output", "compact", compact, "Other failure\n", 3, "error"},
		{"incomplete compact", "compact", "Evidence from a healthy source\n", receipt, 3, "error"},
		{"incomplete HTML", "html", strings.TrimSuffix(html, "</html>\n"), receipt, 3, "error"},
		{"hard exit even with receipt", "compact", compact, receipt, 1, "error"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cacheRoot := t.TempDir()
			t.Setenv(engine.CacheEnvOverride, cacheRoot)
			const version = "test-strict-exit"
			script := fmt.Sprintf(
				"import sys\nsys.stdout.buffer.write(%q.encode())\nsys.stderr.buffer.write(%q.encode())\nraise SystemExit(%d)\n",
				tc.stdout, tc.stderr, tc.code,
			)
			fakeFS := fstest.MapFS{
				"last30days.py": {Data: []byte(script)},
			}
			handler := makeResearchHandler(Config{Version: version, EngineFS: fakeFS})
			result, err := handler(context.Background(), newCallToolRequest(map[string]any{
				"topic": "Evidence", "emit": tc.emit,
			}))
			if err != nil || result == nil {
				t.Fatalf("result = %+v, error = %v", result, err)
			}
			switch tc.wantStatus {
			case "error":
				if !result.IsError || result.StructuredContent != nil || strings.Contains(resultText(result), "Evidence from a healthy source") {
					t.Fatalf("expected only a tool error, got %+v", result)
				}
			case "degraded":
				structured, ok := result.StructuredContent.(map[string]string)
				if result.IsError || !ok || structured["status"] != "degraded" || structured["report"] != tc.stdout {
					t.Fatalf("expected structured degraded report, got %+v", result)
				}
				if !strings.Contains(resultText(result), "degraded coverage") || !strings.Contains(resultText(result), tc.stdout) {
					t.Fatalf("expected a degraded notice and the report, got %q", resultText(result))
				}
			case "complete":
				if result.IsError || resultText(result) != tc.stdout {
					t.Fatalf("expected the clean report unchanged, got %+v", result)
				}
			}
		})
	}
}
