package engine

import (
	"os"
	"syscall"
	"unsafe"
)

var lockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")

func tryLockCacheFile(file *os.File) (bool, error) {
	const exclusiveLock = 0x00000002
	const failImmediately = 0x00000001
	const lockViolation = syscall.Errno(33)
	var overlapped syscall.Overlapped
	result, _, err := lockFileEx.Call(file.Fd(), exclusiveLock|failImmediately, 0, 1, 0, uintptr(unsafe.Pointer(&overlapped)))
	if result == 0 {
		if err == lockViolation {
			return false, nil
		}
		return false, err
	}
	return true, nil
}
