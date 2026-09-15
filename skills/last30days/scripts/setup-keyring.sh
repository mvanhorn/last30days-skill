#!/bin/bash
# Store last30days API keys in the Secret Service keyring (libsecret).
#
# The Linux analog of setup-keychain.sh. Keys are stored as Secret Service items
# with the attribute `service=last30days-<KEY>`. The lib/env.py loader picks them
# up automatically as a lowest-priority credential source on non-Darwin systems.
#
# Common providers: GNOME Keyring, KWallet, KeePassXC. Some desktop distributions
# ship one preconfigured (Omarchy, for example, installs gnome-keyring by default).
#
# SECURITY NOTE: protection at rest is whatever the holding collection provides.
# A passwordless, never-locking keyring — the default on some desktops — leaves its
# items readable by anything running as you — the same protection level as a
# 0600 file, not encryption. Pass --collection to target a password-protected
# collection, or use setup-pass.sh, if you need more than that.
#
# Usage:
#   ./setup-keyring.sh                    # interactive: prompts for each key
#   ./setup-keyring.sh KEY [KEY..]        # prompt only for the listed keys
#   ./setup-keyring.sh --list             # list which keys are stored
#   ./setup-keyring.sh --delete KEY       # remove a stored key
#   ./setup-keyring.sh --collection NAME  # target a specific collection
#
# Existing values are shown as "(set)" and skipped unless --replace is passed.
# Skip any prompt with empty input.

set -euo pipefail

PREFIX="last30days-"
# Mirrors lib/env.py::KEYCHAIN_KEYS, shared with setup-keychain.sh.
ALL_KEYS=(
  OPENAI_API_KEY
  XAI_API_KEY
  GOOGLE_API_KEY
  GEMINI_API_KEY
  GOOGLE_GENAI_API_KEY
  SCRAPECREATORS_API_KEY
  APIFY_API_TOKEN
  AUTH_TOKEN
  CT0
  BSKY_HANDLE
  BSKY_APP_PASSWORD
  TRUTHSOCIAL_TOKEN
  BRAVE_API_KEY
  EXA_API_KEY
  SERPER_API_KEY
  OPENROUTER_API_KEY
  PERPLEXITY_API_KEY
  PARALLEL_API_KEY
  XQUIK_API_KEY
  XIAOHONGSHU_API_BASE
  GITHUB_TOKEN
  BRIGHTDATA_API_KEY
)

if [[ "${OSTYPE:-}" == darwin* ]]; then
  echo "On macOS use setup-keychain.sh instead." >&2
  exit 1
fi
if ! command -v secret-tool >/dev/null 2>&1; then
  echo "secret-tool not found on PATH. Install libsecret (Arch: pacman -S libsecret)." >&2
  exit 1
fi
if ! command -v busctl >/dev/null 2>&1 || ! busctl --user list 2>/dev/null | grep -q org.freedesktop.secrets; then
  echo "Warning: no org.freedesktop.secrets provider on the session bus." >&2
  echo "Start a keyring daemon (Arch: gnome-keyring-daemon --start --components=secrets)." >&2
fi

REPLACE=0
ACTION="prompt"
COLLECTION=""
TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) ACTION="list"; shift ;;
    --delete) ACTION="delete"; shift ;;
    --replace) REPLACE=1; shift ;;
    --collection) COLLECTION="${2:?--collection needs a name}"; shift 2 ;;
    --help|-h) sed -n '2,/^$/p' "$0" | sed 's/^# //; s/^#//'; exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

store_args=()
[[ -n "$COLLECTION" ]] && store_args=(--collection "$COLLECTION")

case "$ACTION" in
  list)
    echo "Stored ${PREFIX}* keyring items:"
    for key in "${ALL_KEYS[@]}"; do
      if secret-tool lookup service "${PREFIX}${key}" >/dev/null 2>&1; then
        echo "  $key"
      fi
    done
    exit 0
    ;;
  delete)
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
      echo "--delete needs at least one KEY name" >&2; exit 2
    fi
    for key in "${TARGETS[@]}"; do
      if secret-tool clear service "${PREFIX}${key}" 2>/dev/null; then
        echo "deleted: $key"
      else
        echo "not found: $key"
      fi
    done
    exit 0
    ;;
esac

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=("${ALL_KEYS[@]}")
fi

added=0; skipped=0; replaced=0
for key in "${TARGETS[@]}"; do
  existing="$(secret-tool lookup service "${PREFIX}${key}" 2>/dev/null || true)"
  if [[ -n "$existing" && "$REPLACE" -eq 0 ]]; then
    printf "  %-28s (set, skipping — use --replace to overwrite)\n" "$key"
    skipped=$((skipped + 1))
    continue
  fi
  printf "  %-28s " "$key"
  IFS= read -rs value
  echo
  if [[ -z "$value" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  printf '%s' "$value" | secret-tool store "${store_args[@]}" \
    --label="${PREFIX}${key}" service "${PREFIX}${key}"
  if [[ -n "$existing" ]]; then
    replaced=$((replaced + 1))
  else
    added=$((added + 1))
  fi
done

echo
echo "added: $added  replaced: $replaced  skipped: $skipped"
