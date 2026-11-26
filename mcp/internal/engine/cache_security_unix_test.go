//go:build darwin || dragonfly || freebsd || illumos || linux || netbsd || openbsd

package engine

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestEnsureRebuildsWhenCachedFileReplacedWithFIFO(t *testing.T) {
	src := newTestFS()
	base := t.TempDir()

	cacheDir, err := Ensure(src, base, "v1")
	if err != nil {
		t.Fatalf("initial Ensure: %v", err)
	}
	target := filepath.Join(cacheDir, "last30days.py")
	if err := os.Remove(target); err != nil {
		t.Fatalf("remove target: %v", err)
	}
	if err := syscall.Mkfifo(target, 0o600); err != nil {
		t.Fatalf("mkfifo: %v", err)
	}

	resetOnce(cacheDir)
	done := make(chan error, 1)
	go func() {
		_, err := Ensure(src, base, "v1")
		done <- err
	}()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("re-Ensure after FIFO replacement: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Ensure hung on FIFO in cache instead of rejecting and rebuilding")
	}

	info, err := os.Lstat(target)
	if err != nil {
		t.Fatalf("lstat rebuilt target: %v", err)
	}
	if !info.Mode().IsRegular() {
		t.Fatalf("expected rebuilt target to be regular file, got mode %v", info.Mode())
	}
	mustReadFile(t, target, "# last30days entry\n")
}
