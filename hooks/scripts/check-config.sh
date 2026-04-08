#!/bin/bash
set -euo pipefail

# Check last30days configuration status and show appropriate welcome message.
# Priority: .claude/last30days.env > ~/.config/last30days/.env > env vars

PROJECT_ENV=".claude/last30days.env"
GLOBAL_ENV="$HOME/.config/last30days/.env"

# Helper: warn if file permissions are too open
check_perms() {
  local file="$1"
  if [[ ! -f "$file" ]]; then return; fi
  local perms
  perms=$(stat -f '%Lp' "$file" 2>/dev/null || stat -c '%a' "$file" 2>/dev/null || echo "")
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
      key=$(echo "$key" | xargs)
      value=$(echo "$value" | xargs | sed 's/^["'\''"]//;s/["'\''"]$//')
      if [[ -n "$key" && -n "$value" ]]; then
        eval "ENV_${key}=\"${value}\""
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

# If setup has never been run, show welcome message for new users
if [[ -z "$SETUP_COMPLETE" && -z "$CONFIG_FILE" && -z "${OPENAI_API_KEY:-}" && -z "${SCRAPECREATORS_API_KEY:-}" && -z "${AUTH_TOKEN:-}" && -z "${XAI_API_KEY:-}" ]]; then
  cat <<'EOF'
/last30days: Ready to use. Run /last30days to get started — setup takes 30 seconds.

Reddit, Hacker News, and Polymarket work out of the box.
The setup wizard can unlock X/Twitter, YouTube, and more.
EOF
  exit 0
fi

# Setup done but check for ScrapeCreators
HAS_SCRAPECREATORS="${ENV_SCRAPECREATORS_API_KEY:-${SCRAPECREATORS_API_KEY:-}}"
HAS_X="${ENV_AUTH_TOKEN:-${AUTH_TOKEN:-}}"
HAS_XAI="${ENV_XAI_API_KEY:-${XAI_API_KEY:-}}"
HAS_YTDLP=""
if command -v yt-dlp &>/dev/null; then
  HAS_YTDLP="yes"
fi
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
  SOURCE_COUNT=$((SOURCE_COUNT + 3))  # Reddit comments + TikTok + Instagram
fi

if [[ -n "$HAS_SCRAPECREATORS" ]]; then
  # Fully configured — compact ready message
  echo "/last30days: Ready — ${SOURCE_COUNT} sources active."
else
  # Setup done but missing ScrapeCreators — recommend it
  echo "/last30days: Ready — ${SOURCE_COUNT} sources active."
  echo "  Tip: Add ScrapeCreators for Reddit comments + TikTok + Instagram."
  echo "  10,000 free API calls, no credit card — scrapecreators.com"
  echo "  last30days has no affiliation with any API provider."
fi
