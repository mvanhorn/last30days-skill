package tools

import (
	"context"
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/mvanhorn/last30days-skill/mcp/internal/engine"
)

func TestPreflightAndResearchShareBrowserCookiePolicy(t *testing.T) {
	for _, tc := range []struct {
		optIn string
		deny  bool
	}{
		{"", true},
		{"off", true},
		{"TrUe", false},
	} {
		t.Run("opt-in="+tc.optIn, func(t *testing.T) {
			t.Setenv(BrowserCookiesEnvOverride, tc.optIn)
			preflight := preflightRunArgs("json", "/tmp/reports")
			research := researchRunArgs("topic", "compact", "", false)
			if got := slices.Contains(preflight, "--no-browser-cookies"); got != tc.deny {
				t.Errorf("preflight cookie denial = %t, want %t", got, tc.deny)
			}
			if got := slices.Contains(research, "--no-browser-cookies"); got != tc.deny {
				t.Errorf("research cookie denial = %t, want %t", got, tc.deny)
			}
		})
	}
}

func TestPreflightRunArgsDefaultTextIsSafe(t *testing.T) {
	t.Setenv("LAST30DAYS_MEMORY_DIR", "")
	t.Setenv(BrowserCookiesEnvOverride, "")
	args := preflightRunArgs("text", "~/Documents/Last30Days")
	want := []string{
		"--preflight",
		"--save-dir", "",
		"--preflight-report-on-save-dir",
		"~/Documents/Last30Days",
		"--no-browser-cookies",
	}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestPreflightRunArgsJSONIsSafeAndStructured(t *testing.T) {
	t.Setenv("LAST30DAYS_MEMORY_DIR", "/tmp/last30days-reports")
	t.Setenv(BrowserCookiesEnvOverride, "")
	args := preflightRunArgs("json", "/tmp/last30days-reports")
	want := []string{
		"--preflight",
		"--save-dir", "",
		"--preflight-report-on-save-dir",
		"/tmp/last30days-reports",
		"--emit=json",
		"--no-browser-cookies",
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

func TestPreflightAndResearchAgreeOnXAPIAutoOptInAndBlueskyHandle(t *testing.T) {
	cacheDir, err := filepath.Abs("../../../skills/last30days/scripts")
	if err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("LAST30DAYS_CONFIG_DIR", filepath.Join(home, "config"))
	t.Setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")
	t.Setenv(BrowserCookiesEnvOverride, "")
	t.Setenv("X_BEARER_TOKEN", "dummy-mcp-bearer-token")
	t.Setenv("LAST30DAYS_X_BACKEND", "${user_config.last30days_x_backend}")
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("XQUIK_API_KEY", "")
	t.Setenv("AUTH_TOKEN", "")
	t.Setenv("CT0", "")
	t.Setenv("BSKY_HANDLE", "alice.bsky.social")
	t.Setenv("BSKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")

	extra := researchExtraEnv()
	if !slices.Contains(extra, "LAST30DAYS_X_BACKEND=xapi") {
		t.Fatalf("researchExtraEnv() = %#v, want LAST30DAYS_X_BACKEND=xapi", extra)
	}

	res, runErr := engine.Run(context.Background(), engine.RunOptions{
		CacheDir: cacheDir,
		Args:     preflightRunArgs("json", ""),
		ExtraEnv: extra,
	})
	if runErr != nil {
		t.Fatalf("preflight run failed: %v\nstderr:\n%s", runErr, string(res.Stderr))
	}

	var payload struct {
		Network struct {
			AvailableSources []string `json:"available_sources"`
		} `json:"network"`
		ActionItems []string `json:"action_items"`
	}
	if err := json.Unmarshal(res.Stdout, &payload); err != nil {
		t.Fatalf("parse preflight json: %v\nstdout:\n%s", err, string(res.Stdout))
	}
	if !slices.Contains(payload.Network.AvailableSources, "x") {
		t.Errorf("preflight available_sources = %#v, want 'x' enabled via xapi auto opt-in", payload.Network.AvailableSources)
	}
	if !slices.Contains(payload.Network.AvailableSources, "bluesky") {
		t.Errorf("preflight available_sources = %#v, want 'bluesky' enabled via BSKY_HANDLE + BSKY_APP_PASSWORD", payload.Network.AvailableSources)
	}
	for _, item := range payload.ActionItems {
		if strings.Contains(item, "LAST30DAYS_X_BACKEND") {
			t.Errorf("unsubstituted placeholder leaked into preflight action_items: %q", item)
		}
	}
}
