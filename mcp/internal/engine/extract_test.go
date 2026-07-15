package engine

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
	"testing/fstest"
)

func newTestFS() fstest.MapFS {
	return fstest.MapFS{
		"last30days.py":  &fstest.MapFile{Data: []byte("# last30days entry\n"), Mode: 0o644},
		"lib/__init__.py": &fstest.MapFile{Data: []byte(""), Mode: 0o644},
		"lib/env.py":      &fstest.MapFile{Data: []byte("# env helpers\n"), Mode: 0o644},
	}
}

func TestEnsureExtractsEngine(t *testing.T) {
	src := newTestFS()
	base := t.TempDir()

	cacheDir, err := Ensure(src, base, "v1")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if cacheDir != filepath.Join(base, cacheSubdir, "v1") {
		t.Fatalf("cacheDir = %q, want %q", cacheDir, filepath.Join(base, cacheSubdir, "v1"))
	}
	mustReadFile(t, filepath.Join(cacheDir, "last30days.py"), "# last30days entry\n")
	mustReadFile(t, filepath.Join(cacheDir, "lib/env.py"), "# env helpers\n")
	mustReadFile(t, filepath.Join(cacheDir, SentinelFilename), "v1")
}

func TestEnsureSkipsWhenSentinelMatches(t *testing.T) {
	src := newTestFS()
	base := t.TempDir()

	cacheDir, err := Ensure(src, base, "v1")
	if err != nil {
		t.Fatalf("first Ensure: %v", err)
	}
	target := filepath.Join(cacheDir, "last30days.py")
	info1, err := os.Stat(target)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}

	// Reset the sync.Once so a second call would re-extract if not for the
	// sentinel short-circuit. Without the reset, sync.Once would skip the
	// extraction regardless of sentinel state.
	resetOnce(cacheDir)

	if _, err := Ensure(src, base, "v1"); err != nil {
		t.Fatalf("second Ensure: %v", err)
	}
	info2, err := os.Stat(target)
	if err != nil {
		t.Fatalf("stat second: %v", err)
	}
	if !info2.ModTime().Equal(info1.ModTime()) {
		t.Fatalf("expected file untouched on sentinel match; got mtime %v -> %v", info1.ModTime(), info2.ModTime())
	}
}

func TestEnsureReExtractsOnVersionChange(t *testing.T) {
	v1 := fstest.MapFS{
		"last30days.py": &fstest.MapFile{Data: []byte("v1\n"), Mode: 0o644},
	}
	v2 := fstest.MapFS{
		"last30days.py": &fstest.MapFile{Data: []byte("v2\n"), Mode: 0o644},
	}
	base := t.TempDir()

	cache1, err := Ensure(v1, base, "v1")
	if err != nil {
		t.Fatalf("Ensure v1: %v", err)
	}
	cache2, err := Ensure(v2, base, "v2")
	if err != nil {
		t.Fatalf("Ensure v2: %v", err)
	}
	if cache1 == cache2 {
		t.Fatalf("expected distinct cache dirs per version, got %q == %q", cache1, cache2)
	}
	mustReadFile(t, filepath.Join(cache1, "last30days.py"), "v1\n")
	mustReadFile(t, filepath.Join(cache2, "last30days.py"), "v2\n")
}

func TestEnsureConcurrentFirstCall(t *testing.T) {
	src := newTestFS()
	base := t.TempDir()

	const goroutines = 10
	var wg sync.WaitGroup
	wg.Add(goroutines)
	results := make([]string, goroutines)
	errs := make([]error, goroutines)
	for i := 0; i < goroutines; i++ {
		i := i
		go func() {
			defer wg.Done()
			results[i], errs[i] = Ensure(src, base, "v1")
		}()
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("goroutine %d: %v", i, err)
		}
	}
	for i := 1; i < goroutines; i++ {
		if results[i] != results[0] {
			t.Fatalf("goroutine 0 saw %q, goroutine %d saw %q", results[0], i, results[i])
		}
	}
	mustReadFile(t, filepath.Join(results[0], "last30days.py"), "# last30days entry\n")
}

func TestEnsureRejectsEmptyVersion(t *testing.T) {
	if _, err := Ensure(newTestFS(), t.TempDir(), ""); err == nil {
		t.Fatal("expected error for empty version")
	}
}

func TestEnsureReturnsErrorWhenCacheUnwritable(t *testing.T) {
	// Place the cache root at a path that cannot exist (a regular file).
	// MkdirAll will refuse and Ensure must surface a wrapped error.
	base := t.TempDir()
	blocker := filepath.Join(base, "blocker")
	if err := os.WriteFile(blocker, []byte("not a dir"), 0o644); err != nil {
		t.Fatalf("setup: %v", err)
	}

	_, err := Ensure(newTestFS(), blocker, "v1")
	if err == nil {
		t.Fatal("expected error when cache parent is not a directory")
	}
}

func TestEnsureUserCacheHonorsOverride(t *testing.T) {
	override := t.TempDir()
	t.Setenv(CacheEnvOverride, override)

	src := newTestFS()
	cacheDir, err := EnsureUserCache(src, "v1")
	if err != nil {
		t.Fatalf("EnsureUserCache: %v", err)
	}
	want := filepath.Join(override, cacheSubdir, "v1")
	if cacheDir != want {
		t.Fatalf("cacheDir = %q, want %q", cacheDir, want)
	}
	mustReadFile(t, filepath.Join(cacheDir, "last30days.py"), "# last30days entry\n")
}

func mustReadFile(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if string(data) != want {
		t.Fatalf("%s: got %q, want %q", path, string(data), want)
	}
}
