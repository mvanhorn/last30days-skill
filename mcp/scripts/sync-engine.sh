#!/usr/bin/env bash
# Mirrors skills/last30days/scripts/{last30days.py,store.py,lib/} into
# mcp/internal/engine/vendored/
# so the Go binary's embed.FS captures the engine at build time.
#
# Source of truth: skills/last30days/scripts/. Never edit mcp/internal/engine/vendored/ directly.
# Run before `go build` locally and in CI before `printing-press bundle`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MCP_DIR}/.." && pwd)"
ENGINE_SRC="${REPO_ROOT}/skills/last30days/scripts"
# Embed path must live inside the consuming package (Go //go:embed cannot
# reach outside its own directory tree), so vendored/ sits under engine/.
VENDORED="${MCP_DIR}/internal/engine/vendored"

if [ ! -f "${ENGINE_SRC}/last30days.py" ]; then
  echo "sync-engine: ${ENGINE_SRC}/last30days.py not found" >&2
  exit 1
fi

mkdir -p "${VENDORED}"
# Clear stale content while keeping the .gitkeep that anchors the embed path.
find "${VENDORED}" -mindepth 1 -not -name ".gitkeep" -delete

# Copy the entry script, the top-level modules it imports at runtime, and the
# lib/ tree (modules + lib/vendor/). sync_contract_test.go fails if the engine
# imports a top-level module missing from this list.
for module in last30days.py store.py; do
  cp "${ENGINE_SRC}/${module}" "${VENDORED}/${module}"
done
cp -R "${ENGINE_SRC}/lib" "${VENDORED}/lib"

# Strip caches so the embed.FS stays deterministic.
find "${VENDORED}" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "${VENDORED}" -type f -name "*.pyc" -delete

echo "sync-engine: vendored engine at ${VENDORED}"
