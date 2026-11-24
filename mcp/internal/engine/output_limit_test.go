package engine

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestHeadBufferBoundsAndDrains(t *testing.T) {
	t.Run("under and exact limit", func(t *testing.T) {
		b := newHeadBuffer(10)
		n, err := b.Write([]byte("hello"))
		if err != nil || n != 5 {
			t.Fatalf("Write = (%d, %v), want (5, nil)", n, err)
		}
		n, err = b.Write([]byte("world"))
		if err != nil || n != 5 {
			t.Fatalf("Write = (%d, %v), want (5, nil)", n, err)
		}
		if b.Truncated() || b.Total() != 10 || string(b.Bytes()) != "helloworld" {
			t.Fatalf("state = truncated:%v total:%d bytes:%q", b.Truncated(), b.Total(), b.Bytes())
		}
	})

	t.Run("overflow drains without error and caps allocation", func(t *testing.T) {
		const limit = 16
		b := newHeadBuffer(limit)
		payload := bytes.Repeat([]byte("abcdefghij"), 100) // 1000 bytes
		for off := 0; off < len(payload); off += 7 {
			end := min(off+7, len(payload))
			n, err := b.Write(payload[off:end])
			if err != nil || n != end-off {
				t.Fatalf("Write(%d) = (%d, %v)", end-off, n, err)
			}
		}
		if !b.Truncated() {
			t.Fatal("Truncated() = false, want true")
		}
		if b.Total() != int64(len(payload)) {
			t.Fatalf("Total() = %d, want %d", b.Total(), len(payload))
		}
		if !bytes.Equal(b.Bytes(), payload[:limit]) {
			t.Fatalf("Bytes() = %q, want %q", b.Bytes(), payload[:limit])
		}
		if cap(b.Bytes()) > limit {
			t.Fatalf("cap(Bytes()) = %d > limit %d", cap(b.Bytes()), limit)
		}
	})

	t.Run("zero limit drains all", func(t *testing.T) {
		b := newHeadBuffer(0)
		n, err := b.Write([]byte("abc"))
		if err != nil || n != 3 {
			t.Fatalf("Write = (%d, %v), want (3, nil)", n, err)
		}
		if !b.Truncated() || b.Total() != 3 || len(b.Bytes()) != 0 {
			t.Fatalf("state = truncated:%v total:%d bytes:%q", b.Truncated(), b.Total(), b.Bytes())
		}
	})
}

func TestTailBufferBoundsAndRetainsLatest(t *testing.T) {
	t.Run("under and exact limit", func(t *testing.T) {
		b := newTailBuffer(8)
		_, _ = b.Write([]byte("abcd"))
		_, _ = b.Write([]byte("efgh"))
		if b.Truncated() || b.Total() != 8 || string(b.Bytes()) != "abcdefgh" {
			t.Fatalf("state = truncated:%v total:%d bytes:%q", b.Truncated(), b.Total(), b.Bytes())
		}
	})

	t.Run("ring retains exact trailing bytes across varied chunk sizes", func(t *testing.T) {
		for _, limit := range []int{1, 5, 13, 64} {
			b := newTailBuffer(limit)
			var full bytes.Buffer
			chunkSizes := []int{1, 2, 3, 4, 7, 11, 17, 29, 3, 1, 5, 19, 100}
			ch := byte('a')
			for _, sz := range chunkSizes {
				chunk := make([]byte, sz)
				for i := range chunk {
					chunk[i] = ch
					ch = 'a' + (ch-'a'+1)%26
				}
				n, err := b.Write(chunk)
				if err != nil || n != sz {
					t.Fatalf("limit=%d Write(%d) = (%d, %v)", limit, sz, n, err)
				}
				full.Write(chunk)
				all := full.Bytes()
				want := all
				wantTrunc := false
				if len(all) > limit {
					want = all[len(all)-limit:]
					wantTrunc = true
				}
				if b.Truncated() != wantTrunc {
					t.Fatalf("limit=%d total=%d Truncated()=%v, want %v", limit, len(all), b.Truncated(), wantTrunc)
				}
				if b.Total() != int64(len(all)) {
					t.Fatalf("limit=%d Total()=%d, want %d", limit, b.Total(), len(all))
				}
				if got := b.Bytes(); !bytes.Equal(got, want) {
					t.Fatalf("limit=%d total=%d Bytes()=%q, want %q", limit, len(all), got, want)
				}
			}
		}
	})

	t.Run("single write larger than limit", func(t *testing.T) {
		b := newTailBuffer(5)
		n, err := b.Write([]byte("0123456789"))
		if err != nil || n != 10 {
			t.Fatalf("Write = (%d, %v), want (10, nil)", n, err)
		}
		if !b.Truncated() || b.Total() != 10 || string(b.Bytes()) != "56789" {
			t.Fatalf("state = truncated:%v total:%d bytes:%q", b.Truncated(), b.Total(), b.Bytes())
		}
	})

	t.Run("zero limit drains all", func(t *testing.T) {
		b := newTailBuffer(0)
		n, err := b.Write([]byte("abc"))
		if err != nil || n != 3 {
			t.Fatalf("Write = (%d, %v), want (3, nil)", n, err)
		}
		if !b.Truncated() || b.Total() != 3 || len(b.Bytes()) != 0 {
			t.Fatalf("state = truncated:%v total:%d bytes:%q", b.Truncated(), b.Total(), b.Bytes())
		}
	})
}

