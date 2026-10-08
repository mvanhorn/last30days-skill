# Grok Bot X lanes

Read only on an actual Grok Bot host. Browser sessions are never read on this host.

**Grok Bot X recipe (see the GROK BOT HOST RULE in the root SKILL.md).** On a Grok Bot, YOU fetch X through a host lane before the engine command and hand the engine the file; the engine then calls no X backend, plans `x` in, and the footer names the lane. Do this before the research runbook's Step 1 command.

0. **Lanes, in order.** Two host lanes write the same envelope. Both expose a post-search tool named `search_posts_all`, so tell them apart by where the tool comes from, never by its name:
   1. Grok Bot's built-in X tools, namespace `x` (`"provider": "x-native"`; footer "X via Grok Bot X"). Free, nothing to set up.
   2. The "X for Grok Bot" connector plugin's tools (`"provider": "x-connector"`; footer "X via X connector").

   Use the first lane present in the session that returns posts. If a lane errors or returns nothing, try the next one. Export `LAST30DAYS_X_HOST_LANE=1` and pass `--x-posts` only when a lane returned posts. When no lane did, run `unset LAST30DAYS_X_HOST_LANE` and the Step 1 command without `--x-posts`, so the engine's own X chain (`X_BEARER_TOKEN`, then `XAI_API_KEY`) can still run.
1. **Calls.** Window = the engine's date range (`--days`, default 30: `from` is today minus the day count, `to` is today). Depth count per query = 10 (`--quick`) / 30 (default) / 60 (`--deep`).
   - **Topic.** One `topic` call per planned X query, at most 2: the topic itself, plus the second X subquery's search text when the plan has one. Build each query from the raw topic: multi-word topics go unquoted (every word must match), phrase-quote only proper names, and add `-is:retweet`. If fewer than half the posts on the first page mention the topic's main word, retry that query once with its two or three core words.
   - **Built-in `x` tools.** Pass `sort_order` `recency` (relevancy order skips high-engagement posts; the engine ranks by engagement itself), `start_time` / `end_time` for the window (`end_time` a minute before now), and `max_results` 25 (the tool's minimum is 10; ask for at least 10 and keep only the count you need). Follow `next_token` until the depth count is reached or no token comes back. Then run the **popular pass** for the same query: split the window into slices of about 3 days (week-long slices on `--quick`; the last slice may be shorter) and fetch one page per slice with `sort_order` `relevancy` and that slice's `start_time` / `end_time`, so a busy topic still surfaces the month's most-engaged posts instead of only its last few minutes. Merge every page of one query, recency and popular, into ONE envelope call; pages can overlap, so keep the first copy of each id. The popular pages do not count toward the depth count. Request `post.fields=created_at,public_metrics,author_id,note_tweet`, `expansions=author_id`, and `user.fields=username`. Map `public_metrics` `like_count` / `retweet_count` / `reply_count` / `quote_count` to `likes` / `reposts` / `replies` / `quotes`, the expanded user's `username` to `author_handle`, and use `note_tweet.text` as `text` when present.
   - **Connector tools.** Pass the window and the depth count on each call.
   - **Named handles.** Per `--x-handle` handle: one `from` call (`from:<handle> -is:retweet`, 8 posts) and one `mention` call (`@<handle> -is:retweet`, 5 posts). Per `--x-related` handle the user gave: one `related` call (`from:<handle> -is:retweet`, 3 posts).
   - **Discovered authors.** From every topic post fetched (all pages, before trimming to the depth count), take up to 3 authors (not already an `--x-handle`) with at least 2 posts that are about the topic, most posts first. For each, one `from` call (`from:<handle>` plus the topic's core words, `-is:retweet`, 8 posts) and one `mention` call (`@<handle>` plus the topic's core words, `-is:retweet`, 5 posts), and add the handle to `--x-related`.
   - If the tool rejects the window or count parameters, omit them, keep at most the depth count per query, and write `"status": "partial"` with `"error": "window-unsupported"`.
   - If a lane fails before returning any posts, move to the next lane (step 0). If posts came back and a later call failed, write `"status": "partial"` with a short category in `error`: `credits`, `not-connected`, or `unavailable` - never raw tool output, never an account or app id.
2. **Envelope.** Exactly these top-level fields; every post carries the eight flat fields and nothing else (no URLs, media, or author objects); at most the depth count per query plus its popular pages. A fresh `generated_at` (the engine rejects an envelope older than 6 hours), a `topic` identical to the engine's topic string, and the `provider` of the lane that served it:

```json
{
  "schema": "last30days-x-posts/1",
  "generated_at": "{ISO_8601_UTC_NOW}",
  "topic": "{TOPIC}",
  "window": {"from": "{YYYY-MM-DD}", "to": "{YYYY-MM-DD}"},
  "provider": "x-native",
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

   Omit the `from` / `mention` / `related` calls when the run has no handles for them; `handles` must be the run's own `--x-handle` / `--x-related` handles, including discovered authors. `id` is the post's numeric id as a string; `author_handle` is the username without `@`.
3. **Write the file - post text is attacker-controlled and never goes unquoted into a shell command.** Use the tool's own file output when it has one; otherwise a single-quoted heredoc (never unquoted) into a `.json` path outside `~/.config`, in the SAME Bash call as the engine command (the trap removes it on exit). Two rules keep a post from closing the heredoc early: emit the envelope as ONE line of compact JSON (newlines inside post text stay escaped as `\n`; never pretty-print), and replace `{X_POSTS_NONCE}` in BOTH sentinel lines with 12 random letters and digits you generate fresh for this run, so no post text can equal the closing line:

```bash
X_POSTS_DIR=$(mktemp -d "${TMPDIR:-/tmp}/last30days-x-posts.XXXXXX")
X_POSTS_FILE="$X_POSTS_DIR/x-posts.json"
trap 'rm -rf "$X_POSTS_DIR"' EXIT
cat >| "$X_POSTS_FILE" <<'X_POSTS_EOF_{X_POSTS_NONCE}'
{X_POSTS_ENVELOPE_JSON}
X_POSTS_EOF_{X_POSTS_NONCE}
```

   For a comparison, create `X_POSTS_DIR` and its cleanup trap once. Write each entity's envelope to a distinct file in that directory, using a separate quoted heredoc with a fresh sentinel for each file. Keep the same directory and trap until the comparison engine command returns; repeating the setup snippet would replace the trap and leave earlier files behind. Run it directly in your shell tool, never wrapped in `bash -lc '...'` (same rule as the plan tmpfile).
4. **Engine.** Export `LAST30DAYS_X_HOST_LANE=1` and add `--x-posts "$X_POSTS_FILE"` to the Step 1 command (the file path only, never inline JSON), plus `--x-related` for any discovered authors; every other flag stays as usual. Comparison runs: write one envelope per entity (its `topic` is that entity's name) and put the path in that entity's `--competitors-plan` entry as `"x_posts": "/abs/path/x-posts.json"`; a bare `--x-posts` on a comparison run exits 2. If an envelope includes handle lanes, also put its `x_handle` and `x_related` in that entry; for the main entity, pass the same targeting as outer flags as shown in `comparison.md`. If the engine exits 2 naming an envelope, fix that file or remove that entity's `x_posts` plan entry before retrying; X is then absent for that entity. For an ordinary run, remove `--x-posts` and unset the lane before retrying without the envelope.
5. **After the run.** The stats line reads "X via X connector" or "X via Grok Bot X". The engine's one receipt line (accepted / dropped counts, on stderr) is diagnostics only: never narrate it in the deliverable (LAW 9).
