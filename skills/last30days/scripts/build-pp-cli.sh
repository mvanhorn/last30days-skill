#!/usr/bin/env bash
# Build a Printing Press CLI (e.g. digg-pp-cli) from a PINNED, reviewed commit
# of the maintainer-controlled fork. USER-RUN ONLY: the last30days skill never
# executes this script; setup and doctor only print the command.
#
# Why not `npx @mvanhorn/printing-press-library install …`? That installer
# fetches a live registry from the upstream default branch and `go install`s
# upstream module paths (`@latest` for unreleased entries), so the code that
# runs can change without review. This script instead:
#   1. clones ONLY the fork below and checks out ONLY the pinned commit,
#      refusing to continue if HEAD does not match the pin;
#   2. builds with GOTOOLCHAIN=local (never downloads a Go toolchain) and
#      -mod=readonly (dependencies must match the fork's go.sum, verified
#      against the public checksum database);
#   3. writes the binary to ${PP_BIN_DIR:-$HOME/.local/bin} and prints its sha256.
#
# Usage: bash build-pp-cli.sh <slug>     (slug: digg | trustpilot | arxiv | techmeme)
# Needs: git, Go (install it yourself, e.g. `brew install go`).
set -euo pipefail

PP_FORK_REPO="https://github.com/missterr/printing-press-library.git"
PP_FORK_SHA="a672844a2e7dca35b6e0a7828e00d61ecb32302d"

slug="${1:-digg}"
case "$slug" in
  digg)       subdir="library/media-and-entertainment/digg" ;;
  trustpilot) subdir="library/marketing/trustpilot" ;;
  arxiv)      subdir="library/other/arxiv" ;;
  techmeme)   subdir="library/productivity/techmeme" ;;
  *) echo "unknown slug: $slug (expected digg, trustpilot, arxiv, or techmeme)" >&2; exit 2 ;;
esac
bin="${slug}-pp-cli"
bin_dir="${PP_BIN_DIR:-$HOME/.local/bin}"

for tool in git go; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "error: '$tool' not found on PATH. Install it yourself (e.g. 'brew install $tool'), then re-run." >&2
    exit 1
  fi
done

work="$(mktemp -d "${TMPDIR:-/tmp}/l30d-pp-build.XXXXXX")"
trap 'rm -rf "$work"' EXIT

echo "Fetching $PP_FORK_REPO at pinned commit $PP_FORK_SHA (sparse: $subdir)"
git -C "$work" init -q src
git -C "$work/src" remote add origin "$PP_FORK_REPO"
git -C "$work/src" sparse-checkout set --no-cone "/$subdir/"
git -C "$work/src" fetch -q --depth 1 --filter=blob:none origin "$PP_FORK_SHA"
git -C "$work/src" -c advice.detachedHead=false checkout -q FETCH_HEAD

actual="$(git -C "$work/src" rev-parse HEAD)"
if [[ "$actual" != "$PP_FORK_SHA" ]]; then
  echo "error: fetched commit $actual does not match pin $PP_FORK_SHA; refusing to build" >&2
  exit 1
fi

cd "$work/src/$subdir"
mkdir -p "$bin_dir"
echo "Building $bin (GOTOOLCHAIN=local, -mod=readonly, checksum-verified deps)"
GOTOOLCHAIN=local GOFLAGS=-mod=readonly GOSUMDB=sum.golang.org GONOSUMDB= GOPRIVATE= GOPROXY=https://proxy.golang.org \
  go build -trimpath -o "$bin_dir/$bin" "./cmd/$bin"

if command -v shasum >/dev/null 2>&1; then
  sum="$(shasum -a 256 "$bin_dir/$bin" | awk '{print $1}')"
else
  sum="$(sha256sum "$bin_dir/$bin" | awk '{print $1}')"
fi
echo "Installed $bin_dir/$bin"
echo "sha256 $sum"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on PATH; add it so the last30days engine can find $bin" ;;
esac
