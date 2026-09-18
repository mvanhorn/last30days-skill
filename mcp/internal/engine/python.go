package engine

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// PythonEnvOverride selects an interpreter without changing the host PATH.
const PythonEnvOverride = "LAST30DAYS_PYTHON"

func resolvePython(override string) (string, error) {
	return resolvePythonContext(context.Background(), override)
}

func resolvePythonContext(ctx context.Context, override string) (string, error) {
	// Preserve the trusted injection seam used by embedded callers and tests.
	if override != "" {
		return override, nil
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if candidate := os.Getenv(PythonEnvOverride); candidate != "" {
		path, err := exec.LookPath(candidate)
		if err == nil && !filepath.IsAbs(path) {
			err = fmt.Errorf("relative executable paths are not allowed")
		}
		if err == nil {
			err = checkPythonVersion(ctx, path)
		}
		if err != nil {
			return "", fmt.Errorf("engine: %s=%q: %w; need Python %s+, install from %s", PythonEnvOverride, candidate, err, MinPythonVersion, PythonInstallURL)
		}
		return path, nil
	}
	candidates := pythonCandidates(os.Getenv("PATH"))
	var failures []string
	for _, candidate := range candidates {
		if ctx.Err() != nil {
			break
		}
		path, err := exec.LookPath(candidate)
		if err == nil {
			err = checkPythonVersion(ctx, path)
		}
		if err == nil {
			return path, nil
		}
		failures = append(failures, fmt.Sprintf("%s: %v", candidate, err))
	}
	if ctx.Err() != nil {
		failures = append(failures, ctx.Err().Error())
	}
	return "", fmt.Errorf("engine: no compatible %s on PATH (need Python %s+; set %s to a compatible executable, or install from %s); checked: %s", DefaultPythonBinary, MinPythonVersion, PythonEnvOverride, PythonInstallURL, strings.Join(failures, "; "))
}

// Keep PATH precedence, prefer python3 in each directory, then versioned names.
// Discover version suffixes rather than freezing the list at today's release.
// Empty and relative PATH entries are excluded even with GODEBUG=execerrdot=0.
func pythonCandidates(pathValue string) []string {
	var candidates []string
	seen := map[string]bool{}
	for _, dir := range filepath.SplitList(pathValue) {
		if !filepath.IsAbs(dir) {
			continue
		}
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		var names []string
		for _, entry := range entries {
			if entry.IsDir() {
				continue
			}
			name := entry.Name()
			stem := strings.TrimSuffix(name, ".exe")
			if stem == "python3" || stem == "python" {
				names = append(names, name)
				continue
			}
			if strings.HasPrefix(stem, "python3.") {
				minor, err := strconv.Atoi(strings.TrimPrefix(stem, "python3."))
				if err == nil && minor >= 12 {
					names = append(names, name)
				}
			}
		}
		sort.SliceStable(names, func(i, j int) bool {
			rank := func(name string) int {
				stem := strings.TrimSuffix(name, ".exe")
				if stem == "python3" {
					return 1000000
				}
				if stem == "python" {
					return -1
				}
				minor, _ := strconv.Atoi(strings.TrimPrefix(stem, "python3."))
				return minor
			}
			return rank(names[i]) > rank(names[j])
		})
		for _, name := range names {
			candidate := filepath.Join(dir, name)
			if !seen[candidate] {
				candidates = append(candidates, candidate)
				seen[candidate] = true
			}
		}
	}
	return candidates
}

func checkPythonVersion(ctx context.Context, path string) error {
	probeCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	// Avoid site initialization and PYTHON* environment overrides during discovery.
	cmd := exec.CommandContext(probeCtx, path, "-I", "-S", "-c", "import sys; print('%d.%d' % sys.version_info[:2])")
	cmd.WaitDelay = 100 * time.Millisecond
	output, err := cmd.Output()
	if probeCtx.Err() != nil {
		return probeCtx.Err()
	}
	if err != nil {
		return fmt.Errorf("version probe failed: %w", err)
	}
	parts := strings.Split(strings.TrimSpace(string(output)), ".")
	if len(parts) != 2 {
		return fmt.Errorf("invalid Python version response")
	}
	major, majorErr := strconv.Atoi(parts[0])
	minor, minorErr := strconv.Atoi(parts[1])
	if majorErr != nil || minorErr != nil || major != 3 || minor < 12 {
		return fmt.Errorf("incompatible Python version %q", strings.TrimSpace(string(output)))
	}
	return nil
}
