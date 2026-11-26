package engine

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// DefaultPythonBinary is preferred when its version meets MinPythonVersion.
const DefaultPythonBinary = "python3"

// MinPythonVersion mirrors the engine's MIN_PYTHON constant in
// last30days.py. Surfaced in errors so users know what they're missing.
const MinPythonVersion = "3.12"

// PythonInstallURL is included in the missing-interpreter error so users
// have a direct route from the failure to a fix.
const PythonInstallURL = "https://www.python.org/downloads/"

// DefaultTimeout caps a single research subprocess. The engine's deep-tier
// resume enrichment alone budgets 450s, so ten minutes is the floor that lets
// a full run finish; the per-stream and X-chain deadlines inside the pipeline
// still fail fast when something hangs.
const DefaultTimeout = 10 * time.Minute

// TimeoutEnvOverride lets operators override DefaultTimeout per install
// (seconds, integer). Honored by Run when RunOptions.Timeout is zero.
const TimeoutEnvOverride = "LAST30DAYS_MCP_TIMEOUT"

// termGracePeriod bounds the SIGTERM phase of the deadline path: the
// python SIGTERM handler needs a moment to killpg() the setsid'd
// descendant groups before the SIGKILL backstop fires.
const termGracePeriod = 2 * time.Second

// pipeGracePeriod bounds output draining after the engine exits even if
// an unregistered detached descendant still holds its output pipes.
const pipeGracePeriod = time.Second

// RunOptions configures one invocation of the embedded Python engine.
// PythonPath is exposed so tests can substitute a stub interpreter without
// manipulating the process PATH.
type RunOptions struct {
	PythonPath     string        // resolved test/caller override; empty honors PythonEnvOverride, then DefaultPythonBinary
	CacheDir       string        // engine.Ensure result; lib/ here is added to PYTHONPATH
	Args           []string      // arguments after last30days.py (topic, --emit=..., etc.)
	ExtraEnv       []string      // appended to os.Environ() for the child process
	Timeout        time.Duration // zero means DefaultTimeout or TimeoutEnvOverride
	MaxStdoutBytes int           // zero means DefaultMaxStdoutBytes
	MaxStderrBytes int           // zero means DefaultMaxStderrBytes
}

// RunResult captures the engine's bounded output. Stdout is what we surface to
// the agent; Stderr is included in error messages so users can diagnose
// engine failures without leaving Claude Desktop.
type RunResult struct {
	Stdout          []byte
	Stderr          []byte
	StdoutTruncated bool
	StderrTruncated bool
	ExitCode        int
	TimedOut        bool
}

