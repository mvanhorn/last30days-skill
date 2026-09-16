package engine

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func fakePython(t *testing.T, dir, name, version string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX executable fixture")
	}
	path := filepath.Join(dir, name)
	body := "#!/bin/sh\nif [ \"$1\" = '-I' ]; then printf '%s\\n' '" + version + "'; else printf 'research completed'; fi\n"
	if err := os.WriteFile(path, []byte(body), 0755); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestPythonDiscoveryFallback(t *testing.T) {
	for _, name := range []string{"python3", "python3.12", "python3.99", "python"} {
		t.Run(name, func(t *testing.T) {
			t.Setenv(PythonEnvOverride, "")
			first, second := t.TempDir(), t.TempDir()
			fakePython(t, first, "python3", "3.9")
			want := fakePython(t, second, name, "3.12")
			t.Setenv("PATH", first+string(os.PathListSeparator)+second)
			got, err := resolvePython("")
			if err != nil || got != want {
				t.Fatalf("got %q, %v; want %q", got, err, want)
			}
			result, err := Run(context.Background(), RunOptions{CacheDir: stageCache(t)})
			if err != nil || string(result.Stdout) != "research completed" {
				t.Fatalf("Run = %+v, %v", result, err)
			}
		})
	}
}

func TestPythonDiscoveryPreservesDefault(t *testing.T) {
	t.Setenv(PythonEnvOverride, "")
	dir := t.TempDir()
	want := fakePython(t, dir, "python3", "3.12")
	fakePython(t, dir, "python3.14", "3.14")
	t.Setenv("PATH", dir)
	got, err := resolvePython("")
	if err != nil || got != want {
		t.Fatalf("got %q, %v", got, err)
	}
}

func TestPythonOverrideVerifiedWithoutFallback(t *testing.T) {
	dir := t.TempDir()
	fakePython(t, dir, "python3", "3.14")
	t.Setenv("PATH", dir)
	for _, version := range []string{"3.9", "invalid", "4.0", "3.12", "3.99"} {
		t.Run(version, func(t *testing.T) {
			explicit := fakePython(t, t.TempDir(), "selected-python", version)
			t.Setenv(PythonEnvOverride, explicit)
			got, err := resolvePython("")
			if version == "3.12" || version == "3.99" {
				if err != nil || got != explicit {
					t.Fatalf("got %q, %v", got, err)
				}
			} else if err == nil || got != "" || !strings.Contains(err.Error(), PythonEnvOverride) {
				t.Fatalf("invalid override fell back: %q, %v", got, err)
			}
		})
	}
	t.Setenv(PythonEnvOverride, filepath.Join(dir, "missing"))
	if _, err := resolvePython(""); err == nil {
		t.Fatal("missing override fell back")
	}
}

func TestPythonDiscoveryExcludesRelativePathsAndConfigHelpers(t *testing.T) {
	t.Setenv(PythonEnvOverride, "")
	dir := t.TempDir()
	fakePython(t, dir, "python3-config", "3.14")
	fakePython(t, dir, "python3.14-config", "3.14")
	fakePython(t, dir, "python3", "3.14")
	t.Chdir(dir)
	t.Setenv("GODEBUG", "execerrdot=0")
	t.Setenv("PATH", ".")
	t.Setenv(PythonEnvOverride, "./python3")
	if _, err := resolvePython(""); err == nil {
		t.Fatal("accepted relative override")
	}
	t.Setenv(PythonEnvOverride, "")
	if _, err := resolvePython(""); err == nil {
		t.Fatal("accepted relative PATH")
	}
	if err := os.Remove(filepath.Join(dir, "python3")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	if _, err := resolvePython(""); err == nil {
		t.Fatal("accepted config helper")
	}
}

func TestPythonProbeCancellation(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX executable fixture")
	}
	path := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(path, []byte("#!/bin/sh\nexec /bin/sleep 10\n"), 0755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	start := time.Now()
	if err := checkPythonVersion(ctx, path); err == nil {
		t.Fatal("expected timeout")
	}
	if time.Since(start) > time.Second {
		t.Fatal("probe ignored cancellation")
	}
}
