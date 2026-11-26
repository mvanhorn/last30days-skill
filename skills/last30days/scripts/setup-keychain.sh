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
  X_BEARER_TOKEN
  DEEPLINE_API_KEY
)

if [[ "${OSTYPE:-}" != darwin* ]]; then
  echo "setup-keychain.sh requires macOS (security command). Got: ${OSTYPE:-}" >&2
  exit 1
fi

if [[ -x /usr/bin/security ]]; then
  SECURITY_BIN="/usr/bin/security"
else
  SECURITY_BIN="${LAST30DAYS_TEST_SECURITY_BIN:-/usr/bin/security}"
fi
if [[ "$SECURITY_BIN" != /* || ! -x "$SECURITY_BIN" ]]; then
  echo "security binary not found at ${SECURITY_BIN}" >&2
  exit 1
fi

matches_regex() {
  local LC_ALL=C
  local value="$1"
  local pattern="$2"
  [[ "$value" =~ $pattern ]]
}

USER_NAME="${USER:-}"
if [[ -z "$USER_NAME" && -x /usr/bin/id ]]; then
  USER_NAME="$(/usr/bin/id -un 2>/dev/null || true)"
fi
if [[ -z "$USER_NAME" ]]; then
  echo "Unable to determine current username (set USER)." >&2
  exit 1
fi
cntrl_re='[[:cntrl:]]'
if [[ "$USER_NAME" == *$'\n'* || "$USER_NAME" == *$'\r'* ]] || matches_regex "$USER_NAME" "$cntrl_re"; then
  echo "Invalid USER value." >&2
  exit 1
fi

is_valid_key() {
  local candidate="$1"
  local allowed
  for allowed in "${ALL_KEYS[@]}"; do
    if [[ "$candidate" == "$allowed" ]]; then
      return 0
    fi
  done
  return 1
}

hex_encode() {
  local LC_ALL=C
  local s="$1"
  local i ch n h out=""
  for (( i = 0; i < ${#s}; i++ )); do
    ch="${s:i:1}"
    printf -v n "%d" "'${ch}"
    printf -v h "%02x" $(( n & 0xff ))
    out+="${h}"
  done
  HEX_VALUE="${out}"
}

REPLACE=0
ACTION="prompt"
TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) ACTION="list"; shift ;;
    --delete) ACTION="delete"; shift ;;
    --replace) REPLACE=1; shift ;;
    --help|-h) sed -n '2,/^$/p' "$0" | sed 's/^# //; s/^#//'; exit 0 ;;
    --) shift; if [[ $# -gt 0 ]]; then TARGETS+=("$@"); fi; break ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

if [[ ${#TARGETS[@]} -gt 0 ]]; then
  for key in "${TARGETS[@]}"; do
    if ! is_valid_key "$key"; then
      echo "unknown or invalid key: $key" >&2
      exit 2
    fi
  done
fi

case "$ACTION" in
  list)
    echo "Stored last30days-* keychain items:"
    for key in "${ALL_KEYS[@]}"; do
      # Existence checks do not need to decrypt or return the password.
      if "$SECURITY_BIN" find-generic-password -a "$USER_NAME" -s "${PREFIX}${key}" >/dev/null 2>&1; then
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
      if "$SECURITY_BIN" delete-generic-password -a "$USER_NAME" -s "${PREFIX}${key}" >/dev/null 2>&1; then
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

escaped_user="${USER_NAME//\\/\\\\}"
escaped_user="${escaped_user//\"/\\\"}"

added=0; skipped=0; replaced=0
for key in "${TARGETS[@]}"; do
  if "$SECURITY_BIN" find-generic-password -a "$USER_NAME" -s "${PREFIX}${key}" >/dev/null 2>&1; then
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
  value=""
  if ! IFS= read -rs value && [[ -z "$value" ]]; then
    echo
    skipped=$((skipped + 1))
    continue
  fi
  echo
  if [[ -z "$value" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  # Hex-encode in pure bash builtins and pipe via stdin to `security -i` so the
  # secret never enters argv or a bash-3.2 heredoc temp file in /tmp.
  HEX_VALUE=""
  hex_encode "$value"
  printf 'add-generic-password -U -a "%s" -s "%s" -X %s\n' \
    "$escaped_user" "${PREFIX}${key}" "$HEX_VALUE" | "$SECURITY_BIN" -i
  value=""
  HEX_VALUE=""
  if [[ "$existed" -eq 1 ]]; then
    replaced=$((replaced + 1))
  else
    added=$((added + 1))
  fi
done

echo
echo "Done. added=$added replaced=$replaced skipped=$skipped"
echo "Verify with: $0 --list"
