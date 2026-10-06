# Runtime and configuration

Read before any engine command. Bind `SKILL_DIR` to the loaded root SKILL.md before running these snippets. A reference directory is never an install root.

# STEP 0: STALE-CLONE SELF-CHECK — RUN BEFORE READING BELOW

Before reading anything else in this file, check whether you loaded SKILL.md from the one known stale-clone location: Claude Code's marketplaces directory.

**Run this check:**

```bash
CLAUDE_CACHE_LATEST=$(find "$HOME/.claude/plugins/cache/last30days-skill/last30days" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -1)
# Two cache layouts ship in the wild — nested ({version}/skills/last30days/SKILL.md)
# and flat ({version}/SKILL.md). Resolve to whichever shape actually exists.
CLAUDE_CACHE_SKILL_MD=""
if [ -n "$CLAUDE_CACHE_LATEST" ]; then
  if [ -f "$CLAUDE_CACHE_LATEST/skills/last30days/SKILL.md" ]; then
    CLAUDE_CACHE_SKILL_MD="$CLAUDE_CACHE_LATEST/skills/last30days/SKILL.md"
  elif [ -f "$CLAUDE_CACHE_LATEST/SKILL.md" ]; then
    CLAUDE_CACHE_SKILL_MD="$CLAUDE_CACHE_LATEST/SKILL.md"
  fi
fi
echo "CLAUDE_CACHE_SKILL_MD=$CLAUDE_CACHE_SKILL_MD"
```

If the loaded root SKILL.md path contains `/.claude/plugins/marketplaces/` AND `$CLAUDE_CACHE_SKILL_MD` is non-empty, STOP and re-read `$CLAUDE_CACHE_SKILL_MD` before proceeding. Otherwise the SKILL.md you have is fine — continue.

**Other install paths are fine:** `~/.codex/skills/`, `~/.agents/skills/`, an `npx skills add` install dir, or a repo checkout are all valid load points - the resolver in Step 1 picks them up. Do NOT abort or hop on those paths.

---


## Runtime Preflight

Before running any `last30days.py` command in this skill, resolve a Python 3.12+ interpreter once and keep it in `LAST30DAYS_PYTHON`:

```bash
try_last30days_python() {
  candidate="${1}"
  [ -n "$candidate" ] || return 1
  if [ -x "$candidate" ]; then
    :
  elif command -v "$candidate" >/dev/null 2>&1; then
    :
  else
    return 1
  fi
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || return 1
  LAST30DAYS_PYTHON="$candidate"
  return 0
}

windows_path_to_unix() {
  path="${1}"
  [ -n "$path" ] || return 1
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -u "$path"
  else
    printf '%s\n' "$path"
  fi
}

if [ -z "${LAST30DAYS_PYTHON:-}" ]; then
  while IFS= read -r windows_python_root; do
    [ -n "$windows_python_root" ] && [ -d "$windows_python_root" ] || continue
    while IFS= read -r py; do
      try_last30days_python "$py" && break 2
    done <<EOF_PYTHON_CANDIDATES
$(find "$windows_python_root" -maxdepth 2 -type f -iname python.exe 2>/dev/null | sort -r)
EOF_PYTHON_CANDIDATES
  done <<EOF_WINDOWS_PYTHON_ROOTS
$([ -n "${LOCALAPPDATA:-}" ] && printf '%s\n' "$(windows_path_to_unix "$LOCALAPPDATA")/Programs/Python")
$([ -n "${ProgramFiles:-}" ] && windows_path_to_unix "$ProgramFiles")
$([ -n "${PROGRAMFILES:-}" ] && windows_path_to_unix "$PROGRAMFILES")
$(program_files_x86="$(printenv 'ProgramFiles(x86)' 2>/dev/null || true)"; [ -n "$program_files_x86" ] && windows_path_to_unix "$program_files_x86")
EOF_WINDOWS_PYTHON_ROOTS
fi

if [ -z "${LAST30DAYS_PYTHON:-}" ]; then
  for py in python3.14 python3.13 python3.12 python3 python; do
    try_last30days_python "$py" && break
  done
fi

# uv fallback: on hosts without a system 3.12 but with `uv` on PATH (most agent
# sandboxes: Cowork, Codex, etc.), provision a managed 3.12 automatically instead
# of hard-failing. No-op when uv is absent — those hosts still hit the error below.
if [ -z "${LAST30DAYS_PYTHON:-}" ] && command -v uv >/dev/null 2>&1; then
  uv_py="$(uv python find '>=3.12' 2>/dev/null)"
  if [ -z "$uv_py" ] || [ ! -x "$uv_py" ]; then
    echo "NOTE: no Python 3.12+ found; installing a managed CPython 3.12 via uv (~28MB, one-time)." >&2
    if UV_HTTP_TIMEOUT=30 uv python install 3.12 >/dev/null 2>&1; then
      uv_py="$(uv python find '>=3.12' 2>/dev/null)"
    else
      echo "WARN: 'uv python install 3.12' failed (network, disk space, or proxy?); falling through to the version-gate error below." >&2
    fi
  fi
  try_last30days_python "$uv_py"
fi

if [ -z "${LAST30DAYS_PYTHON:-}" ]; then
  echo "ERROR: last30days v3 requires Python 3.12+. Install Python 3.12+ or set LAST30DAYS_PYTHON to a supported interpreter." >&2
  exit 1
fi

"${LAST30DAYS_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "ERROR: LAST30DAYS_PYTHON must point to Python 3.12+." >&2
  exit 1
}

```

