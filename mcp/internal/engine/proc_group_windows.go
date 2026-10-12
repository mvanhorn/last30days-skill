//go:build windows

package engine

import (
	"os/exec"
)

// setProcessGroup is a no-op on Windows: syscall.SysProcAttr has no Setpgid
// field and process groups are a POSIX concept. The timeout path falls back
// to killing the direct child.
func setProcessGroup(cmd *exec.Cmd) {
}

// killProcessGroup kills only the direct child on Windows. TerminateProcess
// does not run Python signal or atexit handlers, so descendant cleanup is
// not guaranteed on this platform.
func killProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = cmd.Process.Kill()
}

// termProcessGroup kills the direct child on Windows: there is no
// SIGTERM equivalent, so the TERM and KILL phases collapse into the same
// action and the grace wait in Run is skipped on prompt exit.
func termProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = cmd.Process.Kill()
}
