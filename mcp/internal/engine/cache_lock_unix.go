//go:build darwin || dragonfly || freebsd || illumos || linux || netbsd || openbsd

package engine

import (
	"errors"
	"os"
	"syscall"
)

func tryLockCacheFile(file *os.File) (bool, error) {
	err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) || errors.Is(err, syscall.EINTR) {
		return false, nil
	}
	return err == nil, err
}
