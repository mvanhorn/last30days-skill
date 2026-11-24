//go:build windows

package engine

import (
	"context"
	"os/exec"
	"time"
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

func waitCommand(ctx context.Context, cmd *exec.Cmd, grace time.Duration) error {
	waitCh := make(chan error, 1)
	go func() { waitCh <- cmd.Wait() }()

	select {
	case err := <-waitCh:
		return err
	case <-ctx.Done():
		termProcessGroup(cmd)
		timer := time.NewTimer(grace)
		defer timer.Stop()
		select {
		case err := <-waitCh:
			return err
		case <-timer.C:
			killProcessGroup(cmd)
			return <-waitCh
		}
	}
}
