package engine

import (
	"fmt"
	"os"
	"time"
)

const cacheLockTimeout = 5 * time.Second

func openPublicationLock(cacheDir string, wait bool) (*os.File, error) {
	file, err := os.OpenFile(cacheDir+".lock", os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if wait {
		err = lockCacheFile(file)
	} else {
		var locked bool
		locked, err = tryLockCacheFile(file)
		if !locked && err == nil {
			_ = file.Close()
			return nil, nil
		}
	}
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	return file, nil
}

func lockCacheFile(file *os.File) error {
	deadline := time.Now().Add(cacheLockTimeout)
	for {
		locked, err := tryLockCacheFile(file)
		if err != nil {
			return err
		}
		if locked {
			return nil
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return fmt.Errorf("cache lock unavailable after %s: %w", cacheLockTimeout, os.ErrDeadlineExceeded)
		}
		time.Sleep(min(25*time.Millisecond, remaining))
	}
}