**PYTHON VERSION GATE — when the Runtime Preflight Bash block above exits with a Python version error:**

If the preflight script (including the uv fallback above) emits `ERROR: last30days v3 requires Python 3.12+` (or `LAST30DAYS_PYTHON must point to Python 3.12+`) and exits, you MUST:

1. Display this message to the user:
   > "The last30days engine needs Python 3.12+. Your system has an older version. Install it with one command:
   > - **Mac:** `brew install python@3.12`
   > - **Windows:** `winget install Python.Python.3.12`
   > - **Linux:** `sudo apt install python3.12` (or `pyenv install 3.12`)
   >
   > Then re-run `/last30days <your topic>` and the setup wizard will configure everything automatically."
2. **Stop.** Do not attempt research. Do not fall back to WebSearch-only synthesis.

WebSearch-only synthesis is not equivalent to running the engine — it misses Reddit community data, X/Twitter timelines, YouTube transcripts, TikTok, and Polymarket. Presenting it without disclosure misleads the user about what was actually searched. This is the same category of failure as a WebSearch-only run with no engine footer.

**Native-search signal (web coverage).** If you (the hosting model) have your own web-search tool available, export `LAST30DAYS_NATIVE_SEARCH=1` in the same shell before invoking the engine:

```bash
export LAST30DAYS_NATIVE_SEARCH=1   # ONLY when you have a native web-search tool
```

Your host search is better than the engine's keyless web fallback, so this tells the engine to skip that fallback and leave general web to you (you already run web-search supplements in Step 2). If you have NO web-search tool in the agent session, do **not** set this: the engine's keyless web floor supplies general-web coverage automatically. The rule is capability-based, not host-name-based — set it only when you genuinely have a better search, never to suppress the floor on a host that has nothing else.

## Save-directory resolution

After interpreter bootstrap, resolve the save directory once before the first engine operation that needs it. If the user supplied a one-off directory, add `--save-dir="<requested directory>"` to the resolver command below before running it. Remove any duplicate save-directory flag from the later research arguments. Never source a `.env` file.

```bash
LAST30DAYS_MEMORY_DIR="$("${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" --resolve-save-dir)" || exit
printf 'Resolved save directory: <%s>\n' "$LAST30DAYS_MEMORY_DIR" >&2
```

Capture that exact value, including an empty string. Later library, discovery, and research recipes use this captured value and do not resolve it again. When shell calls do not share variables, restore the captured value explicitly. All discovery legs use the same value even if configuration or working directory changes.

## Configuration

Set `LAST30DAYS_MEMORY_DIR` in the process environment, trusted project config, or global `~/.config/last30days/.env` to choose where raw research files are saved. Resolution follows that order after an explicit `--save-dir`. Only an absent setting defaults the skill to `~/Documents/Last30Days`; an explicitly empty value disables research file saves. The engine creates a nonempty directory on first save.

The engine reads `LAST30DAYS_MEMORY_DIR` from either the process env or `~/.config/last30days/.env`, so direct CLI invocations (`python3 scripts/last30days.py ...`) without `--save-dir` will still save when the env var is set. Mirrors the `LAST30DAYS_STORE` env-or-flag convention. Explicit `--save-dir` always wins.

When both `LAST30DAYS_API_KEY` and `LAST30DAYS_API_BASE` are set, the engine runs the research through that configured remote API instead of local sources (unless `--mock` is passed); `LAST30DAYS_API_BASE` is the endpoint and has no built-in default, so leaving either variable unset runs local sources normally. A configured `--corpus` / `LAST30DAYS_CORPUS_DIRS` is the privacy exception: the engine bypasses the hosted backend and runs locally so no file-derived input is forwarded. An explicit `--perplexity-search-type` flag also runs locally when a direct `PERPLEXITY_API_KEY` exists, because the hosted backend cannot apply it; the engine prints one stderr notice. Without a direct key the run stays hosted and prints a note that the flag was not applied. A `LAST30DAYS_PERPLEXITY_SEARCH_TYPE` value set only in the environment keeps hosted routing and prints a note that it was not applied. The invocation is otherwise unchanged: same flags, `--quick`/`--deep` map to search depth, a non-default `--register` is forwarded for server-side synthesis, progress lines still stream on stderr (`[narrate] step=...` plus a compact elapsed/eta line), and the report prints on stdout and saves to the memory dir as usual, so Steps 1-4 proceed normally on the output. The exception is research JSON: the remote endpoint does not return the local `Report` needed for the versioned agent profile, so use `--emit=json --json-profile=raw` for its existing server-response JSON contract. No per-source keys or setup-wizard credentials are needed for the search itself in this mode. Two engine exits need specific handling: exit code 3 means the API asked a clarifying question first - the engine prints the question and options on stderr; present them to the user and re-run with the chosen angle folded into the topic. An insufficient-credits failure (HTTP 402) prints the account's balance, the amount needed, and a billing link - relay those lines to the user verbatim; do not fall back to WebSearch-only synthesis.

**Developer-only eval capture:** `--record-fixtures <dir>` is a hidden direct-engine flag for maintaining the deterministic research-quality suite. It records scrubbed HTTP and CLI-adapter responses to `<dir>/http.json`; it is never part of the user-facing slash-command invocation. Follow `docs/reference/eval.md` for fixture review, replay, and baseline rules.
