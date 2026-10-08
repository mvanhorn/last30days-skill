package engine

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func prepareCacheStage(cacheDir, version string) (string, *os.File, error) {
	publication, err := openPublicationLock(cacheDir, true)
	if err != nil {
		return "", nil, fmt.Errorf("engine: lock cache staging: %w", err)
	}
	defer publication.Close()
	if err := reclaimCacheStages(cacheDir); err != nil {
		return "", nil, fmt.Errorf("engine: reclaim cache staging: %w", err)
	}
	if sentinelMatches(cacheDir, version) {
		return "", nil, nil
	}
	owner, err := os.CreateTemp(filepath.Dir(cacheDir), filepath.Base(cacheDir)+".tmp-*.lock")
	if err != nil {
		return "", nil, fmt.Errorf("engine: create staging ownership file: %w", err)
	}
	stage := strings.TrimSuffix(owner.Name(), ".lock")
	locked, err := tryLockCacheFile(owner)
	if !locked && err == nil {
		err = errors.New("new staging ownership file is already locked")
	}
	if err == nil {
		err = os.Mkdir(stage, 0o700)
	}
	if err != nil {
		_ = owner.Close()
		_ = os.Remove(owner.Name())
		return "", nil, fmt.Errorf("engine: create cache staging: %w", err)
	}
	return stage, owner, nil
}

// Registration and reclamation both hold the publication lock. A stage's
// separate ownership lock stays held until extraction and publication finish.
func reclaimCacheStages(cacheDir string) error {
	parent := filepath.Dir(cacheDir)
	entries, err := os.ReadDir(parent)
	if err != nil {
		return err
	}
	prefix := filepath.Base(cacheDir) + ".tmp-"
	for _, entry := range entries {
		if !entry.Type().IsRegular() || !strings.HasPrefix(entry.Name(), prefix) || !strings.HasSuffix(entry.Name(), ".lock") {
			continue
		}
		path := filepath.Join(parent, entry.Name())
		owner, err := os.OpenFile(path, os.O_RDWR, 0o600)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}
		locked, err := tryLockCacheFile(owner)
		if err != nil || !locked {
			_ = owner.Close()
			if err != nil {
				return err
			}
			continue
		}
		err = os.RemoveAll(strings.TrimSuffix(path, ".lock"))
		_ = owner.Close()
		if err != nil {
			return err
		}
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return nil
}
