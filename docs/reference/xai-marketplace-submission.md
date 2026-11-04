# Submitting to the xAI plugin marketplace

Getting last30days into xAI's official catalog (`xai-org/plugin-marketplace`) is an outbound PR to *their* repo — an index that only points at our source, so nothing of last30days is vendored there. Do this **after** the change you want to ship has merged to `main`: the entry pins a commit that must already exist.

1. Fork `xai-org/plugin-marketplace` and branch from `main`.
2. Get the commit to pin — a full 40-char lowercase SHA; a branch, tag, or short SHA is rejected by their validator:
   ```bash
   git ls-remote https://github.com/mvanhorn/last30days-skill.git HEAD
   ```
3. Add one entry to their `.grok-plugin/marketplace.json` under `plugins[]`, a remote source pinned to that SHA:
   ```json
   {
     "name": "last30days",
     "description": "Research any topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, GitHub, and 5+ more sources. AI agent scores by upvotes, likes, and real money - not editors.",
     "category": "productivity",
     "source": {
       "source": "url",
       "url": "https://github.com/mvanhorn/last30days-skill.git",
       "sha": "<full-40-char-sha-from-step-2>"
     },
     "homepage": "https://github.com/mvanhorn/last30days-skill",
     "keywords": ["last30days", "last 30 days"]
   }
   ```
4. Regenerate their component index (never hand-edit it) and validate exactly as their CI does:
   ```bash
   python3 scripts/generate-plugin-index.py
   python3 scripts/validate-catalog.py
   python3 scripts/generate-plugin-index.py --check
   ```
5. Open the PR, fill in their template, and wait for code-owner review.

To roll out a later update in their catalog, bump the pinned `sha` in the existing entry — never open a second, parallel entry.

Do not confuse this with our own `.grok-plugin/marketplace.json`: that file makes this repo directly addable as a Grok marketplace (`grok plugin marketplace add mvanhorn/last30days-skill`) and uses a **bare URL** source (no SHA) so it tracks HEAD; the xAI entry above lives in *their* repo and uses a **remote** source pinned to a SHA.
