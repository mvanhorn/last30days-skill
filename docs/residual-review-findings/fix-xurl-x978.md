## Residual Review Findings

Source run: `ce-code-review` on branch `fix/xurl-x978` (issue #978), plan `docs/plans/2026-08-20-fix-xurl-x978-plan.md`. Run dirs: `20260820-220120-767b63a3` (round 1), `20260820-221941-c2d05f7f` (round 2).

### Round 1 findings (P1 fixed, P3s applied)

- **P1 — `stored_auth_status()` masked permission-denied store as AUTH_MISSING** (`skills/last30days/scripts/lib/xurl_x.py`). `pathlib`'s `is_file()`/`is_dir()` swallow `OSError` into `False`, so a chmod-000 `~/.xurl/auth.yml` reported "no token store" instead of the typed `AUTH_ERROR`. Fixed with stat-based probes (`_is_file`/`_is_dir`) that propagate `EACCES`; `FileNotFoundError` stays absent → MISSING. Commits `d1ef34e`, `98795ae`.
- **P3 — legacy flat file precedence** (`xurl_x.py`): candidate order now prefers the live `auth.yml` over a stale flat file when both are physically possible. Covered by `test_canonical_path_preferred_when_both_layouts_exist`.
- **P3 — AUTH_MISSING detail naming** (`xurl_x.py`): `token_store_path()` is re-invoked in the detail string; cosmetic (identical value in scope). Not changed — behavior identical, one redundant `Path.home()` call in a rare error path.
- **P3 — test stub fallback** (`xurl_x.py`): the `except (AttributeError, TypeError)` fallback for path stubs was made explicit and now routes correctly; covered by existing `_UnreadableStore`/`_BrokenFile` tests.

### Round 2 findings (P3, advisory)

- **P3 — dangling-symlink docstring overclaim** (`xurl_x.py:118-138`): `_is_file` returns `False` (MISSING) for a dangling symlink since `stat()` raises `FileNotFoundError` on a missing target. Docstring updated to scope the broken-store claim to permission failures; a dangling target is a missing target and stays MISSING.
- **P3 — stat-PermissionError on candidate scan untested** (testing reviewer): added `test_permission_denied_parent_stat_reports_error_not_missing` (chmod-000 parent dir → AUTH_ERROR) and hardened `test_permission_denied_read_reports_error_not_missing` with a root-euid guard.
- **P3 — "both layouts exist" test naming** (testing reviewer): the two layouts cannot physically coexist (`~/.xurl` is either a file or a directory), so the test pins canonical-path selection rather than a file-vs-dir race. Left as-is with accurate comment.
- **P3 — redundant `token_store_path()` in MISSING detail** (reliability reviewer, advisory): cosmetic; no behavior change.

### Not filed as tracker tickets

All remaining items are P3 advisory/cosmetic with no user-visible impact; the two with real regression value were applied as tests. No GitHub issues filed.

### Verification

- `uv run pytest tests/test_xurl_x.py` → 54 passed
- `uv run pytest tests/test_env_v3.py tests/test_backend_descriptors.py tests/test_diagnose_compat.py` → 132 passed
- `uv run pytest -q --ignore=tests/test_network.py` → full suite passed
- `git diff --check` clean
