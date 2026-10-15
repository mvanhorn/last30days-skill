package engine

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"testing/fstest"
	"time"
)

func TestCacheLockOwnerProcess(t *testing.T) {
	path := os.Getenv("LAST30DAYS_TEST_CACHE_LOCK")
	if path == "" {
		return
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := lockCacheFile(file); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path+".ready", nil, 0o600); err != nil {
		t.Fatal(err)
	}
	time.Sleep(time.Minute)
}

func startCacheLockOwner(t *testing.T, path string) func() {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	return startCacheTestProcess(t, "TestCacheLockOwnerProcess", []string{"LAST30DAYS_TEST_CACHE_LOCK=" + path}, path+".ready")
}

func startCacheTestProcess(t *testing.T, test string, env []string, ready string) func() {
	t.Helper()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	cmd := exec.CommandContext(ctx, executable, "-test.run=^"+test+"$")
	cmd.Env = append(os.Environ(), env...)
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	var stopped sync.Once
	stop := func() {
		stopped.Do(func() {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		})
	}
	t.Cleanup(stop)
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			return stop
		}
		if time.Now().After(deadline) {
			stop()
			t.Fatalf("child process did not become ready: %s", &output)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func cacheStagePaths(t *testing.T, base string) []string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(base, cacheSubdir, "v1.tmp-*"))
	if err != nil {
		t.Fatal(err)
	}
	var stages []string
	for _, path := range paths {
		if !strings.HasSuffix(path, ".lock") {
			stages = append(stages, path)
		}
	}
	return stages
}

func TestEnsureReclaimsStagesAfterProcessDeath(t *testing.T) {
	base := t.TempDir()
	t.Setenv(CacheEnvOverride, base)
	stopOwner := startCacheTestProcess(t, "TestEnsureConcurrentProcesses",
		[]string{"LAST30DAYS_TEST_EXTRACT_ROLE=paused"}, filepath.Join(base, "ready"))
	stages := cacheStagePaths(t, base)
	if len(stages) != 1 {
		t.Fatalf("paused extractor stages = %v, want one", stages)
	}
	stopOwner()
	cacheDir, err := EnsureUserCache(newTestFS(), "v1")
	if err != nil {
		t.Fatal(err)
	}
	mustReadFile(t, filepath.Join(cacheDir, "last30days.py"), "# last30days entry\n")
	if _, err := os.Stat(stages[0]); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("dead extractor's stage was not reclaimed: %v", err)
	}
	if _, err := os.Stat(stages[0] + ".lock"); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("dead extractor's ownership file was not reclaimed: %v", err)
	}
}

func TestEnsurePreservesActiveStagesAndReclaimsThemAfterOwnerDeath(t *testing.T) {
	base := t.TempDir()
	t.Setenv(CacheEnvOverride, base)
	stopOwner := startCacheTestProcess(t, "TestEnsureConcurrentProcesses",
		[]string{"LAST30DAYS_TEST_EXTRACT_ROLE=paused"}, filepath.Join(base, "ready"))
	stages := cacheStagePaths(t, base)
	if len(stages) != 1 {
		t.Fatalf("paused extractor stages = %v, want one", stages)
	}
	cacheDir, err := EnsureUserCache(newTestFS(), "v1")
	if err != nil {
		t.Fatal(err)
	}
	mustReadFile(t, filepath.Join(stages[0], "alpha.txt"), "alpha")
	stopOwner()
	resetOnce(cacheDir)
	if _, err := EnsureUserCache(newTestFS(), "v1"); err != nil {
		t.Fatalf("reuse completed cache: %v", err)
	}
	if _, err := os.Stat(stages[0]); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("cached reuse did not reclaim dead extractor's stage: %v", err)
	}
	mustReadFile(t, filepath.Join(cacheDir, "last30days.py"), "# last30days entry\n")
}

func TestEnsurePreservesUnmarkedStagingDirectories(t *testing.T) {
	base := t.TempDir()
	stage := filepath.Join(base, cacheSubdir, "v1.tmp-unmarked")
	if err := os.MkdirAll(stage, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stage, "legacy.txt"), []byte(base), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Ensure(newTestFS(), base, "v1"); err != nil {
		t.Fatal(err)
	}
	mustReadFile(t, filepath.Join(stage, "legacy.txt"), base)
}

