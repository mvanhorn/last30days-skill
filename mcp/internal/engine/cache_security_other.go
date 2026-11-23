//go:build !darwin && !dragonfly && !freebsd && !illumos && !linux && !netbsd && !openbsd && !windows

package engine

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

func cacheParentDir(baseDir string) string {
	return filepath.Join(filepath.Clean(baseDir), cacheSubdir)
}

func verifyPrivateDir(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("engine: refusing symlink cache directory %s", path)
	}
	if !info.IsDir() {
		return fmt.Errorf("engine: cache path %s is not a directory", path)
	}
	return nil
}

func verifyOwnedRegularFile(info os.FileInfo, path string) error {
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
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
