# Next30Days: forecasts and the scorecard

Read for scorecard, ledger, or grading intent: "how accurate are your forecasts", "show my scorecard", "grade your predictions", or a research run that shows a prediction as due. Also read for launch-rehearsal intent ("how will this land", "roast my launch post", "what will people say about this draft"); that procedure is the Rehearsal section below. The runtime interpreter bootstrap still runs first.

Research says what happened in the last 30 days. A prediction commits to what happens next, in writing, with a probability and a date, and the ledger grades it later. Predictions are part of every ordinary research run, not a separate mode: the run's closing `Next 30 days` section is where they are chosen, checked, and logged, and `synthesis.md` specifies it. A request such as "forecast X" or "what happens next with X" is an ordinary research run on X. This reference covers the ledger: its commands, the grading rules, and the scorecard. You write and grade the predictions; the engine validates, stores, settles, and scores them. Every ledger command takes the same `--save-dir="${LAST30DAYS_MEMORY_DIR}"` as the library and queue commands; the ledger lives only in that directory, and with no save directory nothing is read or logged. Setting `LAST30DAYS_NEXT30DAYS=off` turns predictions off: runs print no prediction block and `forecast add` writes nothing.

## Scorecard, list, and grading fast paths

For scorecard, ledger, or grading intent with no new topic, do not run setup, host WebSearch resolution, topic preflight, or fresh topic research. Run, in order:

```bash
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast settle --save-dir="${LAST30DAYS_MEMORY_DIR}"
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast due --save-dir="${LAST30DAYS_MEMORY_DIR}"
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast scorecard --save-dir="${LAST30DAYS_MEMORY_DIR}"
```

- `forecast settle` checks every open market-anchored prediction against Polymarket's public API and grades the ones whose market has resolved. It is the only ledger command that uses the network.
- `forecast due` lists predictions whose date has arrived and still have no verdict. Grade them (below) before presenting the scorecard.
- `forecast scorecard` prints the record in a fixed order: the logged, overdue, and small-sample lines; the calls that departed from a market baseline and their skill against the market (with `The market was closer than these calls.` when that skill is negative); the calls that departed from a host-stated baseline; the calls that agreed with their baseline; legacy and rehearsal calls, counted with no skill figure; then all graded calls with hit rate, Brier score, and calibration. Relay it verbatim, including the overdue and small-sample lines. Never round the record up, drop a miss, or describe the record more favorably than the block does. An empty ledger is a valid answer.
- `forecast list` (optionally `forecast list <topic>`) prints every prediction with its id and status.

## Grading due predictions

For each prediction from `forecast due`, and each `DUE` line in a research run's `## Earlier Calls` block, find out what actually happened. Use the prediction's own `resolves when` line as the test, and check it against a primary page: host web search, or a fresh `/last30days` run on the topic when the answer needs community evidence. Then write one grade file and submit it:

```bash
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast grade --forecast-file - --save-dir="${LAST30DAYS_MEMORY_DIR}" <<'GRADE_EOF'
{"grades": [
  {"id": 12, "verdict": "hit", "evidence_url": "https://example.com/the-page-that-proves-it"},
  {"id": 13, "verdict": "miss", "evidence_url": "https://example.com/the-page-that-shows-it-did-not-happen", "note": "Shipped as a beta, not a stable release."},
  {"id": 14, "verdict": "void", "note": "The product was renamed; the claim can no longer be judged."}
]}
GRADE_EOF
```

Grading rules, enforced by the engine:

- `hit` and `miss` require an `evidence_url` you actually opened. Never guess, reconstruct, or reuse the prediction's own basis URL as proof of the outcome.
- A `miss` cannot be written before the prediction's date. A `hit` can land early. The one exception is `forecast settle`: a market that has resolved against the call is final, so that miss is recorded when the market resolves.
- A verdict is written once and cannot be changed, and predictions cannot be edited or deleted. Grade only what the evidence settles. If you cannot establish the outcome, leave the prediction ungraded and say so; it stays visible as overdue.
- `void` is for a claim that became unjudgeable, never for a call that went badly. A wrong prediction is a `miss`.
- Grade against the claim as written. Close does not count: "by Dec 1" that happened Dec 3 is a miss.

## Predictions on a research run

There is no separate forecast procedure. Every ordinary topic or comparison run ends with one or two predictions on that run's open questions, or with an item to watch when no question supports one, and `synthesis.md` holds the whole procedure: choosing a question, the baseline, the checks, the submission, and the presentation. Do not write predictions from the brief alone, and do not log a call on anything the run's `## Open Questions` list does not contain.

`forecast add` is the command that section submits with. Its file names each question by its id in the run's list:

```bash
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast add --forecast-file - --save-dir="${LAST30DAYS_MEMORY_DIR}" <<'FORECAST_EOF'
{"topic": "OpenClaw", "predictions": [
  {"question": "Q1",
   "outcome": "No",
   "claim": "The Polymarket market on an OpenClaw acquisition this year resolves No",
   "probability": 0.7,
   "reason": "Both named acquirers publicly walked away this month"},
  {"question": "Q2",
   "claim": "OpenClaw ships no stable 2.0 release by its roadmap date",
   "probability": 0.6,
   "reason": "The release branch still has three open blockers a week out",
   "baseline": {"status_quo": "No stable 2.0 release ships", "probability": 0.8},
   "backs": "status_quo",
   "resolution": {"page": "https://github.com/example/openclaw/releases",
                  "field": "A non-prerelease v2.0.0 tag is listed"}}
]}
FORECAST_EOF
```

