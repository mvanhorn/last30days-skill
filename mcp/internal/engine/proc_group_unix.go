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
	signalDirectChildFunc = func(cmd *exec.Cmd, sig syscall.Signal) error {
		if cmd.Process == nil {
			return nil
		}
		if sig == syscall.SIGKILL {
			return cmd.Process.Kill()
		}
		return cmd.Process.Signal(sig)
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
	return waitCommandWithDrain(ctx, cmd, grace, pipeGracePeriod, nil, nil)
}

// waitCommandWithDrain coordinates leader exit watching, output-pipe draining,
// context cancellation, and leader reaping. Because stdout/stderr are explicit
// *os.File pipes, cmd.Wait() only reaps the leader; keeping cmd.Wait() last
// keeps the leader's PID/PGID pinned throughout draining so same-group helpers
// holding pipes open are still cleaned up if ctx cancels or pipeGrace elapses.
func waitCommandWithDrain(
	ctx context.Context,
	cmd *exec.Cmd,
	grace, pipeGrace time.Duration,
	drainDone <-chan struct{},
	closeReadPipes func(),
) error {
	drainDone, closeReadPipes = normalizeDrain(drainDone, closeReadPipes)
	if cmd.Process == nil || cmd.Process.Pid <= 0 {
		err := waitCmdFunc(cmd)
		finishDrain(drainDone, closeReadPipes, pipeGrace)
		return err
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
			return waitCommandFallback(ctx, cmd, grace, pipeGrace, drainDone, closeReadPipes)
		}
		// Leader exited while still unreaped. Keep it pinned as a zombie until
		// output pipes finish draining so cancellation or pipeGrace expiration
		// can still safely signal same-group helpers holding stdout/stderr open.
		return drainWhileLeaderUnreaped(ctx, cmd, grace, pipeGrace, drainDone, closeReadPipes)

	case <-ctx.Done():
		// Leader is not yet reaped, so its PID/PGID cannot be recycled while
		// SIGTERM and any SIGKILL backstop execute.
		termProcessGroup(cmd)

		timer := time.NewTimer(grace)
		defer timer.Stop()

		select {
		case noReapErr := <-exitedCh:
			if noReapErr != nil {
				return waitCommandFallbackAfterTerm(cmd, grace, pipeGrace, drainDone, closeReadPipes)
			}
			// Leader exited during grace and remains an unreaped zombie;
			// SIGKILL any remaining in-group stragglers before reaping.
			killProcessGroup(cmd)
			err := waitCmdFunc(cmd)
			finishDrain(drainDone, closeReadPipes, pipeGrace)
			return err

		case <-timer.C:
			killProcessGroup(cmd)
			<-exitedCh
			err := waitCmdFunc(cmd)
			finishDrain(drainDone, closeReadPipes, pipeGrace)
			return err
		}
	}
}

func drainWhileLeaderUnreaped(
	ctx context.Context,
	cmd *exec.Cmd,
	grace, pipeGrace time.Duration,
	drainDone <-chan struct{},
	closeReadPipes func(),
) error {
	if ctx.Err() != nil {
		killRemainingGroupForDrain(cmd, grace, drainDone)
		err := waitCmdFunc(cmd)
		finishDrain(drainDone, closeReadPipes, postKillDrainGrace(pipeGrace))
		return err
	}
	select {
	case <-drainDone:
		return waitCmdFunc(cmd)
	default:
	}

	var pipeTimerC <-chan time.Time
	if pipeGrace > 0 {
		pipeTimer := time.NewTimer(pipeGrace)
		defer pipeTimer.Stop()
		pipeTimerC = pipeTimer.C
	}

	select {
	case <-drainDone:
		return waitCmdFunc(cmd)

	case <-ctx.Done():
		// Leader is still an unreaped zombie; SIGTERM then SIGKILL any
		// same-group helpers holding pipes open before reaping.
		killRemainingGroupForDrain(cmd, grace, drainDone)
		err := waitCmdFunc(cmd)
		finishDrain(drainDone, closeReadPipes, postKillDrainGrace(pipeGrace))
		return err

	case <-pipeTimerC:
		// Leader exited normally, but a descendant still holds pipes open past
		// pipeGrace. Clean up any same-group helpers before reaping, then cut
		// off any remaining out-of-group pipe holders.
		killRemainingGroupForDrain(cmd, grace, drainDone)
		err := waitCmdFunc(cmd)
		finishDrain(drainDone, closeReadPipes, postKillDrainGrace(pipeGrace))
		if err == nil {
			return exec.ErrWaitDelay
		}
		return err
	}
}

func killRemainingGroupForDrain(cmd *exec.Cmd, grace time.Duration, drainDone <-chan struct{}) {
	termProcessGroup(cmd)
	timer := time.NewTimer(grace)
	defer timer.Stop()
	select {
	case <-drainDone:
	case <-timer.C:
	}
	killProcessGroup(cmd)
}

func postKillDrainGrace(pipeGrace time.Duration) time.Duration {
	const maxPostKillDrain = 200 * time.Millisecond
	if pipeGrace <= 0 || pipeGrace > maxPostKillDrain {
		return maxPostKillDrain
	}
	return pipeGrace
}

// waitCommandFallback is used only when waitChildNoReap cannot pin the child
// leader unreaped. Because cmd.Wait() runs concurrently, it avoids raw numeric
// -pid group signals and signals only the direct child through os.Process,
// which Go synchronizes internally against PID reuse during reaping.
func waitCommandFallback(
	ctx context.Context,
	cmd *exec.Cmd,
	grace, pipeGrace time.Duration,
	drainDone <-chan struct{},
	closeReadPipes func(),
) error {
	waitCh := make(chan error, 1)
	go func() { waitCh <- waitCmdFunc(cmd) }()

	select {
	case err := <-waitCh:
		drainTimedOut := finishDrain(drainDone, closeReadPipes, pipeGrace)
		if err == nil && drainTimedOut {
			return exec.ErrWaitDelay
		}
		return err
	case <-ctx.Done():
		_ = signalDirectChildFunc(cmd, syscall.SIGTERM)
		return waitCommandFallbackAfterTermCh(cmd, waitCh, grace, pipeGrace, drainDone, closeReadPipes)
	}
}

func waitCommandFallbackAfterTerm(
	cmd *exec.Cmd,
	grace, pipeGrace time.Duration,
	drainDone <-chan struct{},
	closeReadPipes func(),
) error {
	waitCh := make(chan error, 1)
	go func() { waitCh <- waitCmdFunc(cmd) }()
	return waitCommandFallbackAfterTermCh(cmd, waitCh, grace, pipeGrace, drainDone, closeReadPipes)
}

func waitCommandFallbackAfterTermCh(
	cmd *exec.Cmd,
	waitCh <-chan error,
	grace, pipeGrace time.Duration,
	drainDone <-chan struct{},
	closeReadPipes func(),
) error {
	timer := time.NewTimer(grace)
	defer timer.Stop()

	var err error
	select {
	case err = <-waitCh:
	case <-timer.C:
		_ = signalDirectChildFunc(cmd, syscall.SIGKILL)
		err = <-waitCh
	}
	finishDrain(drainDone, closeReadPipes, pipeGrace)
	return err
}
