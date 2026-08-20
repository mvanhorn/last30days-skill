---
title: "Optional-value argparse flags: dispatch on 'is not None', never truthiness"
date: 2026-07-12
category: conventions
module: last30days-cli
problem_type: convention
component: tooling
severity: medium
applies_when:
  - "Retrofitting an optional value onto an existing flag (nargs='?' + const), where old dispatch sites were written for a two-state flag"
  - "Any flag or config key where a falsy value ('', 0, []) is a meaningful present-value distinct from absence"
  - "Dependent/modifier flags whose behavior only applies when an anchor flag is present"
tags:
  - argparse
  - nargs-optional
  - truthiness
  - cli-flags
  - dispatch
  - dependent-flags
  - silent-failure
  - sentinel-values
related_components:
  - testing_framework
---

# Optional-value argparse flags: dispatch on `is not None`, never truthiness

## Context

PR #816 retrofitted an optional value onto the `--discover` flag in `skills/last30days/scripts/last30days.py`:

```python
parser.add_argument(
    "--discover",
    metavar="DOMAIN",
    nargs="?",
    const="",
    default=None,
    help=(
        "Sweep river listings and rank the topics accelerating in a domain; "
        "each survivor gets a full research pass. Bare --discover (no domain) "
        "runs global trending across every feed's hot list"
    ),
)
```

With `nargs="?"` plus `const=""` plus `default=None`, the flag is deliberately three-state:

- flag absent -> `args.discover is None` -> normal research run
- bare `--discover` -> `args.discover == ""` -> global trending sweep (empty domain)
- `--discover X` -> `args.discover == "X"` -> domain-scoped discovery

The near-miss: the pre-existing dispatch was `if args.discover:`. Under Python truthiness, `""` and `None` are both falsy, so bare `--discover` would have been indistinguishable from no flag at all. The headline new mode of the PR (global trending) would simply never fire - the run would silently route into the normal-research path with no error, no warning, and no failing test unless a test exercised the bare form specifically. This was caught during development and the dispatch was changed to key on flag presence.

A second, related trap was caught in PR review (Greptile P2): the dependent flag `--discover-shallow` was accepted without `--discover` and silently no-opped into a full research run - the user asked for a fast, thin sweep and got a slow, full one. It was fixed with an explicit guard that errors loudly (exit 2).

## Guidance

1. With `nargs="?"` + `const`, the None/const/value trichotomy IS the contract: absent = `None`, bare flag = the `const` value, valued = the user's string. Dispatch on `args.flag is not None` (flag present), never on truthiness. When retrofitting optionality onto an existing flag, grep EVERY existing reference to `args.<flag>` - the old references were written when the flag was two-state and any `if args.flag:` among them is now a latent misroute.

2. Put a comment at the dispatch site explaining why it uses `is not None`. `if args.flag:` looks like the idiomatic form, and a future "simplification" pass will happily rewrite the correct check back into the bug. The repo's dispatch carries exactly this comment (`skills/last30days/scripts/last30days.py`):

   ```python
   # Bare --discover (no domain) is global trending, so the dispatch keys on
   # "flag present" (is not None), never on the domain string's truthiness.
   if args.discover is not None:
   ```

3. Dependent/modifier flags (`--x-modifier` that only applies with `--x`) must error loudly when their anchor flag is absent - never silently no-op. A silent no-op means the user asked for one mode and got another with zero feedback. Reject with a clear message and a nonzero exit:

   ```python
   if args.discover_shallow:
       # Without --discover this flag would silently no-op into a full
       # research run - reject it instead of ignoring the requested mode.
       sys.stderr.write(
           "[last30days] --discover-shallow only applies to --discover runs; "
           "add --discover [domain] or drop the flag.\n"
       )
       return 2
   ```

4. Pin both behaviors with CLI-level subprocess tests. Unit tests of the parser alone would not have caught the misroute, because parsing was correct - the bug lived in dispatch. The tests must run the real entry point end to end: one asserting the bare form reaches the new mode, one asserting the orphaned dependent flag is rejected (see Examples).

## Why This Matters

The failure mode is silent misrouting, which is the worst kind: the feature ships, `--help` documents the bare form, and every invocation of it quietly runs the wrong mode. There is no exception, no error message, no test failure - the output is a plausible-looking result from the wrong pipeline. Nothing surfaces the bug unless a test (or an alert user) exercises the bare form specifically and checks which mode actually ran. The same is true of the dependent-flag no-op: `--discover-shallow` without `--discover` produced a valid full research run, just not the one the user asked for.

## When to Apply

- Retrofitting an optional value onto an existing flag (`action="store_true"` or a plain valued option becoming `nargs="?"`): audit every dispatch site that reads the flag.
- Any flag where a falsy value (`""`, `0`, `[]`) is a MEANINGFUL present-value distinct from absence - the sentinel-vs-truthiness distinction applies beyond argparse (env vars, config keys, JSON fields).
- Dependent/modifier flags whose behavior only applies when an anchor flag is present.

## Examples

Before (the near-miss - conflates bare flag with no flag):

```python
if args.discover:          # '' and None are both falsy: bare --discover falls through
    return _run_discover(args, config)
```

After (`skills/last30days/scripts/last30days.py`, with the drift-guard comment):

```python
# Bare --discover (no domain) is global trending, so the dispatch keys on
# "flag present" (is not None), never on the domain string's truthiness.
if args.discover is not None:
    if topic:
        sys.stderr.write(
            "[last30days] --discover supplies the domain and cannot be combined "
            "with a positional topic.\n"
        )
        return 2
    if args.drill:
        sys.stderr.write("[last30days] --discover and --drill are mutually exclusive.\n")
        return 2
    return _run_discover(args, config)
```

The dependent-flag guard immediately below the dispatch:

```python
if args.discover_shallow:
    # Without --discover this flag would silently no-op into a full
    # research run - reject it instead of ignoring the requested mode.
    sys.stderr.write(
        "[last30days] --discover-shallow only applies to --discover runs; "
        "add --discover [domain] or drop the flag.\n"
    )
    return 2
```

The two pinning tests in `tests/test_discover_mode.py`, both running the real CLI via subprocess:

```python
def test_discovery_cli_bare_discover_is_global_trending():
    """Bare --discover (no domain) must run global trending, not error."""
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "--mock",
            "--emit=json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["kind"] == "discovery"
    assert payload["domain"] == ""
    assert payload["outcome"] in {"ok", "nothing-solid"}


def test_discovery_cli_rejects_shallow_without_discover():
    """--discover-shallow on a normal topic run must error, not silently no-op
    into a full research pass (P2 from PR #816 review)."""
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "AI agents",
            "--discover-shallow",
            "--mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--discover-shallow only applies to --discover runs" in result.stderr
```

The first test asserts not just exit 0 but that the discovery pipeline actually ran (`payload["kind"] == "discovery"`, `payload["domain"] == ""`) - the exact property the truthiness bug would have violated. Source: PR #816 (last30days-skill).

## Related

- [Ranked-output confidence floor + honest empty state](../design-patterns/ranked-output-confidence-floor-honest-empty-state.md) - sibling lesson from the same PR #816 discover rebuild (ranking quality).
- [Non-daemon executor threads defeat wall-clock budgets](../logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md) - sibling lesson from PR #816, same lesson class: a discover-mode defect that result-oriented unit tests structurally cannot catch (process lifetime there, bare-flag vs flag-absent conflation here).
- [PR #816](https://github.com/mvanhorn/last30days-skill/pull/816) - the discovery rebuild that introduced the three-state `--discover` flag (released v3.14.0).
