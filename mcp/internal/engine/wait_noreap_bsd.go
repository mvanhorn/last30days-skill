//go:build darwin || freebsd || netbsd || openbsd || dragonfly

package engine

import "syscall"

// waitChildNoReap watches child pid exit via kqueue EVFILT_PROC/NOTE_EXIT
// without reaping it, keeping the zombie pinned in the process table until
// cmd.Wait() runs. Using kqueue instead of waitid also avoids Darwin's
// waitid bug where WEXITED wakes on stopped processes (Go issue #19314).
func waitChildNoReap(pid int) error {
	syscall.ForkLock.RLock()
	kq, err := syscall.Kqueue()
	if err == nil {
		syscall.CloseOnExec(kq)
	}
	syscall.ForkLock.RUnlock()
	if err != nil {
		return errWaitNoReapUnsupported
	}
	defer syscall.Close(kq)

	var change [1]syscall.Kevent_t
	syscall.SetKevent(&change[0], pid, syscall.EVFILT_PROC, syscall.EV_ADD|syscall.EV_ONESHOT)
	change[0].Fflags = syscall.NOTE_EXIT

	var events [1]syscall.Kevent_t
	for {
		zero := syscall.Timespec{}
		n, err := syscall.Kevent(kq, change[:], events[:], &zero)
		if err == syscall.EINTR {
			continue
		}
		if err == syscall.ESRCH {
			// Because cmd.Wait() has not reaped our direct child, ESRCH on
			// EVFILT_PROC registration means the child is already a zombie.
			return nil
		}
		if err != nil {
			return errWaitNoReapUnsupported
		}
		if n > 0 {
			if events[0].Flags&syscall.EV_ERROR != 0 {
				if syscall.Errno(events[0].Data) == syscall.ESRCH {
					return nil
				}
				return errWaitNoReapUnsupported
			}
			return nil
		}
		break
	}

	for {
		n, err := syscall.Kevent(kq, nil, events[:], nil)
		if err == syscall.EINTR {
			continue
		}
		if err != nil {
			return errWaitNoReapUnsupported
		}
		if n > 0 {
			return nil
		}
	}
}