// Run shells out to python3 with last30days.py inside cacheDir. The child
// receives the parent environment (so MCPB user_config env-injection
// reaches the engine) plus ExtraEnv and a PYTHONPATH that points at the
// cache so the engine's `from lib import ...` statements resolve.
//
// A missing interpreter, a non-zero exit, and a timeout each surface as
// distinct errors so the tool handler can map them to user-facing
// messages without re-parsing stderr.
func Run(ctx context.Context, opts RunOptions) (*RunResult, error) {
	if opts.CacheDir == "" {
		return nil, errors.New("engine: CacheDir is required")
	}
	pythonPath, err := resolvePythonContext(ctx, opts.PythonPath)
	if err != nil {
		return nil, err
	}

	scriptPath := filepath.Join(opts.CacheDir, "last30days.py")
	if _, err := os.Stat(scriptPath); err != nil {
		return nil, fmt.Errorf("engine: last30days.py not found in cache %s: %w", opts.CacheDir, err)
	}

	timeout := resolveTimeout(opts.Timeout)
	subCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	args := append([]string{scriptPath}, opts.Args...)
	cmd := exec.Command(pythonPath, args...)
	cmd.Env = buildEnv(opts.CacheDir, opts.ExtraEnv)
	// Own process group so cancellation SIGTERM reaches same-group helpers on
	// the primary unreaped wait path while Python's SIGTERM handler cleans up
	// registered setsid'd descendant groups (including grok CLI via lib/subproc.py)
	// on both primary and direct-child fallback paths. exec.CommandContext would
	// SIGKILL only Python directly and skip its SIGTERM/atexit cleanup.
	setProcessGroup(cmd)

	maxStdout := resolveOutputLimit(opts.MaxStdoutBytes, DefaultMaxStdoutBytes)
	maxStderr := resolveOutputLimit(opts.MaxStderrBytes, DefaultMaxStderrBytes)
	stdout := newHeadBuffer(maxStdout)
	stderr := newTailBuffer(maxStderr)

	if err := subCtx.Err(); err != nil {
		return &RunResult{
			ExitCode: -1,
			TimedOut: errors.Is(err, context.DeadlineExceeded),
		}, fmt.Errorf("engine: subprocess not started: %w", err)
	}
	pipes, err := newOutputPipes()
	if err != nil {
		return &RunResult{
			ExitCode: -1,
			TimedOut: errors.Is(subCtx.Err(), context.DeadlineExceeded),
		}, fmt.Errorf("engine: failed to create subprocess pipes: %w", err)
	}
	cmd.Stdout = pipes.stdoutW
	cmd.Stderr = pipes.stderrW

	if err := cmd.Start(); err != nil {
		pipes.closeAll()
		res := &RunResult{
			Stdout:          stdout.Bytes(),
			Stderr:          stderr.Bytes(),
			StdoutTruncated: stdout.Truncated(),
			StderrTruncated: stderr.Truncated(),
			ExitCode:        0,
			TimedOut:        errors.Is(subCtx.Err(), context.DeadlineExceeded),
		}
		return res, fmt.Errorf("engine: subprocess failed to start: %w", err)
	}
	pipes.startDraining(stdout, stderr)

	err = waitCommandWithDrain(subCtx, cmd, termGracePeriod, pipeGracePeriod, pipes.drainDone, pipes.closeReadPipes)

	ctxErr := subCtx.Err()
	res := &RunResult{
		Stdout:          stdout.Bytes(),
		Stderr:          stderr.Bytes(),
		StdoutTruncated: stdout.Truncated(),
		StderrTruncated: stderr.Truncated(),
		ExitCode:        0,
		TimedOut:        errors.Is(ctxErr, context.DeadlineExceeded),
	}
	if cmd.ProcessState != nil {
		res.ExitCode = cmd.ProcessState.ExitCode()
	}
	if res.TimedOut {
		return res, fmt.Errorf("engine: subprocess exceeded %s timeout: %w", timeout, ctxErr)
	}
	if ctxErr != nil {
		return res, fmt.Errorf("engine: subprocess canceled: %w", ctxErr)
	}
	if res.StdoutTruncated {
		if err != nil {
			var exitErr *exec.ExitError
			if errors.As(err, &exitErr) {
				return res, fmt.Errorf("%w (%d byte limit, received %d bytes; exit code %d)", ErrStdoutLimitExceeded, maxStdout, stdout.Total(), res.ExitCode)
			}
			return res, fmt.Errorf("%w (%d byte limit, received %d bytes; %v)", ErrStdoutLimitExceeded, maxStdout, stdout.Total(), err)
		}
		return res, fmt.Errorf("%w (%d byte limit, received %d bytes)", ErrStdoutLimitExceeded, maxStdout, stdout.Total())
	}
	if err == nil {
		return res, nil
	}

	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return res, fmt.Errorf("engine: subprocess exited with code %d", res.ExitCode)
	}
	return res, fmt.Errorf("engine: subprocess failed: %w", err)
}

func resolveTimeout(explicit time.Duration) time.Duration {
	if explicit > 0 {
		return explicit
	}
	if raw := os.Getenv(TimeoutEnvOverride); raw != "" {
		if d, err := time.ParseDuration(raw); err == nil && d > 0 {
			return d
		}
		// Accept bare integer seconds (e.g. "300") as documented.
		if secs, err := strconv.Atoi(raw); err == nil && secs > 0 {
			return time.Duration(secs) * time.Second
		}
	}
	return DefaultTimeout
}

// buildEnv stitches PYTHONPATH onto os.Environ + ExtraEnv. Any pre-existing
// PYTHONPATH, PYTHONDONTWRITEBYTECODE, or key overridden by ExtraEnv in the
// parent environment is dropped before appending; otherwise the child sees
// duplicate entries and POSIX getenv returns the first one, shadowing our
// cache-dir or MCP-level overrides.
func buildEnv(cacheDir string, extra []string) []string {
	const (
		pyKey       = "PYTHONPATH="
		bytecodeKey = "PYTHONDONTWRITEBYTECODE="
	)
	overrides := make(map[string]struct{}, len(extra))
	for _, kv := range extra {
		if key, ok := envKey(kv); ok {
			overrides[key] = struct{}{}
		}
	}
	parent := os.Environ()
	base := make([]string, 0, len(parent)+2+len(extra))
	for _, kv := range parent {
		if strings.HasPrefix(kv, pyKey) || strings.HasPrefix(kv, bytecodeKey) {
			continue
		}
		if key, ok := envKey(kv); ok {
			if _, replaced := overrides[key]; replaced {
				continue
			}
		}
		base = append(base, kv)
	}
	base = append(base, pyKey+cacheDir, bytecodeKey+"1")
	base = append(base, extra...)
	return base
}

func envKey(kv string) (string, bool) {
	eq := strings.IndexByte(kv, '=')
	if eq <= 0 {
		return "", false
	}
	key := kv[:eq]
	if runtime.GOOS == "windows" {
		return strings.ToUpper(key), true
	}
	return key, true
}
