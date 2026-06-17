#!/bin/bash
set -euo pipefail

# Check last30days configuration status and show appropriate welcome message.
# Priority: .claude/last30days.env > ~/.config/last30days/.env > env vars

PROJECT_ENV=".claude/last30days.env"
GLOBAL_ENV="$HOME/.config/last30days/.env"

# Ensure LAST30DAYS_MEMORY_DIR exists for HTML-brief / raw-markdown saves.
# SKILL.md and the engine default this via the same env-var fallback. Fresh
# installs otherwise fail silently on first --emit=html run. See #395.
mkdir -p "${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}" 2>/dev/null || true

# Helper: warn if file permissions are too open
check_perms() {
  local file="$1"
  if [[ ! -f "$file" ]]; then return; fi
  # Git-for-Windows / MSYS / Cygwin run stat in noacl mode (always 644),
  # so this POSIX check is a false positive. Windows perms use ACLs.
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) return ;;
  esac
  local perms
  # Try GNU stat first (Linux), fall back to BSD stat (macOS).
  # On Linux, `stat -f` prints filesystem info (not permissions) and exits 0,
  # so the previous BSD-first ordering left $perms as multi-line garbage on
  # every Linux session start and printed a false WARNING.
  perms=$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file" 2>/dev/null || echo "")
  if [[ -n "$perms" && "$perms" != "600" && "$perms" != "400" ]]; then
    echo "/last30days: WARNING — $file has permissions $perms (should be 600)."
    echo "  Fix: chmod 600 $file"
  fi
}

