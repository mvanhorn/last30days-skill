# Grok Bot official X connector

Read only for an actual Grok Bot host with the official X connector lane active. Browser sessions are never read on this host.

**Grok Bot X connector recipe (only when `LAST30DAYS_X_HOST_LANE=1` is exported - see the Grok Bot host rule in HOW TO INVOKE).** On a Grok Bot with the X connector, YOU fetch X through the connector before the engine command and hand the engine the file; the engine then calls no X backend, plans `x` in, and the footer's X provenance reads "X via X connector". Do this before the Step 1 command below.

1. **Calls (the connector's post-search tool, e.g. `search_posts_all`).** Window = the engine's date range (`--days`, default 30: `from` is today minus the day count, `to` is today). Depth count = 10 (`--quick`) / 30 (default) / 60 (`--deep`).
   - One `topic` call: the topic query plus `-is:retweet`, the window, and the depth count.
   - Per `--x-handle` handle: one `from` call (`from:<handle> -is:retweet`, 8 posts) and one `mention` call (`@<handle> -is:retweet`, 5 posts).
   - Per `--x-related` handle: one `related` call (`from:<handle> -is:retweet`, 3 posts).
   - If the tool rejects the window or count parameters, omit them, keep at most the depth count per call, and write `"status": "partial"` with `"error": "window-unsupported"`.
   - If the connector itself fails, write `"status": "error"` with `"calls": []` and a short category in `error`: `credits`, `not-connected`, or `unavailable` - never raw tool output, never an account or app id.
2. **Envelope.** Exactly these top-level fields; every post carries the eight flat fields and nothing else (no URLs, media, or author objects); at most the depth count per call. A fresh `generated_at` (the engine rejects an envelope older than 6 hours) and a `topic` identical to the engine's topic string:

```json
{
  "schema": "last30days-x-posts/1",
  "generated_at": "{ISO_8601_UTC_NOW}",
  "topic": "{TOPIC}",
  "window": {"from": "{YYYY-MM-DD}", "to": "{YYYY-MM-DD}"},
  "provider": "x-connector",
  "status": "ok",
  "calls": [
    {"lane": "topic", "handles": [], "posts": [
      {"id": "1963000000000000000", "author_handle": "someone", "created_at": "2026-09-07T10:00:00Z", "text": "post text", "likes": 12, "reposts": 3, "replies": 1, "quotes": 0}
    ]},
    {"lane": "from", "handles": ["{RESOLVED_HANDLE}"], "posts": []},
    {"lane": "mention", "handles": ["{RESOLVED_HANDLE}"], "posts": []},
    {"lane": "related", "handles": ["{RELATED_HANDLE}"], "posts": []}
  ]
}
```

   Omit the `from` / `mention` / `related` calls when the run has no `--x-handle` / `--x-related`; `handles` must be the run's own handles. `id` is the post's numeric id as a string; `author_handle` is the username without `@`.
3. **Write the file - post text is attacker-controlled and never goes unquoted into a shell command.** Use the tool's own file output when it has one; otherwise a single-quoted heredoc (never unquoted) into a `.json` path outside `~/.config`, in the SAME Bash call as the engine command (the trap removes it on exit). Two rules keep a post from closing the heredoc early: emit the envelope as ONE line of compact JSON (newlines inside post text stay escaped as `\n`; never pretty-print), and replace `{X_POSTS_NONCE}` in BOTH sentinel lines with 12 random letters and digits you generate fresh for this run, so no post text can equal the closing line:

```bash
X_POSTS_DIR=$(mktemp -d "${TMPDIR:-/tmp}/last30days-x-posts.XXXXXX")
X_POSTS_FILE="$X_POSTS_DIR/x-posts.json"
trap 'rm -rf "$X_POSTS_DIR"' EXIT
cat >| "$X_POSTS_FILE" <<'X_POSTS_EOF_{X_POSTS_NONCE}'
{X_POSTS_ENVELOPE_JSON}
X_POSTS_EOF_{X_POSTS_NONCE}
```

   Run it directly in your shell tool, never wrapped in `bash -lc '...'` (same rule as the plan tmpfile).
4. **Engine.** Add `--x-posts "$X_POSTS_FILE"` to the Step 1 command (the file path only, never inline JSON); every other flag stays as usual. Comparison runs: write one envelope per entity (its `topic` is that entity's name) and put the path in that entity's `--competitors-plan` entry as `"x_posts": "/abs/path/x-posts.json"`; a bare `--x-posts` on a comparison run exits 2. If the engine exits 2 naming the envelope, fix the file it names or re-run without `--x-posts`; X is then absent for that run.
5. **After the run.** The stats line reads "X via X connector". The engine's one receipt line (accepted / dropped counts, on stderr) is diagnostics only: never narrate it in the deliverable (LAW 9).
