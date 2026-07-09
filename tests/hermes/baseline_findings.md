# Hermes scan baseline — skills/last30days/ (real skills_guard.py, community source)

Measured 2026-07-06 against `fix/hermes-scan-safe-verdict` (off origin/main @ 3.11.0).
Verdict: **dangerous** — BLOCKED (community + dangerous; --force powerless).
Totals: 14 CRITICAL, 36 HIGH, 25 MEDIUM, 1 LOW (76 findings).

## CRITICAL (14) — all clear-able (target: zero → caution)
- 7  exfiltration  python_environ_get_secret   os.environ.get("...API_KEY") reads (env boundary)
- 4  exfiltration  ruby_env_secret             Ruby ENV[] rule firing on Python `env[key]=` (env boundary + rename)
- 2  exfiltration  env_exfil_httpx             xquik.py:144,283  http.get(..., headers={"X-Api-Key": token}) (extract headers)
- 1  injection     deception_hide              SKILL.md:529  "do not tell the user a project file is active" (reword)

## HIGH (36) — includes an UNAVOIDABLE structural finding
- 26 exfiltration  python_os_environ           any `os.environ` substring incl comments (env boundary; blocks SAFE)
- 4  priv-esc      sudo_usage                  SKILL.md:374, last30days.py:34, env.py:247, health.py:148 ("sudo")
- 2  exfiltration  node_process_env            vendored bird-search JS (vendor exclude)
- 1  structural    oversized_skill             1615KB > 1024KB limit  ← BLOCKS SAFE (skill is legitimately ~1.5MB runtime)
- 1  exfiltration  dump_all_env                SKILL.md:327  "printenv ..." shell snippet
- 1  exfiltration  context_exfil               reddit.py:103  comment "include more context"
- 1  exfiltration  ssh_dir_access              youtube_yt.py:172  docstring "~/.ssh/config"

## Feasibility conclusion
- SAFE (zero HIGH) requires clearing `oversized_skill`, which is only possible by .skillignore-ing
  ~500KB of core runtime .py (evasive; contradicts R5) or shrinking the skill below 1MB (infeasible).
- CAUTION (zero CRITICAL) is cleanly reachable and honest; --force then installs.
- Structural limits: too_many_files 101>50 (MEDIUM, irrelevant); oversized_skill 1615KB>1024KB (HIGH).
