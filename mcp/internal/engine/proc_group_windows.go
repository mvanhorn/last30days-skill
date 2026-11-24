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
	return waitCommandWithDrain(ctx, cmd, grace, pipeGracePeriod, nil, nil)
}

func waitCommandWithDrain(
	ctx context.Context,
	cmd *exec.Cmd,
	grace, pipeGrace time.Duration,
	drainDone <-chan struct{},
	closeReadPipes func(),
) error {
	drainDone, closeReadPipes = normalizeDrain(drainDone, closeReadPipes)
	waitCh := make(chan error, 1)
	go func() { waitCh <- cmd.Wait() }()

	select {
	case err := <-waitCh:
		drainTimedOut := finishDrain(drainDone, closeReadPipes, pipeGrace)
		if err == nil && drainTimedOut {
			return exec.ErrWaitDelay
		}
		return err
	case <-ctx.Done():
		termProcessGroup(cmd)
		timer := time.NewTimer(grace)
		defer timer.Stop()
		var err error
		select {
		case err = <-waitCh:
		case <-timer.C:
			killProcessGroup(cmd)
			err = <-waitCh
		}
		finishDrain(drainDone, closeReadPipes, pipeGrace)
		return err
	}
}
