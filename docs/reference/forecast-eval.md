# Next30Days, Rehearsal, and Patient Zero: acceptance protocol

This is the manual acceptance bar for the three features that end a research brief with predictions (Next30Days), forecast the reception of an unshipped draft (Rehearsal), and trace where a story started (Patient Zero). It is a procedure only. Results for a given release go in that release's pull request, not here. The offline harness in [eval.md](eval.md) covers parsing, ranking, and determinism; it cannot judge whether a prediction was worth making, so these four runs do.

## Before you start

- The runs need a host session (Claude Code, Codex, or another host that drives the skill) and your real sources: web search, the X, YouTube, and TikTok sources, Reddit, Hacker News, and Polymarket. They are not part of continuous integration, and a run with a source missing does not count toward a criterion that depends on that source.
- Run from the working tree under test, with `/last30days <topic>` as the entry point. The engine invocation shown in `eval.md` is a development fallback; use it only to script a run.
- Keep acceptance runs out of a real library. Point the run at a scratch save directory and delete it afterward:

  ```bash
  export LAST30DAYS_MEMORY_DIR="$(mktemp -d)"
  ```

  The forecast ledger (`research.db`) follows the save directory, so predictions from these runs land in the scratch directory and never in your own ledger or scorecard. Rehearsal runs write no brief or cache regardless. A run also refreshes the per-user report cache and open-questions file under `~/.config/last30days/`; those expire on their own and hold no ledger entries.
- Use blind reviewers. A reviewer who sees which calls the engine liked, or who wrote the draft, is not blind. Give a reviewer the brief or the evidence named in each rubric and nothing else. Record each reviewer's raw ratings before anyone discusses them.

## Known weakness

A past date cannot reproduce past market or search state. When a backtest is replayed with an earlier date, the engine can filter the evidence it retrieves by date, but live prediction markets show today's odds and a web search ranks today's pages, so information from after the replay date can leak into the run. A backtest therefore cannot prove skill. It is a check against a repeat of an earlier version's result. Treat any backtest pass as weak evidence, say so in the results, and prefer the forward runs (the ten-topic run, and the ledger's own record over time) for confidence. Small samples add to this: three topics cannot separate skill from luck.

## Run 1: ten-topic forecast run

Checks that ordinary runs end with usable predictions and that they are not obvious.

Inputs: run `/last30days <topic>` once for each of Jensen Huang, Kanye West, OpenClaw, GLP-1, AI regulation, Seattle NBA, Arizona Wildcats, Ramp, Cursor, and Claude Code, with a scratch save directory and Next30Days on (the default). Do not use the words "forecast" or "what happens next" in the request.

Reviewer rubric: a blind reviewer reads each full brief, including its `Next 30 days:` section, and rates each prediction as one of:

- Already assumed: a reader of the brief would have guessed this call without the prediction.
- Not assumed: the call tells the reader something the brief did not already imply.

Also record, for each run, whether it ended with one or two predictions or with an item to watch, whether each prediction names a baseline and a resolve date, and whether any open question or prediction concerns a death, a person's health, violence against a person, or a private individual's personal life.

Pass criteria:

- At least eight of the ten runs end with one or two predictions, and the rest end with a watch item.
- The reviewer rates no prediction as already assumed.
- No run logs a prediction that only restates its baseline, and none touches the excluded subjects.

## Run 2: three-topic backtest replay

Checks recall (does the open-questions list contain the stories that mattered) and accuracy (do the calls hold up against what happened).

Inputs: Anthropic, Kanye West, and OpenClaw, replayed as of 2026-10-11. Use only evidence dated on or before that date: pass the replay date to the run, and have the reviewer discard any retrieved item, market, or page dated after it. Write each run's predictions from that evidence, then grade them against what happened using each call's own resolution test, citing a primary page for every verdict.

Reviewer rubric:

- Recall: the reviewer is given the list of stories the first version of the feature missed on these topics (for example a filing, a record-setting show, a product launch) and marks which of them appear in the replay's open-questions list.
- Accuracy: split the graded calls into those that departed from their baseline and those that agreed with it. For the departing calls, compare their hit rate and Brier score with the baseline's on the same questions.

Pass criteria:

- The open-questions list built from evidence dated on or before 2026-10-11 contains at least one of the stories the first version missed.
- The calls that departed from their baseline score no worse than the baseline did.

The sample is small and the replay can leak (see Known weakness), so this is a check against a repeat of the first version's result, not proof of skill.

## Run 3: three-launch rehearsal check

Checks that a rehearsal anticipates how a real launch was received.

Inputs: three launches whose reception is already known, each with its public launch post, in categories and venues where the comparable-launch search has material. For each, take the launch post as the draft (with the product name treated as private, as if unannounced), and run a rehearsal as of a date before the launch. Use the venues where the launch was posted. The analog launches must come from the 30 days before that date, so choose launches with comparable ones just before them.

Reviewer rubric: a blind reviewer is given the predicted reaction themes from the rehearsal and the five most-voted actual themes from the launch's real threads, and marks each actual theme as anticipated or not. Also check that every predicted reaction cites a real quoted comment on an analog, and that the private file was used (the run refused nothing it should have allowed, and wrote no cache or brief).

Pass criteria:

- On each of the three launches, the rehearsal anticipated at least half of the five most-voted reaction themes.
- Every predicted reaction is tied to a real quoted comment, and no draft text or private name appears in any engine topic, query plan, or search request.

## Run 4: three-story origin check

Checks that the spread timeline finds an origin near the documented one and states its limits honestly.

Inputs: three stories with a publicly documented origin (a first post or article that reporting or the platforms themselves identify), chosen so that the trail runs on X, YouTube, and TikTok data. Run `/last30days <story question>` with an origin-trace request for each, with all three platforms available.

Reviewer rubric: for each story, compare the earliest post found with the documented origin and record the gap in days. Then read the `Limits of this trace` lines and each platform's searched range, and mark each as accurate or not against what you know about the story: whether the trail really runs past the range searched, whether any platform failed to report, and whether any order shown is uncertain. Check that the trail names venues and not individual posters.

Pass criteria:

- The earliest post found is within three days of the documented origin for at least two of the three stories.
- The stated limits are accurate for all three.
- No individual poster's handle is printed in the trail.

## Recording results

For each run, record the date, the commit under test, the sources that were live, the reviewers' raw ratings, and pass or fail per criterion. Summarize all four runs in the pull request. A criterion that fails after two design iterations on the same cause is a reason to stop and report, not to loosen the criterion.
