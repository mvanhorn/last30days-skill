#!/bin/bash
# Store last30days API keys in the macOS Keychain.
#
# Keys are stored as generic passwords with service name `last30days-<KEY>`
# for the current user. The lib/env.py loader picks them up automatically as
# the lowest-priority credential source on Darwin.
#
# Usage:
#   ./setup-keychain.sh              # interactive: prompts for each key
#   ./setup-keychain.sh KEY [KEY..]  # prompt only for the listed keys
#   ./setup-keychain.sh --list       # list which last30days-* items exist
#   ./setup-keychain.sh --delete KEY # remove a stored key
#
# Existing values are shown as "(set)" and skipped unless --replace is passed.
# Skip any prompt with empty input.

set -euo pipefail

PREFIX="last30days-"
# Mirrors lib/env.py::KEYCHAIN_KEYS — kept in sync via
# tests/test_env_keychain.py::test_keychain_keys_match_setup_script.
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

if [[ "${OSTYPE:-}" != darwin* ]]; then
  echo "setup-keychain.sh requires macOS (security command). Got: $OSTYPE" >&2
  exit 1
fi
if ! command -v security >/dev/null 2>&1; then
  echo "security command not found on PATH" >&2
  exit 1
fi

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

case "$ACTION" in
  list)
    echo "Stored last30days-* keychain items:"
    for key in "${ALL_KEYS[@]}"; do
      # No -w: the item's existence is the question, so don't ask Keychain to
      # decrypt and hand back the secret only to discard it.
      if security find-generic-password -a "$USER" -s "${PREFIX}${key}" >/dev/null 2>&1; then
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
      if security delete-generic-password -a "$USER" -s "${PREFIX}${key}" >/dev/null 2>&1; then
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
  # Exit status answers "is it set?" without decrypting the stored secret into
  # a shell variable, where a stray `set -x` or error trace could surface it.
  if security find-generic-password -a "$USER" -s "${PREFIX}${key}" >/dev/null 2>&1; then
    existed=1
  else
    existed=0
  fi
  if [[ "$existed" -eq 1 && "$REPLACE" -eq 0 ]]; then
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
  # Feed the secret on stdin rather than as `-w "$value"`. A command argument is
  # visible in `ps` output to every user on the machine for the lifetime of the
  # call; stdin is not. With -w given no argument, `security` prompts for the
  # value and then a retype (the form its own man page recommends), so the value
  # goes down the pipe twice and the prompts are discarded.
  if ! printf '%s\n%s\n' "$value" "$value" \
      | security add-generic-password -U -a "$USER" -s "${PREFIX}${key}" -w >/dev/null 2>&1; then
    unset value
    echo "failed to store $key in the keychain" >&2
    exit 1
  fi
  unset value
  if [[ "$existed" -eq 1 ]]; then
    replaced=$((replaced + 1))
  else
    added=$((added + 1))
  fi
done

echo
echo "Done. added=$added replaced=$replaced skipped=$skipped"
echo "Verify with: $0 --list"
