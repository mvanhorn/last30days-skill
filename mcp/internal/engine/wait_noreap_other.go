//go:build !windows && !linux && !darwin && !freebsd && !netbsd && !openbsd && !dragonfly

package engine

func waitChildNoReap(pid int) error {
	return errWaitNoReapUnsupported
}
