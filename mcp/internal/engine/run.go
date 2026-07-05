package engine

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// DefaultPythonBinary is the interpreter we look up unless RunOptions
// overrides it. Windows installs may expose only "python"; we surface a
// clear error in that case rather than silently picking the wrong binary.
const DefaultPythonBinary = "python3"

// MinPythonVersion mirrors the engine's MIN_PYTHON constant in
// last30days.py. Surfaced in errors so users know what they're missing.
const MinPythonVersion = "3.12"

// PythonInstallURL is included in the missing-interpreter error so users
// have a direct route from the failure to a fix.
const PythonInstallURL = "https://www.python.org/downloads/"

// DefaultTimeout caps a single research subprocess. The engine's deep mode
// can run several minutes; five minutes is a safe upper bound that still
// fails fast when something hangs.
const DefaultTimeout = 5 * time.Minute

// TimeoutEnvOverride lets operators override DefaultTimeout per install
// (seconds, integer). Honored by Run when RunOptions.Timeout is zero.
const TimeoutEnvOverride = "LAST30DAYS_MCP_TIMEOUT"

// RunOptions configures one invocation of the embedded Python engine.
// PythonPath is exposed so tests can substitute a stub interpreter without
// manipulating the process PATH.
type RunOptions struct {
	PythonPath string        // resolved python3 binary; empty means look up DefaultPythonBinary on PATH
	CacheDir   string        // engine.Ensure result; lib/ here is added to PYTHONPATH
	Args       []string      // arguments after last30days.py (topic, --emit=..., etc.)
	ExtraEnv   []string      // appended to os.Environ() for the child process
	Timeout    time.Duration // zero means DefaultTimeout or TimeoutEnvOverride
}

// RunResult captures the engine's full output. Stdout is what we surface to
// the agent; Stderr is included in error messages so users can diagnose
// engine failures without leaving Claude Desktop.
type RunResult struct {
	Stdout   []byte
	Stderr   []byte
	ExitCode int
	TimedOut bool
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
	pythonPath, err := resolvePython(opts.PythonPath)
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
	cmd := exec.CommandContext(subCtx, pythonPath, args...)
	cmd.Env = buildEnv(opts.CacheDir, opts.ExtraEnv)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err = cmd.Run()
	res := &RunResult{
		Stdout:   stdout.Bytes(),
		Stderr:   stderr.Bytes(),
		ExitCode: 0,
		TimedOut: errors.Is(subCtx.Err(), context.DeadlineExceeded),
	}
	if err == nil {
		return res, nil
	}

	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		res.ExitCode = exitErr.ExitCode()
		if res.TimedOut {
			return res, fmt.Errorf("engine: subprocess exceeded %s timeout", timeout)
		}
		return res, fmt.Errorf("engine: subprocess exited with code %d", res.ExitCode)
	}
	return res, fmt.Errorf("engine: subprocess failed to start: %w", err)
}

// resolvePython returns an absolute path to the interpreter or an error
// naming the install URL. If the caller supplied a path we trust it - tests
// rely on this to inject a stub. Otherwise we look up python3 on PATH.
func resolvePython(override string) (string, error) {
	if override != "" {
		return override, nil
	}
	path, err := exec.LookPath(DefaultPythonBinary)
	if err == nil {
		return path, nil
	}
	return "", fmt.Errorf(
		"engine: %s not found on PATH (need Python %s+, install from %s; current GOOS=%s)",
		DefaultPythonBinary, MinPythonVersion, PythonInstallURL, runtime.GOOS,
	)
}

func resolveTimeout(explicit time.Duration) time.Duration {
	if explicit > 0 {
		return explicit
	}
	if raw := os.Getenv(TimeoutEnvOverride); raw != "" {
		if d, err := time.ParseDuration(raw); err == nil && d > 0 {
			return d
		}
	}
	return DefaultTimeout
}

// buildEnv stitches PYTHONPATH onto os.Environ + ExtraEnv. Any pre-existing
// PYTHONPATH in the parent environment is dropped before appending the
// cache dir; otherwise the child sees two PYTHONPATH= entries and POSIX
// getenv returns the first one, so the user's value wins and the engine's
// `from lib import ...` fails with ModuleNotFoundError. The engine is
// self-contained and does not need the user's Python module search path.
func buildEnv(cacheDir string, extra []string) []string {
	const pyKey = "PYTHONPATH="
	parent := os.Environ()
	base := make([]string, 0, len(parent)+1+len(extra))
	for _, kv := range parent {
		if strings.HasPrefix(kv, pyKey) {
			continue
		}
		base = append(base, kv)
	}
	base = append(base, pyKey+cacheDir)
	base = append(base, extra...)
	return base
}
