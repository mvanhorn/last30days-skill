# feat(x): demote Grok CLI to opt-in backup

Stop using the Grok CLI as the default X backend. A leftover `~/.grok/auth.json` must never steal the X lane. Grok stays as a pin-only backup: off unless LAST30DAYS_X_BACKEND=grok or --x-backend grok.

### Requirements
- R1. Unpinned auto chain is bird → xai → xurl → xquik. Bird is first. Grok is not a member. Presence of ~/.grok/auth.json (ok, expired, or error) must not change which backend an unpinned run uses.
- R2. Grok remains a valid explicit selection: LAST30DAYS_X_BACKEND=grok and --x-backend grok. A pin forces grok with no failover. If grok is unusable, X is unconfigured and doctor/footer say so with the existing login hint.
- R3. Doctor "will use: grok" only when grok is pinned and the probe is OK or DEGRADED. Unpinned, grok may appear as unused opt-in ("available, unused — pin LAST30DAYS_X_BACKEND=grok"), never as the predicted winner.
- R4. get_x_source_status and get_x_source_with_method must prefer bird over xai/xurl/xquik when cookies are present. Grok wins only when the pin is grok.
- R5. Host docs stop presenting grok as the default keyless X path. Document it as opt-in backup. Default story is bird first, then xai / xurl / xquik.
- R6. Setup / first-run / prescriptions do not nag grok login as the fix for missing X. Cookie consent and paid keys remain the default prescriptions. Grok login is mentioned only as an optional pin.
- R7. Do not delete scripts/lib/grok_x.py, retrieve-judge-retry, or expires_at honesty. Pinned grok still uses them.
- R8. A machine with only a grok login (no cookies, no XAI/XQUIK, no xurl) has X unconfigured until the user pins grok. Footer: X skipped-unconfigured, not auth-failed-from-grok.
- R9. Tests cover the cases above; docs/changelog updated.

### Implementation units
U1 env.py: _X_BACKEND_ORDER = ("bird", "xai", "xurl", "xquik"); X_BACKEND_OPT_IN = ("grok",); X_BACKEND_KNOWN = ORDER + OPT_IN; pin uses KNOWN; unpinned walks ORDER only; get_x_source_status bird first, grok only if pinned; get_x_source_with_method bird before xai.

U2 backends.py / doctor.py / prescriptions.py: descriptor is auto ORDER then grok opt-in; unpinned collect-then-pick ignores opt-in; do not change _probe_grok honesty.

U3 SKILL.md, CONFIGURATION.md, README.md, README.pt-BR.md if needed, changelog.d: remove "sits ahead of the cookie path"; document bird → xai → xurl → xquik; pin grok to enable it.

U4 tests: unpinned grok-only empty; unpinned grok+bird → bird; unpinned bird+xai → bird; pin grok+store → ["grok"]; pin grok no store empty; doctor unpinned never predicts grok; descriptor parity treats grok as trailing opt-in.

### Tests T1–T7
T1 unpinned grok AUTH_OK, no other creds → X unconfigured
T2 unpinned grok AUTH_EXPIRED, no other creds → X unconfigured (not will-use grok)
T3 unpinned grok AUTH_OK + cookies → bird
T4 unpinned XAI_API_KEY + grok store, no cookies → xai
T4b unpinned XAI_API_KEY + cookies → bird
T5 pin grok AUTH_OK → grok no failover
T6 pin grok no store → error / grok login prescription
T7 docs match R5

### Keep-the-door-open (KTD)
1. grok_x.py stays untouched
2. x_judge.py stays untouched
3. expires_at honesty stays untouched
4. auth.x.ai is never called
5. bird cookie extraction is unchanged
