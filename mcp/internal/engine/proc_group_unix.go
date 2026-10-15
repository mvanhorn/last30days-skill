//go:build !windows

package engine

import (
	"os/exec"
	"syscall"
)

// setProcessGroup puts the engine child in its own process group so a
// timeout kill reaches same-group grandchildren (grok CLI). Descendants
// spawned via lib/subproc.py run in their own pgids (os.setsid) and are
// unreachable by kill(-pid); they die via the engine's SIGTERM handler,
// which killpg()s each registered child group. Mirrors lib/subproc.py's
// os.setsid discipline.
func setProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

// termProcessGroup SIGTERMs the whole engine process group (negative pid =
// group). This runs the python SIGTERM handler, which cleans the setsid'd
// descendant groups before the SIGKILL backstop. Errors are ignored: the
// group may already be gone when the deadline fires.
func termProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
}

// killProcessGroup SIGKILLs the whole engine process group (negative pid =
// group). ESRCH and other errors are intentionally ignored: the group may
// already be gone when the deadline fires.
func killProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
}
