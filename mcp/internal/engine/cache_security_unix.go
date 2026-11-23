//go:build darwin || dragonfly || freebsd || illumos || linux || netbsd || openbsd

package engine

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

func cacheParentDir(baseDir string) string {
	return filepath.Join(filepath.Clean(baseDir), fmt.Sprintf("%s-%d", cacheSubdir, os.Geteuid()))
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
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return fmt.Errorf("engine: unable to verify owner of %s", path)
	}
	if st.Uid != uint32(os.Geteuid()) {
		return fmt.Errorf("engine: cache directory %s is not owned by current user", path)
	}
	if info.Mode().Perm()&0o022 != 0 {
		return fmt.Errorf("engine: cache directory %s is writable by group or others (mode %04o)", path, info.Mode().Perm())
	}
	return nil
}

func verifyOwnedRegularFile(info os.FileInfo, path string) error {
	if !info.Mode().IsRegular() {
		return fmt.Errorf("engine: refusing non-regular cache file %s", path)
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return fmt.Errorf("engine: unable to verify owner of %s", path)
	}
	if st.Uid != uint32(os.Geteuid()) {
		return fmt.Errorf("engine: cache file %s is not owned by current user", path)
	}
	return nil
}

func openNoFollowFile(path string, flag int, perm os.FileMode) (*os.File, error) {
	openFlags := flag | syscall.O_NOFOLLOW
	if flag&(os.O_WRONLY|os.O_RDWR) == 0 {
		openFlags |= syscall.O_NONBLOCK
	}
	file, err := os.OpenFile(path, openFlags, perm)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	if err := verifyOwnedRegularFile(info, path); err != nil {
		_ = file.Close()
		return nil, err
	}
	return file, nil
}
