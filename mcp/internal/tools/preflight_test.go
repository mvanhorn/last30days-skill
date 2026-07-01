package tools

import (
	"strings"
	"testing"
)

func TestPreflightRunArgsDefaultTextIsSafe(t *testing.T) {
	t.Setenv("LAST30DAYS_MEMORY_DIR", "")
	args := preflightRunArgs("text")
	want := []string{
		"--preflight",
		"--preflight-report-on-save-dir",
		"~/Documents/Last30Days",
	}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestPreflightRunArgsJSONIsSafeAndStructured(t *testing.T) {
	t.Setenv("LAST30DAYS_MEMORY_DIR", "/tmp/last30days-reports")
	args := preflightRunArgs("json")
	want := []string{
		"--preflight",
		"--preflight-report-on-save-dir",
		"/tmp/last30days-reports",
		"--emit=json",
	}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestPreflightFormatArgumentDefaultsAndValidates(t *testing.T) {
	cases := []struct {
		name    string
		args    map[string]any
		want    string
		wantErr bool
	}{
		{"missing defaults to text", map[string]any{}, "text", false},
		{"empty defaults to text", map[string]any{"format": ""}, "text", false},
		{"text passes", map[string]any{"format": "text"}, "text", false},
		{"json passes", map[string]any{"format": "json"}, "json", false},
		{"invalid rejected", map[string]any{"format": "xml"}, "", true},
		{"non-string rejected", map[string]any{"format": true}, "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := preflightFormatArgument(tc.args)
			if (err != nil) != tc.wantErr {
				t.Fatalf("err = %v, wantErr = %v", err, tc.wantErr)
			}
			if got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}
