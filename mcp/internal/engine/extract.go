package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
)

// SentinelFilename names the file Ensure writes inside the cache directory
// after a successful extraction. Its contents are compared to the requested
// version; a match short-circuits re-extraction on subsequent calls.
const SentinelFilename = ".version"

// DigestFilename records the SHA-256 manifest digest of the embedded engine
// tree so reuse verifies content integrity rather than trusting a bare version.
const DigestFilename = ".sha256"

// cacheSubdir namespaces our cache under the OS user cache directory so
// multiple printing-press-style bundles can coexist.
const cacheSubdir = "last30days-pp-mcp"

// CacheEnvOverride lets users redirect the cache directory when the default
// OS cache location is read-only (locked-down corp images, ephemeral CI
// containers). Pointed at by extract errors via the documented escape hatch.
const CacheEnvOverride = "LAST30DAYS_CACHE_DIR"

var validVersionRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$`)

var windowsReservedNames = map[string]struct{}{
	"CON": {}, "PRN": {}, "AUX": {}, "NUL": {},
	"COM1": {}, "COM2": {}, "COM3": {}, "COM4": {}, "COM5": {}, "COM6": {}, "COM7": {}, "COM8": {}, "COM9": {},
	"LPT1": {}, "LPT2": {}, "LPT3": {}, "LPT4": {}, "LPT5": {}, "LPT6": {}, "LPT7": {}, "LPT8": {}, "LPT9": {},
}

func validateVersion(version string) error {
	if version == "" {
		return errors.New("engine: version is required")
	}
	if !validVersionRe.MatchString(version) {
		return fmt.Errorf("engine: invalid version %q", version)
	}
	if strings.HasSuffix(version, ".") || strings.Contains(version, "..") {
		return fmt.Errorf("engine: invalid version %q", version)
	}
	stem := strings.ToUpper(strings.SplitN(version, ".", 2)[0])
	if _, reserved := windowsReservedNames[stem]; reserved {
		return fmt.Errorf("engine: reserved version name %q", version)
	}
	return nil
}

// Ensure extracts src into a user-scoped cache directory under baseDir and
// returns the cache path. If the sentinel and content digest already match src
// and directory ownership/permissions are safe, the directory is reused without
// rewriting. version must be a safe single path segment.
//
// Extraction writes to a unique sibling directory and publishes it under a
// filesystem lock so another process cannot remove a completed cache. Concurrent
// callers within the same process serialize behind a per-cache-dir sync.Once.
func Ensure(src fs.FS, baseDir, version string) (string, error) {
	if err := validateVersion(version); err != nil {
		return "", err
	}
	parent := cacheParentDir(baseDir)
	cacheDir := filepath.Join(parent, version)
	if filepath.Dir(cacheDir) != parent || filepath.Base(cacheDir) != version {
		return "", fmt.Errorf("engine: cache path escaped parent for version %q", version)
	}

	once := getOnce(cacheDir)
	once.Do(func() {
		once.err = ensureLocked(src, cacheDir, version)
		if once.err != nil {
			// Waiting callers share this error; a later call can retry with
			// a new entry after the failed extraction has finished.
			resetOnce(cacheDir)
		}
	})
	if once.err != nil {
		return "", once.err
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

func manifestDigest(files map[string][32]byte) string {
	paths := make([]string, 0, len(files))
	for p := range files {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	manifest := sha256.New()
	for _, p := range paths {
		sum := files[p]
		_, _ = fmt.Fprintf(manifest, "%s\x00%x\n", p, sum)
	}
	return hex.EncodeToString(manifest.Sum(nil))
}

func computeSourceManifest(src fs.FS) (string, map[string][32]byte, error) {
	files := map[string][32]byte{}
	err := fs.WalkDir(src, ".", func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == "." || d.IsDir() {
			return nil
		}
		in, err := src.Open(path)
		if err != nil {
			return fmt.Errorf("engine: open %s: %w", path, err)
		}
		h := sha256.New()
		_, copyErr := io.Copy(h, in)
		closeErr := in.Close()
		if copyErr != nil {
			return fmt.Errorf("engine: hash %s: %w", path, copyErr)
		}
		if closeErr != nil {
			return fmt.Errorf("engine: close %s: %w", path, closeErr)
		}
		var sum [32]byte
		copy(sum[:], h.Sum(nil))
		files[path] = sum
		return nil
	})
	if err != nil {
		return "", nil, err
	}
	return manifestDigest(files), files, nil
}

func verifyCachedTree(src fs.FS, cacheDir, version string) bool {
	if err := verifyPrivateDir(filepath.Dir(cacheDir)); err != nil {
		return false
	}
	if err := verifyPrivateDir(cacheDir); err != nil {
		return false
	}
	if !sentinelMatches(cacheDir, version) {
		return false
	}
	expectedDigest, expectedFiles, err := computeSourceManifest(src)
	if err != nil {
		return false
	}
	return cacheReady(cacheDir, version, expectedDigest, expectedFiles)
}

func ensureLocked(src fs.FS, cacheDir, version string) error {
	if verifyCachedTree(src, cacheDir, version) {
		if lock, err := openPublicationLock(cacheDir, false); err == nil && lock != nil {
			_ = reclaimCacheStages(cacheDir)
			_ = lock.Close()
		}
		return nil
	}
	parent := filepath.Dir(cacheDir)
	if err := ensurePrivateParentDir(parent); err != nil {
		return fmt.Errorf("engine: unsafe cache parent (%s, set %s to override): %w", parent, CacheEnvOverride, err)
	}
	tmpDir, owner, err := prepareCacheStage(src, cacheDir, version)
	if err != nil {
		return err
	}
	if tmpDir == "" {
		return nil
	}
	defer func() {
		_ = owner.Close()
		_ = os.RemoveAll(tmpDir)
		_ = os.Remove(owner.Name())
	}()
	expectedDigest, expectedFiles, err := extractAll(src, tmpDir)
	if err != nil {
		return err
	}
	sentinel := filepath.Join(tmpDir, SentinelFilename)
	if err := os.WriteFile(sentinel, []byte(version), 0o600); err != nil {
		return fmt.Errorf("engine: write sentinel: %w", err)
	}
	digestPath := filepath.Join(tmpDir, DigestFilename)
	if err := os.WriteFile(digestPath, []byte(expectedDigest), 0o600); err != nil {
		return fmt.Errorf("engine: write digest: %w", err)
	}
	lock, err := openPublicationLock(cacheDir, true)
	if err != nil {
		return fmt.Errorf("engine: lock cache publication: %w", err)
	}
	defer func() { _ = lock.Close() }()
	// Keep the lock file in place: unlinking it can give concurrent publishers
	// different lock inodes. Closing the descriptor releases the lock.
	if err := verifyPrivateDir(parent); err != nil {
		return fmt.Errorf("engine: unsafe cache parent: %w", err)
	}
	if cacheReady(cacheDir, version, expectedDigest, expectedFiles) {
		return nil
	}
	if _, err := os.Lstat(cacheDir); err == nil {
		if err := verifyPrivateDir(cacheDir); err != nil {
			return fmt.Errorf("engine: unsafe existing cache dir: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("engine: stat old cache: %w", err)
	}
	if err := os.RemoveAll(cacheDir); err != nil {
		return fmt.Errorf("engine: clean old cache: %w", err)
	}
	if err := os.Rename(tmpDir, cacheDir); err != nil {
		return fmt.Errorf("engine: promote tmp cache: %w", err)
	}
	return nil
}

func readRegularNoFollow(path string) ([]byte, error) {
	f, err := openNoFollowFile(path, os.O_RDONLY, 0)
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	return io.ReadAll(io.LimitReader(f, 4096))
}

func sentinelMatches(cacheDir, version string) bool {
	data, err := readRegularNoFollow(filepath.Join(cacheDir, SentinelFilename))
	if err != nil {
		return false
	}
	return string(data) == version
}

func cacheReady(cacheDir, version, expectedDigest string, expectedFiles map[string][32]byte) bool {
	if err := verifyPrivateDir(filepath.Dir(cacheDir)); err != nil {
		return false
	}
	if err := verifyPrivateDir(cacheDir); err != nil {
		return false
	}
	if !sentinelMatches(cacheDir, version) {
		return false
	}
	digestBytes, err := readRegularNoFollow(filepath.Join(cacheDir, DigestFilename))
	if err != nil || string(digestBytes) != expectedDigest {
		return false
	}
	root, err := os.OpenRoot(cacheDir)
	if err != nil {
		return false
	}
	defer func() { _ = root.Close() }()
	for relPath, wantSum := range expectedFiles {
		nativeRel := filepath.FromSlash(relPath)
		f, err := openRootRegularNoFollow(root, cacheDir, nativeRel)
		if err != nil {
			return false
		}
		h := sha256.New()
		_, copyErr := io.Copy(h, f)
		_ = f.Close()
		if copyErr != nil {
			return false
		}
		var gotSum [32]byte
		copy(gotSum[:], h.Sum(nil))
		if gotSum != wantSum {
			return false
		}
	}
	return verifyCachedTreeEntries(cacheDir, root, expectedFiles)
}

var blockedExtraCacheExts = map[string]struct{}{
	".py":    {},
	".pyc":   {},
	".pyo":   {},
	".pyw":   {},
	".pth":   {},
	".so":    {},
	".pyd":   {},
	".dll":   {},
	".dylib": {},
	".egg":   {},
	".whl":   {},
}

func verifyCachedTreeEntries(cacheDir string, root *os.Root, expectedFiles map[string][32]byte) bool {
	errUnexpected := errors.New("unexpected cache entry")
	err := fs.WalkDir(root.FS(), ".", func(relPath string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if relPath == "." {
			return nil
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return errUnexpected
		}
		fullPath := filepath.Join(cacheDir, filepath.FromSlash(relPath))
		if d.IsDir() {
			if pathHasSegment(relPath, "__pycache__") {
				return errUnexpected
			}
			return verifyPrivateDir(fullPath)
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if err := verifyOwnedRegularFile(info, fullPath); err != nil {
			return err
		}
		if relPath == SentinelFilename || relPath == DigestFilename {
			return nil
		}
		if _, ok := expectedFiles[relPath]; ok {
			return nil
		}
		if pathHasSegment(relPath, "__pycache__") {
			return errUnexpected
		}
		ext := strings.ToLower(filepath.Ext(relPath))
		if _, blocked := blockedExtraCacheExts[ext]; blocked {
			return errUnexpected
		}
		return nil
	})
	return err == nil
}

func pathHasSegment(slashPath, target string) bool {
	for _, seg := range strings.Split(slashPath, "/") {
		if seg == target {
			return true
		}
	}
	return false
}

func extractAll(src fs.FS, dst string) (string, map[string][32]byte, error) {
	root, err := os.OpenRoot(dst)
	if err != nil {
		return "", nil, fmt.Errorf("engine: open staging root: %w", err)
	}
	defer func() { _ = root.Close() }()
	files := map[string][32]byte{}
	err = fs.WalkDir(src, ".", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == "." {
			return nil
		}
		rel := filepath.FromSlash(path)
		if d.IsDir() {
			return root.MkdirAll(rel, 0o700)
		}
		sum, err := copyEmbeddedFile(src, root, path, rel)
		if err != nil {
			return err
		}
		files[path] = sum
		return nil
	})
	if err != nil {
		return "", nil, err
	}
	return manifestDigest(files), files, nil
}

func copyEmbeddedFile(src fs.FS, root *os.Root, srcPath, relDst string) ([32]byte, error) {
	var sum [32]byte
	in, err := src.Open(srcPath)
	if err != nil {
		return sum, fmt.Errorf("engine: open %s: %w", srcPath, err)
	}
	defer func() { _ = in.Close() }()
	if dir := filepath.Dir(relDst); dir != "." {
		if err := root.MkdirAll(dir, 0o700); err != nil {
			return sum, fmt.Errorf("engine: ensure parent of %s: %w", relDst, err)
		}
	}
	out, err := root.OpenFile(relDst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return sum, fmt.Errorf("engine: create %s: %w", relDst, err)
	}
	defer func() { _ = out.Close() }()
	h := sha256.New()
	if _, err := io.Copy(io.MultiWriter(out, h), in); err != nil {
		return sum, fmt.Errorf("engine: write %s: %w", relDst, err)
	}
	copy(sum[:], h.Sum(nil))
	return sum, nil
}

// onceRegistry serializes first-call extraction per cache directory so the
// rename in ensureLocked happens exactly once across goroutines.
type extractionOnce struct {
	sync.Once
	err error
}

var (
	onceMu       sync.Mutex
	onceRegistry = map[string]*extractionOnce{}
)

func getOnce(cacheDir string) *extractionOnce {
	onceMu.Lock()
	defer onceMu.Unlock()
	if o, ok := onceRegistry[cacheDir]; ok {
		return o
	}
	o := &extractionOnce{}
	onceRegistry[cacheDir] = o
	return o
}

func resetOnce(cacheDir string) {
	onceMu.Lock()
	defer onceMu.Unlock()
	delete(onceRegistry, cacheDir)
}
