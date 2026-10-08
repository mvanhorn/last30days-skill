//go:build !windows

package engine

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestRunParentCancellationIsNotSuccess(t *testing.T) {
	dir := t.TempDir()
	ready := filepath.Join(dir, "ready")
	stub := filepath.Join(dir, "python3-cancel-stub.sh")
	script := `#!/bin/sh
trap 'printf "canceled cleanly\n" >&2; exit 0' TERM
printf 'partial research\n'
: > "$RUN_CANCEL_READY"
while :; do sleep 1; done
`
	if err := os.WriteFile(stub, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	readyCh := make(chan error, 1)
	go func() {
		deadline := time.NewTimer(3 * time.Second)
		defer deadline.Stop()
		tick := time.NewTicker(10 * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-tick.C:
				if _, err := os.Stat(ready); err == nil {
					readyCh <- nil
					cancel()
					return
				}
			case <-deadline.C:
				readyCh <- errors.New("child did not install its SIGTERM handler")
				cancel()
				return
			}
		}
	}()

	started := time.Now()
	res, err := Run(ctx, RunOptions{
		PythonPath: stub,
		CacheDir:   stageCache(t),
		Timeout:    10 * time.Second,
		ExtraEnv:   []string{"RUN_CANCEL_READY=" + ready},
	})
	if readyErr := <-readyCh; readyErr != nil {
		t.Fatal(readyErr)
	}
	if time.Since(started) > 4*time.Second {
		t.Error("parent cancellation exceeded the bounded shutdown grace")
	}
	if !errors.Is(err, context.Canceled) {
		t.Errorf("Run error = %v, want context.Canceled after clean child exit", err)
	}
	if res == nil {
		t.Fatal("Run result is nil; want partial output alongside cancellation")
	}
	if res.ExitCode != 0 || res.TimedOut {
		t.Errorf("Run result = %+v, want clean exit without timeout", res)
	}
	if string(res.Stdout) != "partial research\n" || !strings.Contains(string(res.Stderr), "canceled cleanly") {
		t.Errorf("partial output lost: stdout=%q stderr=%q", res.Stdout, res.Stderr)
	}
}

func TestRunTimeoutBoundsInheritedPipes(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"signal-exit", "clean-exit", "clean-exit-without-child", "term-ignoring-in-group"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			pidFile := filepath.Join(dir, "detached.pid")
			stub := filepath.Join(dir, "python3-detached-stub.sh")
			if err := os.WriteFile(stub, []byte("#!/bin/sh\nexec \"$CR012_TEST_BINARY\" -test.run='^TestRunDetachedPipeHelper$'\n"), 0o755); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				if raw, err := os.ReadFile(pidFile); err == nil {
					if pid, err := strconv.Atoi(string(raw)); err == nil && pid > 0 {
						_ = syscall.Kill(pid, syscall.SIGKILL)
					}
				}
			})

			started := time.Now()
			res, err := Run(context.Background(), RunOptions{
				PythonPath: stub,
				CacheDir:   stageCache(t),
				Timeout:    200 * time.Millisecond,
				ExtraEnv: []string{
					"CR012_TEST_BINARY=" + executable,
					"CR012_HELPER_MODE=" + mode,
					"CR012_PID_FILE=" + pidFile,
				},
			})
			elapsed := time.Since(started)
			if elapsed > 4*time.Second {
				t.Errorf("Run took %s for a 200ms timeout; detached pipes must not outlive the bounded shutdown grace", elapsed)
			}
			if err == nil || !strings.Contains(err.Error(), "timeout") {
				t.Errorf("Run error = %v, want timeout", err)
			}
			if res == nil || !res.TimedOut {
				t.Fatalf("Run result = %+v, want TimedOut", res)
			}
			if mode != "clean-exit-without-child" && (!strings.Contains(string(res.Stdout), "detached stdout") || !strings.Contains(string(res.Stderr), "detached stderr")) {
				t.Fatalf("detached process did not inherit both pipes: stdout=%q stderr=%q", res.Stdout, res.Stderr)
			}
			if mode == "term-ignoring-in-group" {
				assertPidDead(t, pidFile, "TERM-ignoring in-group grandchild")
			}
		})
	}
}

func TestRunDetachedPipeHelper(t *testing.T) {
	mode := os.Getenv("CR012_HELPER_MODE")
	if mode == "" {
		t.Skip("subprocess fixture")
	}
	if mode == "pipe-holder" || mode == "pipe-holder-ignore-term" {
		if mode == "pipe-holder-ignore-term" {
			signal.Ignore(syscall.SIGTERM)
		}
		_, _ = os.Stdout.WriteString("detached stdout\n")
		_, _ = os.Stderr.WriteString("detached stderr\n")
		time.Sleep(8 * time.Second)
		os.Exit(0)
	}
	if mode == "clean-exit" || mode == "clean-exit-without-child" {
		terminated := make(chan os.Signal, 1)
		signal.Notify(terminated, syscall.SIGTERM)
		go func() {
			<-terminated
			os.Exit(0)
		}()
	}
	if mode == "clean-exit-without-child" {
		time.Sleep(8 * time.Second)
		os.Exit(0)
	}
	child := exec.Command(os.Args[0], "-test.run=^TestRunDetachedPipeHelper$")
	if mode == "term-ignoring-in-group" {
		child.Env = append(os.Environ(), "CR012_HELPER_MODE=pipe-holder-ignore-term")
	} else {
		child.Env = append(os.Environ(), "CR012_HELPER_MODE=pipe-holder")
		child.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	}
	child.Stdout = os.Stdout
	child.Stderr = os.Stderr
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(os.Getenv("CR012_PID_FILE"), []byte(strconv.Itoa(child.Process.Pid)), 0o600); err != nil {
		_ = child.Process.Kill()
		t.Fatal(err)
	}
	time.Sleep(8 * time.Second)
	os.Exit(0)
}

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