- A market question takes only `outcome`. The engine fills the market's link, price, and closing date from its own list; the price is the call's baseline, and the call settles automatically.
- Any other question takes `baseline` (the `status_quo` outcome and its `probability`), `backs` (`status_quo` or `change`), and `resolution` (the `page` and the `field` or event on it that settles the call).
- `probability` is 0.5 to 0.99 on the claim as stated. The question's own date is the deadline.
- The engine exits 2 and writes nothing for a third prediction in one file, an id that is not in the topic's stored list, a list that has expired (run the topic again), a market call within 5 points of the market's price on the same outcome, and a call above 0.85 that the status quo holds.
- A question takes one open call. For a question that already has one, the engine returns the existing call instead of logging a second.
- When the engine prints `Not logged:` (no save directory is in effect, or Next30Days is turned off), nothing was written. Never present a prediction as logged unless the engine printed it with an id.

## Rehearsal: forecast the reception of the user's own draft

Read this section for launch-rehearsal intent: the user supplies something they have not shipped yet (a launch post, tweet or thread, landing page, README, pitch, product name, pricing page) and asks how it will land, what people will say, to roast it, to pressure-test it, or to rehearse the launch. A rehearsal is a forecast whose subject is the user's own draft. It answers from how real communities reacted to comparable things in the last 30 days, never from general intuition about audiences.

**Keep the draft private.** Unreleased work is confidential by default. Never place the draft's text, the unannounced product or company name, pricing, or any distinctive phrase from it into an engine topic, a query plan, a WebSearch query, or any other outbound request. Search for the category and for the analogs only. The single exception is a name the user says is already public. Do not save, publish, or export the draft, and do not log it to the ledger verbatim.

1. **Read the draft and state the pitch.** In two or three sentences: what it is, who it is for, the one claim it leads with, the price or ask, and where the user plans to post it. If the draft is a file or URL, read it with the host's own tools. If the venues are unknown, ask once which communities they are launching to, then continue with the likely ones when there is no answer.
2. **Pick 2 or 3 analogs.** An analog is a real, named launch or announcement from the last 30 days in the same category, aimed at the same communities, that people actually discussed. Find candidates with host web search on the category (not the draft), or from this conversation's research. Prefer one that landed well and one that landed badly. If no analog from the window can be found, say so and stop; do not rehearse against invented or older launches.
3. **Research how the analogs landed.** Run them as a comparison (`{Analog A} vs {Analog B}`), returning to the root to load the comparison route, or as ordinary topic runs when there is only one. Resolve handles and subreddits for the venues the user is launching to, so the evidence comes from the rooms the draft will enter.
4. **Mine the reactions, per venue.** From the top-voted comments and replies, collect: the objections that recur and how many votes they drew, what earned praise, what the launcher's own replies did to the thread, and what separated the launch that landed from the one that did not. Count votes, not mentions.
5. **Present the rehearsal** after the comparison synthesis, under the prose label `Rehearsal:`.
   - **One paragraph per venue** the user named: the likely reception there and the two or three specific things people will say. Every predicted reaction cites a real comment on an analog, quoted verbatim with its link, under the root citation law. Give the vote count when the source reports one; where it does not (Hacker News comments carry no points), say the count is unavailable and never supply a number. A predicted reaction with no supporting comment is cut.
   - **What will work**, on the same evidence standard.
   - **The single change with the most leverage**, stated as an edit to the draft: the objection it removes and the analog thread that shows it.
   - **What the evidence cannot tell you.** Name the venues with thin or no analog evidence and say the rehearsal is silent on them. Analog reactions are evidence about the room, not a guarantee about this launch.
6. **Offer to put it on the record.** Ask whether to log the rehearsal's predictions so they can be graded after launch. On yes, log them with `forecast add` as a rehearsal file: `"kind": "rehearsal"` beside `topic`, no `question`, and each prediction carrying `claim`, `probability`, `resolve_by`, `resolution` (the observable test, as one sentence), and `basis`. The `topic` is a neutral label the user approves (for an unannounced product, a codename, never the real name), ask for the planned launch date when it is unknown and set every `resolve_by` explicitly to a date after it (the 30-day default counts from today and would expire before a later launch; a launch too far out to resolve within the ledger's 90-day limit cannot be logged yet, so say so and offer to log it closer to the date), each `claim` describes an observable outcome within 30 days of that launch date ("the top-voted comment on the launch thread raises pricing"), and `basis` holds 1 to 6 analog comment URLs. Never log a prediction about reception without the user's yes.

Be direct about weaknesses; a rehearsal that flatters is worthless. Criticize the draft, never the person, and do not invent hostility the analog threads do not show.

## What a forecast is not

These are probabilistic reads of public evidence, written down so they can be checked. They are never financial, betting, legal, or medical advice, and never a reason to trade. When a forecast concerns a ticker, a market position, or an election, say plainly that it is a logged prediction and not a recommendation.
