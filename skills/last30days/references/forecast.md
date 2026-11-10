# Next30Days: forecasts and the scorecard

Read for forecast, prediction, or scorecard intent: "what happens next with X", "forecast X", "predict the next 30 days for X", "next30 X", "how accurate are your forecasts", "show my scorecard", "grade your predictions". Also read for launch-rehearsal intent ("how will this land", "roast my launch post", "what will people say about this draft"); that procedure is the Rehearsal section below. The runtime interpreter bootstrap still runs first.

Research says what happened in the last 30 days. A forecast commits to what happens in the next 30, in writing, with a probability and a date, and the ledger grades it later. You write the predictions; the engine validates, stores, settles, and scores them. Every ledger command takes the same `--save-dir="${LAST30DAYS_MEMORY_DIR}"` as the library and queue commands; the ledger lives only in that directory, and with no save directory nothing is read or logged.

## Scorecard, list, and grading fast paths

For scorecard, ledger, or grading intent with no new topic, do not run setup, host WebSearch resolution, topic preflight, or fresh topic research. Run, in order:

```bash
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast settle --save-dir="${LAST30DAYS_MEMORY_DIR}"
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast due --save-dir="${LAST30DAYS_MEMORY_DIR}"
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast scorecard --save-dir="${LAST30DAYS_MEMORY_DIR}"
```

- `forecast settle` checks every open market-anchored prediction against Polymarket's public API and grades the ones whose market has resolved. It is the only ledger command that uses the network.
- `forecast due` lists predictions whose date has arrived and still have no verdict. Grade them (below) before presenting the scorecard.
- `forecast scorecard` prints hit rate, Brier score, calibration, and the comparison against market prices. Relay it verbatim, including the overdue and small-sample lines. Never round the record up, drop a miss, or describe the record more favorably than the block does. An empty ledger is a valid answer.
- `forecast list` (optionally `forecast list <topic>`) prints every prediction with its id and status.

## Grading due predictions

For each prediction from `forecast due`, find out what actually happened. Use the prediction's own `resolves when` line as the test, and check it against a primary page: host web search, or a fresh `/last30days` run on the topic when the answer needs community evidence. Then write one grade file and submit it:

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

## Making a forecast for a topic

1. Run ordinary topic research first, through the research-runbook and synthesis routes, exactly as for any topic. Present that synthesis as usual. When this conversation already holds a fresh run on the topic, use it instead of re-running.
2. Run `forecast list <topic>` so open calls on this topic are in front of you. A question takes one open call: the engine returns the existing call instead of logging a second, and it finds calls logged under a renamed form of the topic.
3. Write 1 or 2 predictions (the engine refuses more than 2 per file). Each one targets one entry in this run's Open Questions list, named by its id in `question`, and must be:
   - **Falsifiable.** `claim` is one specific event that a stranger could check on the question's date. "Sentiment will stay mixed" and "adoption will grow" are not predictions. The question's own date is the deadline; you do not set one.
   - **Stated in the direction you believe**, with `probability` from 0.5 to 0.99. To predict that something will not happen, write the negative claim.
   - **Worth making.** `reason` is one line on why the call says more than its baseline. The engine refuses a restatement: a market call within 5 points of the market's price on the same outcome, or a call above 0.85 that the status quo holds.
   - **Honestly calibrated.** Reserve 0.9 and above for calls you would be shocked to lose.
4. For a market question, add only `outcome`: the label you are backing. The engine fills the market's link, price, and closing date from its own list and ignores any you send; the price is the call's baseline, and the call settles automatically. Never anchor to a market that has already resolved. For any other question, state the baseline and how the call settles: `baseline` gives the `status_quo` outcome and its `probability`, `backs` says whether your claim is that outcome (`status_quo`) or its opposite (`change`), and `resolution` names the `page` (an http or https link) and the `field` or event on it that settles the call.
5. Log them:

```bash
"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" forecast add --forecast-file - --save-dir="${LAST30DAYS_MEMORY_DIR}" <<'FORECAST_EOF'
{"topic": "OpenClaw", "predictions": [
  {"question": "Q2",
   "claim": "OpenClaw ships no stable 2.0 release by its roadmap date",
   "probability": 0.6,
   "reason": "The release branch still has three open blockers a week out",
   "baseline": {"status_quo": "No stable 2.0 release ships", "probability": 0.8},
   "backs": "status_quo",
   "resolution": {"page": "https://github.com/example/openclaw/releases",
                  "field": "A non-prerelease v2.0.0 tag is listed"}},
  {"question": "Q1",
   "outcome": "No",
   "claim": "The Polymarket market on an OpenClaw acquisition this year resolves No",
   "probability": 0.95,
   "reason": "Both named acquirers publicly walked away this month"}
]}
FORECAST_EOF
```

On exit 2 the engine names the failing prediction and writes nothing; fix the file and resubmit once. An id that is not in the topic's stored list, or a list that has expired, also exits 2: run the topic again first. When the engine prints `Not logged:` (no save directory is in effect, or Next30Days is turned off), show the predictions marked as not logged. Never present a prediction as logged unless the engine printed it with an id.

6. After the research synthesis and its footer, present the forecast under the prose label `Next 30 days:` as a numbered list. Each item gives the claim, the probability as a percentage, the resolve date, the baseline, and one sentence of reasoning tied to cited evidence. Follow it with the scorecard's hit rate and Brier lines when the ledger already has graded calls, then one line saying these are logged and will be graded on their dates. The root output laws still apply: no em or en dashes, no `##` headers.

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
