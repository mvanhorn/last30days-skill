//go:build !darwin && !dragonfly && !freebsd && !illumos && !linux && !netbsd && !openbsd && !windows

package engine

import (
	"fmt"
	"os"
	"runtime"
)

func tryLockCacheFile(file *os.File) (bool, error) {
	return false, fmt.Errorf("cache locking is unsupported on %s", runtime.GOOS)
}
