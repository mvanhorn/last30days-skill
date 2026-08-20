// Package manifest holds tests for mcp/manifest.json. It contains no
// production code - the manifest itself is the artifact, and these tests
// guard structural invariants the bundling pipeline depends on.
package manifest

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// envBinding is a minimal subset of the MCPB v0.3 manifest just covering
// the fields these tests assert on. We deliberately do not depend on the
// printing-press internal/pipeline types (that's an internal/ package and
// not importable across modules) - the structural invariants below are
// what actually matter for Claude Desktop install correctness.
type manifestShape struct {
	ManifestVersion string `json:"manifest_version"`
	Name            string `json:"name"`
	Version         string `json:"version"`
	Server          struct {
		Type       string `json:"type"`
		EntryPoint string `json:"entry_point"`
		MCPConfig  struct {
			Command string            `json:"command"`
			Env     map[string]string `json:"env"`
		} `json:"mcp_config"`
	} `json:"server"`
	UserConfig map[string]struct {
		Type        string `json:"type"`
		Title       string `json:"title"`
		Description string `json:"description"`
		Sensitive   bool   `json:"sensitive"`
		Required    bool   `json:"required"`
	} `json:"user_config"`
	Compatibility struct {
		ClaudeDesktop string   `json:"claude_desktop"`
		Platforms     []string `json:"platforms"`
	} `json:"compatibility"`
}

// loadManifest reads mcp/manifest.json relative to this test file so the
// test passes regardless of where `go test` is invoked from.
func loadManifest(t *testing.T) manifestShape {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	// manifest_test.go is at mcp/internal/manifest/; manifest.json at mcp/.
	manifestPath := filepath.Join(filepath.Dir(thisFile), "..", "..", "manifest.json")
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	var m manifestShape
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("parse manifest: %v", err)
	}
	return m
}

func TestManifestRequiredFields(t *testing.T) {
	m := loadManifest(t)
	if m.ManifestVersion != "0.3" {
		t.Errorf("manifest_version = %q, want 0.3", m.ManifestVersion)
	}
	if m.Name != "last30days-pp-mcp" {
		t.Errorf("name = %q, want last30days-pp-mcp", m.Name)
	}
	if m.Version == "" {
		t.Error("version is empty")
	}
	if m.Server.Type != "binary" {
		t.Errorf("server.type = %q, want binary", m.Server.Type)
	}
	if m.Server.EntryPoint != "bin/last30days-pp-mcp" {
		t.Errorf("server.entry_point = %q, want bin/last30days-pp-mcp", m.Server.EntryPoint)
	}
	if m.Compatibility.ClaudeDesktop == "" {
		t.Error("compatibility.claude_desktop is empty")
	}
}

// TestEnvAndUserConfigCrossReference is the key invariant: every
// ${user_config.<key>} substitution in server.mcp_config.env must point
// at a real user_config entry, and every declared user_config must be
// wired to an env var. A typo on either side silently disables a credential
// at install time without the binary or Claude Desktop noticing.
func TestEnvAndUserConfigCrossReference(t *testing.T) {
	m := loadManifest(t)

	if len(m.Server.MCPConfig.Env) == 0 {
		t.Fatal("server.mcp_config.env is empty; expected user_config substitutions")
	}
	if len(m.UserConfig) == 0 {
		t.Fatal("user_config is empty; expected per-key declarations")
	}

	for envName, value := range m.Server.MCPConfig.Env {
		key, ok := parseUserConfigRef(value)
		if !ok {
			t.Errorf("env[%s] = %q is not a ${user_config.<key>} reference", envName, value)
			continue
		}
		if _, declared := m.UserConfig[key]; !declared {
			t.Errorf("env[%s] references user_config[%q], which is not declared", envName, key)
		}
		// The user_config key must be the lowercased env var so Claude
		// Desktop's substitution rule matches PP's emitted shape.
		if got := strings.ToLower(envName); key != got {
			t.Errorf("env[%s] -> user_config[%q]; convention requires user_config[%q]", envName, key, got)
		}
	}

	envValues := make(map[string]bool, len(m.Server.MCPConfig.Env))
	for _, value := range m.Server.MCPConfig.Env {
		if key, ok := parseUserConfigRef(value); ok {
			envValues[key] = true
		}
	}
	for key := range m.UserConfig {
		if !envValues[key] {
			t.Errorf("user_config[%q] is declared but never substituted into env", key)
		}
	}
}

func TestUserConfigShape(t *testing.T) {
	m := loadManifest(t)
	for key, slot := range m.UserConfig {
		if slot.Type != "string" {
			t.Errorf("user_config[%q].type = %q, want string", key, slot.Type)
		}
		if slot.Title == "" {
			t.Errorf("user_config[%q].title is empty", key)
		}
		if slot.Description == "" {
			t.Errorf("user_config[%q].description is empty", key)
		}
		if !slot.Sensitive {
			// API keys must be flagged sensitive so Claude Desktop masks
			// the input and prefers OS-keychain storage.
			t.Errorf("user_config[%q].sensitive = false; want true for API credentials", key)
		}
		if slot.Required {
			// The engine degrades to web-only mode without keys, so no
			// key is install-blocking.
			t.Errorf("user_config[%q].required = true; engine degrades without keys, so all keys are optional", key)
		}
	}
}

func TestPlatformsMatchShippingMatrix(t *testing.T) {
	// compatibility.platforms must list exactly what the release CI
	// actually packages. Listing a platform we don't ship would let
	// Claude Desktop start an install that has no matching binary inside
	// the bundle, producing a silent failure. The CI matrix in
	// .github/workflows/release.yml currently covers darwin (arm64 +
	// amd64) and linux/amd64; Windows is deferred.
	m := loadManifest(t)
	required := map[string]bool{"darwin": false, "linux": false}
	forbidden := map[string]bool{"win32": true}
	for _, p := range m.Compatibility.Platforms {
		if _, ok := required[p]; ok {
			required[p] = true
		}
		if forbidden[p] {
			t.Errorf("compatibility.platforms contains %q but the release matrix does not ship that platform; add it to the matrix or remove from the manifest", p)
		}
	}
	for p, found := range required {
		if !found {
			t.Errorf("compatibility.platforms missing %q", p)
		}
	}
}

func parseUserConfigRef(value string) (string, bool) {
	const prefix = "${user_config."
	const suffix = "}"
	if !strings.HasPrefix(value, prefix) || !strings.HasSuffix(value, suffix) {
		return "", false
	}
	return value[len(prefix) : len(value)-len(suffix)], true
}
