package tools

import (
	"context"
	"encoding/json"
	"os"
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
	t.Setenv("LAST30DAYS_X_BACKEND", "")
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("XQUIK_API_KEY", "")
	t.Setenv("AUTH_TOKEN", "")
	t.Setenv("CT0", "")
	t.Setenv("BSKY_HANDLE", "alice.bsky.social")
	t.Setenv("BSKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")

	extra := researchExtraEnv()
	if !slices.Contains(extra, xAPIOptInEnv) {
		t.Fatalf("researchExtraEnv() = %#v, want %s", extra, xAPIOptInEnv)
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
			t.Errorf("unexpected LAST30DAYS_X_BACKEND action item: %q", item)
		}
	}
}

func TestXAPIAutoOptInDoesNotOverrideDotenvPin(t *testing.T) {
	cacheDir, err := filepath.Abs("../../../skills/last30days/scripts")
	if err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	configDir := filepath.Join(home, "config")
	if err := os.Mkdir(configDir, 0700); err != nil {
		t.Fatal(err)
	}
	// Pin bird in .env without static bird cookies: xapi opt-in must NOT
	// override the explicit .env pin to activate X via xapi.
	if err := os.WriteFile(filepath.Join(configDir, ".env"), []byte("LAST30DAYS_X_BACKEND=bird\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("LAST30DAYS_CONFIG_DIR", configDir)
	t.Setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")
	t.Setenv(BrowserCookiesEnvOverride, "")
	t.Setenv("X_BEARER_TOKEN", "dummy-mcp-bearer-token")
	t.Setenv("LAST30DAYS_X_BACKEND", "${user_config.last30days_x_backend}")
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("XQUIK_API_KEY", "")
	t.Setenv("AUTH_TOKEN", "")
	t.Setenv("CT0", "")

	res, runErr := engine.Run(context.Background(), engine.RunOptions{
		CacheDir: cacheDir,
		Args:     preflightRunArgs("json", ""),
		ExtraEnv: researchExtraEnv(),
	})
	if runErr != nil {
		t.Fatalf("preflight run failed: %v\nstderr:\n%s", runErr, string(res.Stderr))
	}

	var payload struct {
		Network struct {
			AvailableSources []string `json:"available_sources"`
		} `json:"network"`
	}
	if err := json.Unmarshal(res.Stdout, &payload); err != nil {
		t.Fatalf("parse preflight json: %v\nstdout:\n%s", err, string(res.Stdout))
	}
	if slices.Contains(payload.Network.AvailableSources, "x") {
		t.Errorf("preflight available_sources = %#v; .env LAST30DAYS_X_BACKEND=bird pin was overridden by MCP xapi opt-in", payload.Network.AvailableSources)
	}
}

func TestXAPIAutoOptInHandlesAutoBackendAndMissingNodeWithCookies(t *testing.T) {
	cacheDir, err := filepath.Abs("../../../skills/last30days/scripts")
	if err != nil {
		t.Fatal(err)
	}

	t.Run("LAST30DAYS_X_BACKEND=auto still enables xapi", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LAST30DAYS_CONFIG_DIR", filepath.Join(home, "config"))
		t.Setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")
		t.Setenv(BrowserCookiesEnvOverride, "")
		t.Setenv("X_BEARER_TOKEN", "dummy-mcp-bearer-token")
		t.Setenv("LAST30DAYS_X_BACKEND", "auto")
		t.Setenv("LAST30DAYS_X_API_OPT_IN", "")
		t.Setenv("XAI_API_KEY", "")
		t.Setenv("XQUIK_API_KEY", "")
		t.Setenv("AUTH_TOKEN", "")
		t.Setenv("CT0", "")

		res, runErr := engine.Run(context.Background(), engine.RunOptions{
			CacheDir: cacheDir,
			Args:     preflightRunArgs("json", ""),
			ExtraEnv: researchExtraEnv(),
		})
		if runErr != nil {
			t.Fatalf("preflight run failed: %v\nstderr:\n%s", runErr, string(res.Stderr))
		}
		var payload struct {
			Network struct {
				AvailableSources []string `json:"available_sources"`
			} `json:"network"`
		}
		if err := json.Unmarshal(res.Stdout, &payload); err != nil {
			t.Fatalf("parse preflight json: %v\nstdout:\n%s", err, string(res.Stdout))
		}
		if !slices.Contains(payload.Network.AvailableSources, "x") {
			t.Errorf("preflight available_sources = %#v; want 'x' enabled when LAST30DAYS_X_BACKEND=auto", payload.Network.AvailableSources)
		}
	})

	t.Run("AUTH_TOKEN+CT0 without node on PATH falls back to xapi", func(t *testing.T) {
		home := t.TempDir()
		emptyBin := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LAST30DAYS_CONFIG_DIR", filepath.Join(home, "config"))
		t.Setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")
		t.Setenv(BrowserCookiesEnvOverride, "")
		t.Setenv("X_BEARER_TOKEN", "dummy-mcp-bearer-token")
		t.Setenv("LAST30DAYS_X_BACKEND", "")
		t.Setenv("LAST30DAYS_X_API_OPT_IN", "")
		t.Setenv("XAI_API_KEY", "")
		t.Setenv("XQUIK_API_KEY", "")
		t.Setenv("AUTH_TOKEN", "dummy-auth")
		t.Setenv("CT0", "dummy-ct0")

		// Override PATH only for the child Python process so engine.Run resolves
		// Python 3.12+ normally while shutil.which("node") inside Python is None.
		res, runErr := engine.Run(context.Background(), engine.RunOptions{
			CacheDir: cacheDir,
			Args:     preflightRunArgs("json", ""),
			ExtraEnv: append(researchExtraEnv(), "PATH="+emptyBin),
		})
		if runErr != nil {
			t.Fatalf("preflight run failed: %v\nstderr:\n%s", runErr, string(res.Stderr))
		}
		var payload struct {
			Network struct {
				AvailableSources []string `json:"available_sources"`
			} `json:"network"`
		}
		if err := json.Unmarshal(res.Stdout, &payload); err != nil {
			t.Fatalf("parse preflight json: %v\nstdout:\n%s", err, string(res.Stdout))
		}
		if !slices.Contains(payload.Network.AvailableSources, "x") {
			t.Errorf("preflight available_sources = %#v; want 'x' via xapi fallback when cookies lack node", payload.Network.AvailableSources)
		}
	})

	t.Run("explicit process-env LAST30DAYS_X_BACKEND=bird pin still suppresses xapi", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LAST30DAYS_CONFIG_DIR", filepath.Join(home, "config"))
		t.Setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")
		t.Setenv(BrowserCookiesEnvOverride, "")
		t.Setenv("X_BEARER_TOKEN", "dummy-mcp-bearer-token")
		t.Setenv("LAST30DAYS_X_BACKEND", "bird")
		t.Setenv("LAST30DAYS_X_API_OPT_IN", "")
		t.Setenv("XAI_API_KEY", "")
		t.Setenv("XQUIK_API_KEY", "")
		t.Setenv("AUTH_TOKEN", "")
		t.Setenv("CT0", "")

		res, runErr := engine.Run(context.Background(), engine.RunOptions{
			CacheDir: cacheDir,
			Args:     preflightRunArgs("json", ""),
			ExtraEnv: researchExtraEnv(),
		})
		if runErr != nil {
			t.Fatalf("preflight run failed: %v\nstderr:\n%s", runErr, string(res.Stderr))
		}
		var payload struct {
			Network struct {
				AvailableSources []string `json:"available_sources"`
			} `json:"network"`
		}
		if err := json.Unmarshal(res.Stdout, &payload); err != nil {
			t.Fatalf("parse preflight json: %v\nstdout:\n%s", err, string(res.Stdout))
		}
		if slices.Contains(payload.Network.AvailableSources, "x") {
			t.Errorf("preflight available_sources = %#v; explicit LAST30DAYS_X_BACKEND=bird pin was overridden", payload.Network.AvailableSources)
		}
	})
}
