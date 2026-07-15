package engine

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sync"
)

// SentinelFilename names the file Ensure writes inside the cache directory
// after a successful extraction. Its contents are compared to the requested
// version; a match short-circuits re-extraction on subsequent calls.
const SentinelFilename = ".version"

// cacheSubdir namespaces our cache under the OS user cache directory so
// multiple printing-press-style bundles can coexist.
const cacheSubdir = "last30days-pp-mcp"

// CacheEnvOverride lets users redirect the cache directory when the default
// OS cache location is read-only (locked-down corp images, ephemeral CI
// containers). Pointed at by extract errors via the documented escape hatch.
const CacheEnvOverride = "LAST30DAYS_CACHE_DIR"

// Ensure extracts src into baseDir/last30days-pp-mcp/<version> and returns
// the cache path. If the sentinel file already records the same version the
// directory is reused without rewriting. version must be non-empty so the
// cache layout always namespaces by version.
//
// Extraction writes to a sibling .tmp directory and renames it on success
// so a partial extraction can never be mistaken for a complete one. Concurrent
// callers within the same process serialize behind a per-cache-dir sync.Once
// so the rename happens exactly once.
func Ensure(src fs.FS, baseDir, version string) (string, error) {
	if version == "" {
		return "", errors.New("engine: version is required")
	}
	cacheDir := filepath.Join(baseDir, cacheSubdir, version)

	once := getOnce(cacheDir)
	var extractErr error
	once.Do(func() {
		extractErr = ensureLocked(src, cacheDir, version)
	})
	if extractErr != nil {
		// Reset the sync.Once so a follow-up call can retry rather than
		// permanently caching the error. Retry is the right default when
		// the failure is transient (e.g., disk full, parent dir restored).
		resetOnce(cacheDir)
		return "", extractErr
	}
	return cacheDir, nil
}

// EnsureUserCache wraps Ensure with the OS user cache dir (or the
// LAST30DAYS_CACHE_DIR override) as base. Production callers use this; tests
// use Ensure with an explicit temp dir.
func EnsureUserCache(src fs.FS, version string) (string, error) {
	if override := os.Getenv(CacheEnvOverride); override != "" {
		return Ensure(src, override, version)
	}
	base, err := os.UserCacheDir()
	if err != nil {
		return "", fmt.Errorf("engine: resolve user cache dir (set %s to override): %w", CacheEnvOverride, err)
	}
	return Ensure(src, base, version)
}

func ensureLocked(src fs.FS, cacheDir, version string) error {
	if sentinelMatches(cacheDir, version) {
		return nil
	}
	tmpDir := cacheDir + ".tmp"
	if err := os.RemoveAll(tmpDir); err != nil {
		return fmt.Errorf("engine: clean tmp cache: %w", err)
	}
	if err := os.MkdirAll(tmpDir, 0o755); err != nil {
		return fmt.Errorf("engine: create tmp cache (%s, set %s to override): %w", tmpDir, CacheEnvOverride, err)
	}
	if err := extractAll(src, tmpDir); err != nil {
		_ = os.RemoveAll(tmpDir)
		return err
	}
	sentinel := filepath.Join(tmpDir, SentinelFilename)
	if err := os.WriteFile(sentinel, []byte(version), 0o644); err != nil {
		_ = os.RemoveAll(tmpDir)
		return fmt.Errorf("engine: write sentinel: %w", err)
	}
	if err := os.RemoveAll(cacheDir); err != nil {
		_ = os.RemoveAll(tmpDir)
		return fmt.Errorf("engine: clean old cache: %w", err)
	}
	if err := os.Rename(tmpDir, cacheDir); err != nil {
		_ = os.RemoveAll(tmpDir)
		return fmt.Errorf("engine: promote tmp cache: %w", err)
	}
	return nil
}

func sentinelMatches(cacheDir, version string) bool {
	data, err := os.ReadFile(filepath.Join(cacheDir, SentinelFilename))
	if err != nil {
		return false
	}
	return string(data) == version
}

func extractAll(src fs.FS, dst string) error {
	return fs.WalkDir(src, ".", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == "." {
			return nil
		}
		target := filepath.Join(dst, path)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		return copyEmbeddedFile(src, path, target)
	})
}

func copyEmbeddedFile(src fs.FS, srcPath, dst string) error {
	in, err := src.Open(srcPath)
	if err != nil {
		return fmt.Errorf("engine: open %s: %w", srcPath, err)
	}
	defer func() { _ = in.Close() }()
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return fmt.Errorf("engine: ensure parent of %s: %w", dst, err)
	}
	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return fmt.Errorf("engine: create %s: %w", dst, err)
	}
	defer func() { _ = out.Close() }()
	if _, err := io.Copy(out, in); err != nil {
		return fmt.Errorf("engine: write %s: %w", dst, err)
	}
	return nil
}

// onceRegistry serializes first-call extraction per cache directory so the
// rename in ensureLocked happens exactly once across goroutines.
var (
	onceMu       sync.Mutex
	onceRegistry = map[string]*sync.Once{}
)

func getOnce(cacheDir string) *sync.Once {
	onceMu.Lock()
	defer onceMu.Unlock()
	if o, ok := onceRegistry[cacheDir]; ok {
		return o
	}
	o := &sync.Once{}
	onceRegistry[cacheDir] = o
	return o
}

func resetOnce(cacheDir string) {
	onceMu.Lock()
	defer onceMu.Unlock()
	delete(onceRegistry, cacheDir)
}
