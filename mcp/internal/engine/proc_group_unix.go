//go:build !windows

package engine

import (
	"context"
	"errors"
	"os/exec"
	"syscall"
	"time"
)

var errWaitNoReapUnsupported = errors.New("wait without reap unsupported")

// Overridable seams for deterministic process-lifecycle ordering tests.
var (
	signalProcessGroupFunc = func(pid int, sig syscall.Signal) error {
		return syscall.Kill(-pid, sig)
	}
	waitChildNoReapFunc = waitChildNoReap
	waitCmdFunc         = func(cmd *exec.Cmd) error {
		return cmd.Wait()
	}
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
	_ = signalProcessGroupFunc(cmd.Process.Pid, syscall.SIGTERM)
}

// killProcessGroup SIGKILLs the whole engine process group (negative pid =
// group). ESRCH and other errors are intentionally ignored: the group may
// already be gone when the deadline fires.
func killProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = signalProcessGroupFunc(cmd.Process.Pid, syscall.SIGKILL)
}

// waitCommand monitors cmd until it exits or ctx ends, ensuring all
// process-group signals are delivered while the child leader is still unreaped
// so the OS cannot recycle its PID/PGID into an unrelated process group.
func waitCommand(ctx context.Context, cmd *exec.Cmd, grace time.Duration) error {
	if cmd.Process == nil || cmd.Process.Pid <= 0 {
		return waitCmdFunc(cmd)
	}
	pid := cmd.Process.Pid
	exitedCh := make(chan error, 1)
	go func() {
		exitedCh <- waitChildNoReapFunc(pid)
	}()

	select {
	case noReapErr := <-exitedCh:
		// Only noReapErr == nil guarantees the leader is an unreaped zombie;
		// any error falls back so context timeout/cancel still kills the child.
		if noReapErr != nil {
			return waitCommandFallback(ctx, cmd, grace)
		}
		return waitCmdFunc(cmd)

	case <-ctx.Done():
		// Leader is not yet reaped, so its PID/PGID cannot be recycled while
		// SIGTERM and any SIGKILL backstop execute.
		termProcessGroup(cmd)

		timer := time.NewTimer(grace)
		defer timer.Stop()

		select {
		case noReapErr := <-exitedCh:
			if noReapErr != nil {
				return waitCommandFallbackAfterTerm(cmd, grace)
			}
			// Leader exited during grace and remains an unreaped zombie;
			// SIGKILL any remaining in-group stragglers before reaping.
			killProcessGroup(cmd)
			return waitCmdFunc(cmd)

		case <-timer.C:
			killProcessGroup(cmd)
			<-exitedCh
			return waitCmdFunc(cmd)
		}
	}
}

func waitCommandFallback(ctx context.Context, cmd *exec.Cmd, grace time.Duration) error {
	waitCh := make(chan error, 1)
	go func() { waitCh <- waitCmdFunc(cmd) }()

	select {
	case err := <-waitCh:
		return err
	case <-ctx.Done():
		select {
		case err := <-waitCh:
			return err
		default:
		}
		termProcessGroup(cmd)
		return waitCommandFallbackAfterTermCh(cmd, waitCh, grace)
	}
}

func waitCommandFallbackAfterTerm(cmd *exec.Cmd, grace time.Duration) error {
	waitCh := make(chan error, 1)
	go func() { waitCh <- waitCmdFunc(cmd) }()
	return waitCommandFallbackAfterTermCh(cmd, waitCh, grace)
}

func waitCommandFallbackAfterTermCh(cmd *exec.Cmd, waitCh <-chan error, grace time.Duration) error {
	timer := time.NewTimer(grace)
	defer timer.Stop()

	select {
	case err := <-waitCh:
		// Never signal -pid after cmd.Wait() has reaped the leader.
		return err
	case <-timer.C:
		select {
		case err := <-waitCh:
			return err
		default:
		}
		killProcessGroup(cmd)
		return <-waitCh
	}
}
