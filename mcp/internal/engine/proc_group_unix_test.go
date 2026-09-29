//go:build !windows

package engine

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestSetProcessGroupSetsSetpgid guards the CR-013 fix: the engine child
// must lead its own process group so a timeout kill reaches grandchildren
// (node bird-search, yt-dlp, grok CLI) instead of SIGKILLing only the
// direct python child while its atexit SIGTERM cleanup never runs.
func TestSetProcessGroupSetsSetpgid(t *testing.T) {
	cmd := exec.Command("true")
	setProcessGroup(cmd)
	if cmd.SysProcAttr == nil {
		t.Fatal("SysProcAttr is nil; want Setpgid process-group attribute")
	}
	if !cmd.SysProcAttr.Setpgid {
		t.Fatal("SysProcAttr.Setpgid = false, want true")
	}
}

// TestRunTimeoutKillsGrandchild exercises the group-kill path end to end:
// a stub interpreter spawns a background sleep grandchild, Run hits its
// deadline, and the grandchild must be dead afterwards. With the old
// exec.CommandContext behavior only the direct child died and the
// grandchild kept running.
func TestRunTimeoutKillsGrandchild(t *testing.T) {
	dir := t.TempDir()
	stub := filepath.Join(dir, "python3-group-stub.sh")
	inGroupPid := filepath.Join(dir, "grandchild.pid")
	setsidPid := filepath.Join(dir, "setsid-grandchild.pid")
	script := `#!/usr/bin/env bash
# Grandchildren redirect their fds away from the stub's stdout/stderr:
# otherwise a surviving orphan holds Go's exec pipe open and cmd.Wait()
# blocks until the orphan exits, masking the leak as a slow pass.
sleep 30 >/dev/null 2>&1 &
echo -n "$!" > "` + inGroupPid + `"
# True shape of the engine's descendants: lib/subproc.py spawns every
# child with os.setsid, so node bird-search / yt-dlp / digg each lead
# their own pgid that kill(-enginepid) can never reach. Only the
# engine's SIGTERM handler (killpg per registered child) kills them —
# modeled here by the trap, mirroring last30days._on_sigterm.
setsid sleep 30 >/dev/null 2>&1 &
SIDPID=$!
echo -n "$SIDPID" > "` + setsidPid + `"
trap 'kill -TERM "$SIDPID" 2>/dev/null; exit 143' TERM
sleep 30
`
	if err := os.WriteFile(stub, []byte(script), 0o755); err != nil {
		t.Fatalf("write stub: %v", err)
	}
	cache := stageCache(t)

	res, err := Run(context.Background(), RunOptions{
		PythonPath: stub,
		CacheDir:   cache,
		Timeout:    500 * time.Millisecond,
	})
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if !res.TimedOut {
		t.Fatal("TimedOut = false, want true")
	}

	assertPidDead(t, inGroupPid, "in-group grandchild")
	assertPidDead(t, setsidPid, "setsid grandchild")
}

// TestTermProcessGroupSendsSigterm checks the first phase of the deadline
// path directly: a process in its own group must exit promptly after
// termProcessGroup (no SIGKILL backstop involved).
func TestTermProcessGroupSendsSigterm(t *testing.T) {
	cmd := exec.Command("sleep", "30")
	setProcessGroup(cmd)
	if err := cmd.Start(); err != nil {
		t.Fatalf("start sleep: %v", err)
	}
	termProcessGroup(cmd)
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		killProcessGroup(cmd)
		<-done
		t.Fatal("process survived SIGTERM group kill; SIGKILL backstop fired")
	}
}

// assertPidDead reads a pid from pidFile and polls until no process with
// that pid exists. A SIGKILL-only deadline path leaves the setsid
// grandchild alive (its pgid differs), so this fails without the
// SIGTERM-first discipline.
func assertPidDead(t *testing.T, pidFile, what string) {
	t.Helper()
	raw, readErr := os.ReadFile(pidFile)
	if readErr != nil {
		t.Fatalf("%s pid file missing: %v", what, readErr)
	}
	pid, convErr := strconv.Atoi(strings.TrimSpace(string(raw)))
	if convErr != nil || pid <= 0 {
		t.Fatalf("bad %s pid %q: %v", what, raw, convErr)
	}

	// Signal delivery is async; poll for the process to disappear.
	deadline := time.Now().Add(5 * time.Second)
	for {
		if kerr := syscall.Kill(pid, 0); kerr != nil {
			if !errors.Is(kerr, syscall.ESRCH) {
				t.Logf("kill(pid, 0) = %v; treating as dead", kerr)
			}
			return
		}
		if time.Now().After(deadline) {
			// Best-effort cleanup so a regression does not leak sleeps.
			_ = syscall.Kill(pid, syscall.SIGKILL)
			t.Fatalf("%s pid %d still alive 5s after timeout; group kill failed", what, pid)
		}
		time.Sleep(20 * time.Millisecond)
	}
}