func makeFloodStub(t *testing.T, script string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX shell fixture")
	}
	path := filepath.Join(t.TempDir(), "python3-flood-stub.sh")
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("write stub: %v", err)
	}
	return path
}

// TestRunStdoutLimitDrainsWithoutDeadlockAndErrors verifies that a subprocess
// emitting far more than the OS pipe buffer and MaxStdoutBytes is drained
// without stalling, stays capped in memory, and fails with ErrStdoutLimitExceeded.
func TestRunStdoutLimitDrainsWithoutDeadlockAndErrors(t *testing.T) {
	// Write 256 KiB (> 64 KiB pipe buffer) starting with a known marker.
	stub := makeFloodStub(t, `#!/bin/sh
printf 'HEAD_MARKER:'
dd if=/dev/zero bs=4096 count=64 2>/dev/null | tr '\0' 'X'
exit "${STUB_EXIT_CODE:-0}"
`)
	cache := stageCache(t)

	for _, tc := range []struct {
		name     string
		exitCode string
		wantCode int
	}{
		{"clean exit overflow", "0", 0},
		{"non-zero exit overflow", "2", 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("STUB_EXIT_CODE", tc.exitCode)
			started := time.Now()
			res, err := Run(context.Background(), RunOptions{
				PythonPath:     stub,
				CacheDir:       cache,
				Timeout:        5 * time.Second,
				MaxStdoutBytes: 1024,
			})
			if time.Since(started) > 3*time.Second {
				t.Fatalf("Run took %s; oversized stdout may have stalled pipe", time.Since(started))
			}
			if !errors.Is(err, ErrStdoutLimitExceeded) {
				t.Fatalf("Run error = %v, want ErrStdoutLimitExceeded", err)
			}
			if res == nil {
				t.Fatal("Run result is nil; want bounded result alongside overflow error")
			}
			if !res.StdoutTruncated {
				t.Fatal("StdoutTruncated = false, want true")
			}
			if len(res.Stdout) != 1024 {
				t.Fatalf("len(Stdout) = %d, want 1024", len(res.Stdout))
			}
			if !strings.HasPrefix(string(res.Stdout), "HEAD_MARKER:XXXX") {
				t.Fatalf("Stdout prefix = %q, want HEAD_MARKER:XXXX...", res.Stdout[:24])
			}
			if res.ExitCode != tc.wantCode {
				t.Fatalf("ExitCode = %d, want %d", res.ExitCode, tc.wantCode)
			}
			if tc.wantCode != 0 && !strings.Contains(err.Error(), "exit code 2") {
				t.Fatalf("error %q did not preserve non-zero exit code", err)
			}
		})
	}
}

// TestRunStderrLimitRetainsTailWithoutFailingCleanRun verifies that verbose
// stderr exceeding MaxStderrBytes is drained without stalling, keeps the
// trailing diagnostics (including strict-exit receipts), and does not turn a
// healthy exit-0 run into an error.
func TestRunStderrLimitRetainsTailWithoutFailingCleanRun(t *testing.T) {
	stub := makeFloodStub(t, `#!/bin/sh
printf 'clean report\n'
dd if=/dev/zero bs=4096 count=64 2>/dev/null | tr '\0' 'E' >&2
printf '\n[last30days] strict-exit: degraded sources: x\nFINAL_MARKER\n' >&2
exit "${STUB_EXIT_CODE:-0}"
`)
	cache := stageCache(t)

	t.Run("exit 0 succeeds with bounded stderr tail", func(t *testing.T) {
		t.Setenv("STUB_EXIT_CODE", "0")
		res, err := Run(context.Background(), RunOptions{
			PythonPath:     stub,
			CacheDir:       cache,
			Timeout:        5 * time.Second,
			MaxStderrBytes: 1024,
		})
		if err != nil {
			t.Fatalf("Run: %v", err)
		}
		if string(res.Stdout) != "clean report\n" || res.StdoutTruncated {
			t.Fatalf("stdout = %q (truncated=%v), want clean report", res.Stdout, res.StdoutTruncated)
		}
		if !res.StderrTruncated {
			t.Fatal("StderrTruncated = false, want true")
		}
		if len(res.Stderr) != 1024 {
			t.Fatalf("len(Stderr) = %d, want 1024", len(res.Stderr))
		}
		if !strings.HasSuffix(string(res.Stderr), "[last30days] strict-exit: degraded sources: x\nFINAL_MARKER\n") {
			t.Fatalf("stderr tail lost final markers: %q", res.Stderr[len(res.Stderr)-80:])
		}
	})

	t.Run("exit 3 preserves strict-exit receipt in bounded stderr tail", func(t *testing.T) {
		t.Setenv("STUB_EXIT_CODE", "3")
		res, err := Run(context.Background(), RunOptions{
			PythonPath:     stub,
			CacheDir:       cache,
			Timeout:        5 * time.Second,
			MaxStderrBytes: 1024,
		})
		if err == nil {
			t.Fatal("expected error for exit code 3")
		}
		if res == nil || res.ExitCode != 3 || !res.StderrTruncated || len(res.Stderr) != 1024 {
			t.Fatalf("unexpected result: %+v", res)
		}
		if !strings.Contains(string(res.Stderr), "[last30days] strict-exit: degraded sources: x") {
			t.Fatalf("stderr tail lost strict-exit receipt: %q", res.Stderr)
		}
	})
}