func TestEnsureReclaimsUnownedStageRegistration(t *testing.T) {
	base := t.TempDir()
	parent := filepath.Join(base, cacheSubdir)
	if err := os.MkdirAll(parent, 0o700); err != nil {
		t.Fatal(err)
	}
	owner := filepath.Join(parent, "v1.tmp-abandoned.lock")
	if err := os.WriteFile(owner, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Ensure(newTestFS(), base, "v1"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(owner); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("abandoned registration was not reclaimed: %v", err)
	}
}

func TestEnsureHeldPublicationLockTimesOut(t *testing.T) {
	base := t.TempDir()
	t.Setenv(CacheEnvOverride, base)
	stopOwner := startCacheLockOwner(t, filepath.Join(base, cacheSubdir, "v1.lock"))
	const callers = 4
	done := make(chan error, callers)
	started := time.Now()
	for range callers {
		go func() {
			_, err := EnsureUserCache(newTestFS(), "v1")
			done <- err
		}()
	}
	deadline := time.After(7 * time.Second)
	var results []error
	for range callers {
		select {
		case err := <-done:
			results = append(results, err)
		case <-deadline:
			stopOwner()
			for len(results) < callers {
				select {
				case err := <-done:
					results = append(results, err)
				case <-time.After(5 * time.Second):
					t.Fatal("extraction did not finish after lock owner was killed")
				}
			}
			t.Fatal("held publication lock exceeded the seven-second test deadline")
		}
	}
	for _, err := range results {
		if !errors.Is(err, os.ErrDeadlineExceeded) {
			t.Fatalf("held publication lock: got %v, want deadline error", err)
		}
	}
	t.Logf("all %d held-lock callers returned deadline errors after %s", callers, time.Since(started))
	if _, err := os.Stat(filepath.Join(base, cacheSubdir, "v1")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("timed-out contender published a cache: %v", err)
	}
	staged, err := filepath.Glob(filepath.Join(base, cacheSubdir, "v1.tmp-*"))
	if err != nil || len(staged) != 0 {
		t.Fatalf("timed-out contender left staging directories: %v, %v", staged, err)
	}
	stopOwner()
	cacheDir, err := EnsureUserCache(newTestFS(), "v1")
	if err != nil {
		t.Fatalf("retry after lock owner death: %v", err)
	}
	mustReadFile(t, filepath.Join(cacheDir, "last30days.py"), "# last30days entry\n")
}

func TestCacheLockExcludesOtherProcessesAndReleasesOnOwnerDeath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cache.lock")
	stopOwner := startCacheLockOwner(t, path)
	file, err := os.OpenFile(path, os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	done := make(chan error, 1)
	go func() { done <- lockCacheFile(file) }()
	select {
	case err := <-done:
		t.Fatalf("contender returned while another process held the lock: %v", err)
	case <-time.After(250 * time.Millisecond):
	}
	stopOwner()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("acquire after owner death: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("lock was not released after owner death")
	}
}

func TestEnsurePublicationTimeoutCleansOwnedStage(t *testing.T) {
	base := t.TempDir()
	t.Setenv(CacheEnvOverride, base)
	src := newTestFS()
	src["zeta.txt"] = &fstest.MapFile{Data: []byte("zeta")}
	done := make(chan error, 1)
	finished := false
	t.Cleanup(func() {
		_ = os.WriteFile(filepath.Join(base, "release"), nil, 0o600)
		if !finished {
			select {
			case <-done:
			case <-time.After(7 * time.Second):
				t.Error("extractor did not finish during cleanup")
			}
		}
	})
	go func() {
		_, err := EnsureUserCache(pausedExtractionFS{FS: src, base: base}, "v1")
		done <- err
	}()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(base, "ready")); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("extractor did not pause before publication")
		}
		time.Sleep(10 * time.Millisecond)
	}
	stages := cacheStagePaths(t, base)
	if len(stages) != 1 {
		t.Fatalf("paused extractor stages = %v, want one", stages)
	}
	startCacheLockOwner(t, filepath.Join(base, cacheSubdir, "v1.lock"))
	if err := os.WriteFile(filepath.Join(base, "release"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		finished = true
		if !errors.Is(err, os.ErrDeadlineExceeded) {
			t.Fatalf("publication contention: got %v, want deadline error", err)
		}
	case <-time.After(7 * time.Second):
		t.Fatal("publication contention exceeded the seven-second test deadline")
	}
	if _, err := os.Stat(stages[0]); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("timed-out extractor retained its stage: %v", err)
	}
	if _, err := os.Stat(stages[0] + ".lock"); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("timed-out extractor retained its ownership file: %v", err)
	}
}
