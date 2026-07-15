package engine

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// makeStubPython writes a shell script that simulates python3 and returns
// its absolute path. The script honors a small env-driven protocol so each
// test can shape its output:
//
//	STUB_STDOUT      - text printed to stdout
//	STUB_STDERR      - text printed to stderr
//	STUB_EXIT_CODE   - integer exit code (default 0)
//	STUB_SLEEP_SECS  - sleep before exiting (for timeout tests)
//	STUB_ECHO_ENV    - name of an env var; the stub prints "<NAME>=<VALUE>"
//	STUB_ECHO_ARG    - integer index; the stub prints "ARG<i>=<args[i]>"
//
// The stub ignores its first argument (the script path), matching how a
// real python3 invocation treats `python3 last30days.py ...`.
func makeStubPython(t *testing.T) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("stub-python tests rely on POSIX shell")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "python3-stub.sh")
	script := `#!/usr/bin/env bash
if [ -n "${STUB_SLEEP_SECS:-}" ]; then sleep "$STUB_SLEEP_SECS"; fi
if [ -n "${STUB_STDOUT:-}" ]; then printf "%s" "$STUB_STDOUT"; fi
if [ -n "${STUB_STDERR:-}" ]; then printf "%s" "$STUB_STDERR" >&2; fi
if [ -n "${STUB_ECHO_ENV:-}" ]; then echo "${STUB_ECHO_ENV}=${!STUB_ECHO_ENV:-<unset>}"; fi
if [ -n "${STUB_ECHO_ARG:-}" ]; then echo "ARG${STUB_ECHO_ARG}=${!STUB_ECHO_ARG:-<unset>}"; fi
exit "${STUB_EXIT_CODE:-0}"
`
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("write stub: %v", err)
	}
	return path
}

// stageCache materializes a fake CacheDir with a no-op last30days.py so
// the existence check in Run passes. The stub python3 ignores the script
// contents, so the file just has to exist.
func stageCache(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "last30days.py"), []byte("# stub\n"), 0o644); err != nil {
		t.Fatalf("stage cache: %v", err)
	}
	return dir
}

func TestRunHappyPath(t *testing.T) {
	stub := makeStubPython(t)
	cache := stageCache(t)
	t.Setenv("STUB_STDOUT", "synthesis output\n")

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
		Args:       []string{"my topic", "--emit=compact"},
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if string(res.Stdout) != "synthesis output\n" {
		t.Fatalf("stdout = %q, want %q", res.Stdout, "synthesis output\n")
	}
	if res.ExitCode != 0 {
		t.Fatalf("ExitCode = %d, want 0", res.ExitCode)
	}
	if res.TimedOut {
		t.Fatal("TimedOut = true, want false")
	}
}

func TestRunForwardsEnv(t *testing.T) {
	stub := makeStubPython(t)
	cache := stageCache(t)
	t.Setenv("OPENAI_API_KEY", "sk-test-value")
	t.Setenv("STUB_ECHO_ENV", "OPENAI_API_KEY")

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if got := strings.TrimSpace(string(res.Stdout)); got != "OPENAI_API_KEY=sk-test-value" {
		t.Fatalf("stdout = %q, want OPENAI_API_KEY=sk-test-value", got)
	}
}

func TestRunSetsPythonPath(t *testing.T) {
	stub := makeStubPython(t)
	cache := stageCache(t)
	t.Setenv("STUB_ECHO_ENV", "PYTHONPATH")

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	want := "PYTHONPATH=" + cache
	if got := strings.TrimSpace(string(res.Stdout)); got != want {
		t.Fatalf("stdout = %q, want %q", got, want)
	}
}

// TestRunDropsPreExistingPythonPath guards the buildEnv dedup: when the
// parent already sets PYTHONPATH (common on dev machines and CI runners
// that touch Python), the child must NOT see two PYTHONPATH= entries.
// POSIX getenv returns the first match, so a duplicate from os.Environ
// would shadow our cache-dir entry and break `from lib import ...`.
func TestRunDropsPreExistingPythonPath(t *testing.T) {
	stub := makeStubPython(t)
	cache := stageCache(t)
	t.Setenv("PYTHONPATH", "/users-stale-pythonpath")
	t.Setenv("STUB_ECHO_ENV", "PYTHONPATH")

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	got := strings.TrimSpace(string(res.Stdout))
	want := "PYTHONPATH=" + cache
	if got != want {
		t.Fatalf("stdout = %q, want %q (stale parent value leaked through)", got, want)
	}
}

