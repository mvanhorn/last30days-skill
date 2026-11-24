//go:build linux

package engine

import (
	"syscall"
	"unsafe"
)

const linuxPPID = 1 // P_PID from <sys/wait.h>

// waitChildNoReap blocks until child pid exits without reaping it, keeping
// the zombie pinned in the kernel process table so its PID/PGID cannot be
// recycled before group signaling completes.
func waitChildNoReap(pid int) error {
	// Linux struct siginfo is 128 bytes across architectures; [16]uint64
	// guarantees 8-byte alignment.
	var siginfo [16]uint64
	for {
		_, _, errno := syscall.Syscall6(
			syscall.SYS_WAITID,
			uintptr(linuxPPID),
			uintptr(pid),
			uintptr(unsafe.Pointer(&siginfo[0])),
			uintptr(syscall.WEXITED|syscall.WNOWAIT),
			0,
			0,
		)
		if errno == 0 {
			return nil
		}
		if errno == syscall.EINTR {
			continue
		}
		if errno == syscall.ENOSYS {
			return errWaitNoReapUnsupported
		}
		return errno
	}
}