# Load env file into variables for inspection (without exporting)
load_env_vars() {
  local file="$1"
  if [[ -f "$file" ]]; then
    while IFS='=' read -r key value; do
      # Skip comments, empty lines
      [[ "$key" =~ ^[[:space:]]*# ]] && continue
      [[ -z "$key" ]] && continue
      key=$(echo "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
      value=$(echo "$value" | sed -e 's/^[[:space:]]*//;s/[[:space:]]*$//' -e 's/^["'\''"]//;s/["'\''"]$//')
      # Strip inline comments (# preceded by whitespace) to prevent
      # command substitution in backtick-containing comments
      value="${value%%[[:space:]]#*}"
      if [[ -n "$key" && -n "$value" ]]; then
        # printf -v writes via assignment semantics (global from inside a
        # function), works on macOS's /bin/bash 3.2 — `declare -g` is 4.2+.
        printf -v "ENV_${key}" '%s' "$value"
      fi
    done < "$file"
  fi
}

# Determine which config file is active
CONFIG_FILE=""
if [[ -f "$PROJECT_ENV" ]]; then
  CONFIG_FILE="$PROJECT_ENV"
  check_perms "$PROJECT_ENV"
elif [[ -f "$GLOBAL_ENV" ]]; then
  CONFIG_FILE="$GLOBAL_ENV"
  check_perms "$GLOBAL_ENV"
fi

# Load config if found
if [[ -n "$CONFIG_FILE" ]]; then
  load_env_vars "$CONFIG_FILE"
fi

# Check SETUP_COMPLETE (from file or env)
SETUP_COMPLETE="${ENV_SETUP_COMPLETE:-${SETUP_COMPLETE:-}}"

# Compute last-run summary line (if last-run.json exists)
if [[ "${LAST30DAYS_CONFIG_DIR+x}" == "x" ]]; then
  if [[ -n "$LAST30DAYS_CONFIG_DIR" ]]; then
    LAST_RUN_FILE="$LAST30DAYS_CONFIG_DIR/last-run.json"
  else
    LAST_RUN_FILE=""
  fi
else
  LAST_RUN_FILE="$HOME/.config/last30days/last-run.json"
fi
LAST_RUN_LINE=""
if [[ -n "$LAST_RUN_FILE" && -f "$LAST_RUN_FILE" ]] && command -v python3 &>/dev/null; then
  LAST_RUN_LINE=$(LAST_RUN_FILE="$LAST_RUN_FILE" python3 - <<'PY' 2>/dev/null || true
import datetime
import json
import os

path = os.environ["LAST_RUN_FILE"]
try:
    with open(path) as fh:
        d = json.load(fh)
    topic = (d.get("topic") or "?")[:60]
    ts = d.get("timestamp", "")
    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    delta = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
    if delta < 60: ago = f"{int(delta)}s ago"
    elif delta < 3600: ago = f"{int(delta//60)}m ago"
    elif delta < 86400: ago = f"{int(delta//3600)}h ago"
    else: ago = f"{int(delta//86400)}d ago"
    total = d.get("total", 0)
    print(f"  Last run: \"{topic}\" · {ago} · {total} results")
except Exception:
    pass
PY
)
fi

# Detect capability that doesn't need a config file: yt-dlp on PATH.
# Done before the new-user early-exit so first-run users with yt-dlp
# installed see YouTube is already available. See #394.
HAS_YTDLP=""
if command -v yt-dlp &>/dev/null; then
  HAS_YTDLP="yes"
fi

# If setup has never been run, show welcome message for new users
if [[ -z "$SETUP_COMPLETE" && -z "$CONFIG_FILE" && -z "${OPENAI_API_KEY:-}" && -z "${SCRAPECREATORS_API_KEY:-}" && -z "${AUTH_TOKEN:-}" && -z "${XAI_API_KEY:-}" ]]; then
  if [[ -n "$HAS_YTDLP" ]]; then
    # YouTube is already working via the on-system yt-dlp binary — don't list
    # it as something the wizard needs to unlock. See #394.
    cat <<'EOF'
/last30days: Ready to use. Run /last30days to get started — setup takes 30 seconds.
  Research any topic across Reddit, HN, X, YouTube, Polymarket (last 30 days).

Reddit, Hacker News, Polymarket, and YouTube (yt-dlp detected) work out of the box.
The setup wizard can unlock X/Twitter and more.
  Detected: yt-dlp is installed (YouTube transcripts ready, no setup needed).
EOF
  else
    cat <<'EOF'
/last30days: Ready to use. Run /last30days to get started — setup takes 30 seconds.
  Research any topic across Reddit, HN, X, YouTube, Polymarket (last 30 days).

Reddit, Hacker News, and Polymarket work out of the box.
The setup wizard can unlock X/Twitter, YouTube, and more.
EOF
  fi
  if [[ -n "$LAST_RUN_LINE" ]]; then
    echo "$LAST_RUN_LINE"
  fi
  exit 0
fi

# Setup done but check for ScrapeCreators
HAS_SCRAPECREATORS="${ENV_SCRAPECREATORS_API_KEY:-${SCRAPECREATORS_API_KEY:-}}"
HAS_X=""
if [[ -n "${ENV_AUTH_TOKEN:-${AUTH_TOKEN:-}}" && -n "${ENV_CT0:-${CT0:-}}" ]]; then
  HAS_X="yes"
fi
HAS_XAI="${ENV_XAI_API_KEY:-${XAI_API_KEY:-}}"
HAS_BSKY="${ENV_BSKY_HANDLE:-${BSKY_HANDLE:-}}"
HAS_EXA="${ENV_EXA_API_KEY:-${EXA_API_KEY:-}}"

# Count active sources
SOURCE_COUNT=2  # HN + Polymarket are always free
if [[ -n "$HAS_X" || -n "$HAS_XAI" ]]; then
  SOURCE_COUNT=$((SOURCE_COUNT + 1))
fi
# Reddit public JSON always works
SOURCE_COUNT=$((SOURCE_COUNT + 1))
if [[ -n "$HAS_YTDLP" ]]; then
  SOURCE_COUNT=$((SOURCE_COUNT + 1))
fi
if [[ -n "$HAS_EXA" ]]; then
  SOURCE_COUNT=$((SOURCE_COUNT + 1))
fi
if [[ -n "$HAS_BSKY" ]]; then
  SOURCE_COUNT=$((SOURCE_COUNT + 1))
fi
if [[ -n "$HAS_SCRAPECREATORS" ]]; then
  # Start with Reddit comments + TikTok + Instagram, subtract any in EXCLUDE_SOURCES.
  # Normalise EXCLUDED (lowercase + collapse whitespace around commas + strip outer
  # whitespace) so the matching mirrors pipeline.py's .strip().lower() parsing.
  SC_ADD=3
  EXCLUDED="${ENV_EXCLUDE_SOURCES:-${EXCLUDE_SOURCES:-}}"
  EXCLUDED_NORM=$(printf '%s' "$EXCLUDED" | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[[:space:]]*,[[:space:]]*/,/g; s/^[[:space:]]+//; s/[[:space:]]+$//')
  if [[ ",$EXCLUDED_NORM," == *",tiktok,"* ]]; then
    SC_ADD=$((SC_ADD - 1))
  fi
  if [[ ",$EXCLUDED_NORM," == *",instagram,"* ]]; then
    SC_ADD=$((SC_ADD - 1))
  fi
  SOURCE_COUNT=$((SOURCE_COUNT + SC_ADD))
fi

if [[ -n "$HAS_SCRAPECREATORS" ]]; then
  # Fully configured — compact ready message
  echo "/last30days: Ready — ${SOURCE_COUNT} sources active."
  echo "  Research any topic across social + market + web sources (last 30 days)."
  if [[ -n "$LAST_RUN_LINE" ]]; then
    echo "$LAST_RUN_LINE"
  fi
else
  # Setup done but missing ScrapeCreators — recommend it
  echo "/last30days: Ready — ${SOURCE_COUNT} sources active."
  echo "  Research any topic across social + market + web sources (last 30 days)."
  if [[ -n "$LAST_RUN_LINE" ]]; then
    echo "$LAST_RUN_LINE"
  fi
  echo "  Tip: Add ScrapeCreators for Reddit comments + TikTok + Instagram."
  echo "  100 free credits, no credit card — scrapecreators.com"
  echo "  last30days has no affiliation with any API provider."
fi

# The branches above end with `[[ -n "$LAST_RUN_LINE" ]] && echo ...`. When
# LAST_RUN_LINE is empty, that test returns 1 and is the script's last command,
# leaking exit=1 to callers (e.g. SessionStart hook drivers) despite no error.
exit 0
