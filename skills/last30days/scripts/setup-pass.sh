#!/usr/bin/env bash
# Store last30days API keys in a pass(1) store.
#
# Keys are stored at pass path `last30days/<KEY>` (the Linux/Unix analog of the
# Keychain `last30days-<KEY>` convention). The lib/env.py loader picks them up
# automatically as a lowest-priority credential source wherever `pass` exists.
# Honors PASSWORD_STORE_DIR; override the path prefix with LAST30DAYS_PASS_PREFIX
# (must match what the loader uses).
#
# Usage:
#   ./setup-pass.sh              # interactive: prompts for each key
#   ./setup-pass.sh KEY [KEY..]  # prompt only for the listed keys
#   ./setup-pass.sh --list       # list which last30days/* entries exist
#   ./setup-pass.sh --delete KEY # remove a stored key
#
# Existing values are shown as "(set)" and skipped unless --replace is passed.
# Skip any prompt with empty input.

set -euo pipefail

PREFIX="${LAST30DAYS_PASS_PREFIX:-last30days/}"
# Mirrors lib/env.py::KEYCHAIN_KEYS — kept in sync via
# tests/test_env_pass.py::test_pass_keys_match_setup_script.
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
)

REPLACE=0
ACTION="prompt"
TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) ACTION="list"; shift ;;
    --delete) ACTION="delete"; shift ;;
    --replace) REPLACE=1; shift ;;
    --help|-h) sed -n '2,/^$/p' "$0" | sed 's/^# //; s/^#//'; exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

# Checked after flag parsing so `--help` works on a box without pass installed.
if ! command -v pass >/dev/null 2>&1; then
  echo "setup-pass.sh requires the pass(1) password manager (not found on PATH)." >&2
  exit 1
fi

case "$ACTION" in
  list)
    echo "Stored ${PREFIX}* pass entries:"
    for key in "${ALL_KEYS[@]}"; do
      if pass show "${PREFIX}${key}" >/dev/null 2>&1; then
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
      if pass rm -f "${PREFIX}${key}" >/dev/null 2>&1; then
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
  if pass show "${PREFIX}${key}" >/dev/null 2>&1; then
    existing=1
  else
    existing=0
  fi
  if [[ "$existing" -eq 1 && "$REPLACE" -eq 0 ]]; then
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
  # Don't let one failed insert (gpg misconfig, missing store key, disk) abort
  # the whole batch under `set -e`; report it and move on.
  if ! printf '%s\n' "$value" | pass insert -m -f "${PREFIX}${key}" >/dev/null; then
    echo "  failed: $key (pass insert error)" >&2
    skipped=$((skipped + 1))
    continue
  fi
  if [[ "$existing" -eq 1 ]]; then
    replaced=$((replaced + 1))
  else
    added=$((added + 1))
  fi
done

echo
echo "Done. added=$added replaced=$replaced skipped=$skipped"
echo "Verify with: $0 --list"
