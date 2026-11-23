package engine

import (
	"errors"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"syscall"
)

const fileAttributeReparsePoint = 0x0400

func sanitizeUserScope(raw string) string {
	var b strings.Builder
	for _, r := range raw {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '.' || r == '_' || r == '-' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func cacheParentDir(baseDir string) string {
	scope := ""
	if u, err := user.Current(); err == nil && u != nil {
		scope = sanitizeUserScope(u.Uid)
		if scope == "" {
			scope = sanitizeUserScope(u.Username)
		}
	}
	if scope == "" {
		scope = sanitizeUserScope(os.Getenv("USERNAME"))
	}
	if scope == "" {
		return filepath.Join(filepath.Clean(baseDir), cacheSubdir)
	}
	return filepath.Join(filepath.Clean(baseDir), fmt.Sprintf("%s-%s", cacheSubdir, scope))
}

func isReparsePoint(info os.FileInfo) bool {
	if info.Mode()&os.ModeSymlink != 0 {
		return true
	}
	if attr, ok := info.Sys().(*syscall.Win32FileAttributeData); ok {
		return attr.FileAttributes&fileAttributeReparsePoint != 0
	}
	return false
}

func verifyPrivateDir(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if isReparsePoint(info) {
		return fmt.Errorf("engine: refusing symlink or junction cache directory %s", path)
	}
	if !info.IsDir() {
		return fmt.Errorf("engine: cache path %s is not a directory", path)
	}
	return nil
}

func verifyOwnedRegularFile(info os.FileInfo, path string) error {
	if isReparsePoint(info) || !info.Mode().IsRegular() {
		return fmt.Errorf("engine: refusing non-regular cache file %s", path)
	}
	return nil
}

func openNoFollowFile(path string, flag int, perm os.FileMode) (*os.File, error) {
	if info, err := os.Lstat(path); err == nil {
		if err := verifyOwnedRegularFile(info, path); err != nil {
			return nil, err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	file, err := os.OpenFile(path, flag, perm)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	if !info.Mode().IsRegular() {
		_ = file.Close()
		return nil, fmt.Errorf("engine: refusing non-regular cache file %s", path)
	}
	return file, nil
}