func TestBuildEnvDropsAllPreExistingPythonPath(t *testing.T) {
	// Direct unit test on buildEnv to catch the case where the parent has
	// PYTHONPATH set: the returned slice must contain exactly one
	// PYTHONPATH= entry, and it must be ours.
	t.Setenv("PYTHONPATH", "/parent/one")
	cache := "/cache/dir"
	out := buildEnv(cache, []string{"EXTRA=1"})

	var pythonPaths []string
	for _, kv := range out {
		if strings.HasPrefix(kv, "PYTHONPATH=") {
			pythonPaths = append(pythonPaths, kv)
		}
	}
	if len(pythonPaths) != 1 {
		t.Fatalf("got %d PYTHONPATH entries, want 1: %v", len(pythonPaths), pythonPaths)
	}
	if pythonPaths[0] != "PYTHONPATH="+cache {
		t.Fatalf("PYTHONPATH = %q, want %q", pythonPaths[0], "PYTHONPATH="+cache)
	}
	// Confirm ExtraEnv still rides along.
	found := false
	for _, kv := range out {
		if kv == "EXTRA=1" {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("EXTRA=1 missing from buildEnv output")
	}
}

func TestRunSurfacesExitCode(t *testing.T) {
	stub := makeStubPython(t)
	cache := stageCache(t)
	t.Setenv("STUB_STDERR", "engine boom\n")
	t.Setenv("STUB_EXIT_CODE", "2")

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
	})
	if err == nil {
		t.Fatal("expected error for non-zero exit")
	}
	if res == nil {
		t.Fatal("res is nil; want populated result alongside error")
	}
	if res.ExitCode != 2 {
		t.Fatalf("ExitCode = %d, want 2", res.ExitCode)
	}
	if !strings.Contains(string(res.Stderr), "engine boom") {
		t.Fatalf("stderr did not surface engine output: %q", res.Stderr)
	}
}

func TestRunTimesOut(t *testing.T) {
	stub := makeStubPython(t)
	cache := stageCache(t)
	t.Setenv("STUB_SLEEP_SECS", "3")

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
		Timeout:    200 * time.Millisecond,
	})
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if !res.TimedOut {
		t.Fatal("TimedOut = false, want true")
	}
	if !strings.Contains(err.Error(), "timeout") {
		t.Fatalf("error %q lacks 'timeout' marker", err)
	}
}

func TestRunMissingPython(t *testing.T) {
	cache := stageCache(t)
	// Empty PATH guarantees the lookup fails. PythonPath stays unset so Run
	// falls through to exec.LookPath.
	t.Setenv("PATH", "")

	_, err := Run(context.Background(), RunOptions{CacheDir: cache})
	if err == nil {
		t.Fatal("expected lookup failure with empty PATH")
	}
	if !strings.Contains(err.Error(), DefaultPythonBinary) {
		t.Fatalf("error %q does not mention %s", err, DefaultPythonBinary)
	}
	if !strings.Contains(err.Error(), PythonInstallURL) {
		t.Fatalf("error %q does not include install URL", err)
	}
}

func TestRunMissingScript(t *testing.T) {
	stub := makeStubPython(t)
	// CacheDir exists but contains no last30days.py.
	cache := t.TempDir()

	_, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
	})
	if err == nil {
		t.Fatal("expected error when last30days.py missing")
	}
	if !strings.Contains(err.Error(), "last30days.py") {
		t.Fatalf("error %q does not name missing script", err)
	}
}

func TestRunRejectsEmptyCacheDir(t *testing.T) {
	stub := makeStubPython(t)
	_, err := Run(context.Background(), RunOptions{PythonPath: stub})
	if err == nil {
		t.Fatal("expected error for empty CacheDir")
	}
	if !errors.Is(err, err) || !strings.Contains(err.Error(), "CacheDir") {
		t.Fatalf("error %q does not name CacheDir", err)
	}
}

func TestResolveTimeoutHonorsEnv(t *testing.T) {
	t.Setenv(TimeoutEnvOverride, "750ms")
	if got := resolveTimeout(0); got != 750*time.Millisecond {
		t.Fatalf("resolveTimeout = %v, want 750ms", got)
	}
	t.Setenv(TimeoutEnvOverride, "garbage")
	if got := resolveTimeout(0); got != DefaultTimeout {
		t.Fatalf("garbage value: got %v, want default %v", got, DefaultTimeout)
	}
	if got := resolveTimeout(time.Minute); got != time.Minute {
		t.Fatalf("explicit value not honored: got %v", got)
	}
}
