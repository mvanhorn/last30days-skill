// Package engine wraps the vendored Python last30days engine. The engine
// is embedded at build time via //go:embed and extracted into a per-user
// cache directory on first use, then invoked through python3 in a
// subprocess. Consumers should call EnsureUserCache to materialize the
// engine and Run to execute it.
package engine

import (
	"embed"
	"io/fs"
)

// EngineSourceDir is the embed root inside the binary. scripts/sync-engine.sh
// mirrors skills/last30days/scripts/ into this directory before each build.
// The all: prefix preserves files starting with "." or "_" so the .gitkeep
// anchor file survives - without it the embed would error before sync runs.
//
//go:embed all:vendored
var vendored embed.FS

// EngineFS returns the embedded engine as a filesystem rooted at the
// vendored/ directory contents (so callers see "last30days.py" at the
// root, not "vendored/last30days.py").
func EngineFS() (fs.FS, error) {
	return fs.Sub(vendored, "vendored")
}
