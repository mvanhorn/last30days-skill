//go:build linux

package engine

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
)

func procStatState(t *testing.T, pid int) (byte, error) {
	t.Helper()
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0, err
	}
	// Format is: "<pid> (<comm>) <state> ..."; comm may contain spaces or ')'.
	idx := strings.LastIndexByte(string(raw), ')')
	if idx < 0 || idx+2 >= len(raw) {
		return 0, fmt.Errorf("unexpected /proc/%d/stat format: %q", pid, raw)
	}
	return raw[idx+2], nil
}

// TestWaitChildNoReapKeepsZombiePinned verifies directly against Linux /proc
// that waitChildNoReap observes child exit while leaving the process as an
// unreaped zombie ('Z'), pinning its PID/PGID until cmd.Wait() runs.
func TestWaitChildNoReapKeepsZombiePinned(t *testing.T) {
	cmd := exec.Command("/bin/sh", "-c", "exit 0")
	setProcessGroup(cmd)
	if err := cmd.Start(); err != nil {
		t.Fatalf("start child: %v", err)
	}
	pid := cmd.Process.Pid

	if err := waitChildNoReap(pid); err != nil {
		_ = cmd.Wait()
		t.Fatalf("waitChildNoReap(%d): %v", pid, err)
	}

	state, err := procStatState(t, pid)
	if err != nil {
		_ = cmd.Wait()
		t.Fatalf("child pid %d vanished before cmd.Wait(): %v", pid, err)
	}
	if state != 'Z' {
		_ = cmd.Wait()
		t.Fatalf("child pid %d state = %q after waitChildNoReap, want 'Z' (unreaped zombie)", pid, state)
	}

	if err := cmd.Wait(); err != nil {
		t.Fatalf("cmd.Wait() after waitChildNoReap: %v", err)
	}
	if _, err := os.Stat(fmt.Sprintf("/proc/%d/stat", pid)); err == nil {
		t.Fatalf("child pid %d still exists in /proc after cmd.Wait() reap", pid)
	}
}
