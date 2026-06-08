# Search Quality Eval

`skills/last30days/scripts/evaluate_search_quality.py` is an optional local evaluation step for retrieval quality. It is not part of the user-facing runtime and does not need to run in CI by default.

What it does:

- runs a baseline revision (default `origin/main`) against a candidate checkout
- evaluates the fixed 5 reviewer topics by default
- computes deterministic stability metrics:
  - `Jaccard` overlap vs baseline
  - retention vs baseline
  - per-source counts and overlap
- optionally calls Gemini as a judge for graded relevance labels and then computes:
  - `Precision@5`
  - `nDCG@5`
  - source-coverage recall across the judged union pool

Recommended usage:

```bash
uv run python skills/last30days/scripts/evaluate_search_quality.py
```

Useful flags:

```bash
uv run python skills/last30days/scripts/evaluate_search_quality.py \
  --baseline-rev origin/main \
  --candidate-rev HEAD \
  --no-default-topics \
  --topic "cursor IDE pricing" \
  --per-source-limit 5
```

Gemini configuration:

- preferred on this workspace: set `GOOGLE_API_KEY`
- also accepted: `GEMINI_API_KEY` or `GOOGLE_GENAI_API_KEY`
- optional: set `GEMINI_MODEL`
- default model is `gemini-3-pro-preview` for the direct Gemini API

Notes:

- The script forces a clean env-based auth path when it shells out to `last30days.py`.
- It passes `XAI_API_KEY`, `OPENAI_API_KEY`, and `SCRAPECREATORS_API_KEY`, but intentionally does not pass browser-cookie X auth. That keeps evaluation runs on the popup-free path.
- It also strips `node` from the eval `PATH` and wraps `yt-dlp` with `--ignore-config`, so older revisions do not inherit local browser-cookie config either.
- `Jaccard` and retention are regression guards, not truth metrics.
- `Precision@5` and `nDCG@5` are only as good as the judged pool. They help compare revisions, but they are not a substitute for a larger labeled benchmark.
