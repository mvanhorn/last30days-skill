# Reddit session offer

Read when the root route for the Reddit session offer applies: a first-run setup right after the X cookie answer (yes or skip), or a returning user whose first-run gate printed `REDDIT_OFFER`. On a Grok Bot host (`LAST30DAYS_HOST=grok-bot`) never ask this question, never run these commands, and return to the original request.

The Reddit session is the logged-in layer of free Reddit: search runs as the user's own reddit.com account, which finds more threads and comments and returns live scores. It needs its own yes. X consent never implies it. A `BROWSER_CONSENT=false` saved by skipping X does not block an explicit Reddit yes; `FROM_BROWSER=off` still does.

## Ask once

Ask this as its own question after the X answer, never folded into the X question. Ask it once: either answer records `REDDIT_SESSION_OFFERED=true`, and the gate then stops printing `REDDIT_OFFER`.

Question text (reproduce it verbatim):

"Separate from any X access: may I read your browser's reddit.com login cookie so Reddit search runs as your own account, now and on later research runs? Logged in, Reddit finds more threads and comments and shows live scores. The engine then acts as your own Reddit account, and automated use of a logged-in account may breach Reddit's terms and put that account at risk. Only the reddit_session cookie is read, it is sent only to reddit.com, and setup saves the browser name, never the cookie value. Scheduled runs skip it unless you opt in. Turn it off any time with REDDIT_BROWSER_CONSENT=false in ~/.config/last30days/.env. Use my Reddit login?"

- Modal hosts (Claude Code): call AskUserQuestion with that question and two options: "Yes - use my Reddit login" (description: "Logged-in Reddit search as your own account; may breach Reddit's terms.") and "No - keep Reddit logged out" (description: "Free Reddit search keeps working without a login.").
- Non-modal hosts: ask the same text as its own message ending `(yes / no)`. **Wait for the answer.**

A skip, an unrelated reply or no answer is a no. Never read a reddit.com cookie without this yes.

## Run the answer

- On yes: run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --reddit-session` and read the `status` field of its JSON:
  - `found`: say the logged-in Reddit session is on, using the named `browser`.
  - `not-found`: consent is saved. Check `checked` before suggesting a new login. On `error`, say the browser's login could not be checked. If Windows Firefox on WSL could not be read, suggest fully quitting Windows Firefox, then retrying `setup --reddit-session`. On `permission-denied`, give the macOS Privacy & Security advice from the setup reference if applicable. On `unsupported-encryption`, suggest Firefox. When the browser was read successfully and says `not-logged-in`, suggest logging in to reddit.com there and rerunning setup.
  - `vetoed`: a saved refusal blocks every browser read, so nothing was read. Name the `reason` once. Do not remove the refusal yourself.
  - `unavailable`: a Grok Bot host; say nothing about Reddit login.
- On no: run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --no-reddit-session`. It records the refusal and the offer. Free Reddit keeps working.

Never print, ask for or echo a cookie value. Then return to the step that sent you here: the next first-run step, or the returning user's original request (research a waiting topic right away; do not show the welcome or any other onboarding step).
