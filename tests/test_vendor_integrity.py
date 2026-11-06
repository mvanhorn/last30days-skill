"""Integrity + capability guard for the vendored X-search client.

Every file under ``lib/vendor/bird-search/`` must match the sha256 manifest in
its ``VENDORED.md`` (so any change to vendored code is an explicit, reviewed
manifest edit), and the code must stay a search-only, env-only subset: no write
endpoints, no process spawning, no dynamic code, no non-relative dynamic loads.

Regenerate the manifest after a deliberate, reviewed change with:
    python3 tests/test_vendor_integrity.py --print-manifest
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

VENDOR = (
    Path(__file__).resolve().parent.parent
    / "skills" / "last30days" / "scripts" / "lib" / "vendor" / "bird-search"
)
MANIFEST_DOC = VENDOR / "VENDORED.md"

FORBIDDEN = {
    "write endpoint (tweet post)": re.compile(r"statuses/update"),
    "write endpoint (media upload)": re.compile(r"media/upload|media/metadata"),
    "process spawning": re.compile(r"child_process|\bspawn\s*\(|\bexecSync\b|\bexecFile\b"),
    "dynamic code": re.compile(r"\beval\s*\(|new\s+Function\s*\("),
    "non-relative dynamic import": re.compile(r"\bimport\s*\(\s*['\"](?![./])"),
    "browser cookie library": re.compile(r"sweet-cookie"),
}


def _digest(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _actual() -> dict[str, str]:
    return {
        p.relative_to(VENDOR).as_posix(): _digest(p)
        for p in sorted(VENDOR.rglob("*"))
        if p.is_file() and p.name != "VENDORED.md" and "node_modules" not in p.parts
    }


def _recorded() -> dict[str, str]:
    text = MANIFEST_DOC.read_text(encoding="utf-8")
    block = text.split("<!-- manifest:start -->", 1)[1].split("<!-- manifest:end -->", 1)[0]
    out = {}
    for line in block.strip().splitlines():
        digest, name = line.split(None, 1)
        out[name.strip()] = digest
    return out


def test_vendored_files_match_reviewed_manifest():
    actual, recorded = _actual(), _recorded()
    assert actual.keys() == recorded.keys(), (
        f"vendored file set changed: added={sorted(actual.keys() - recorded.keys())} "
        f"removed={sorted(recorded.keys() - actual.keys())}"
    )
    changed = sorted(k for k in actual if actual[k] != recorded[k])
    assert not changed, (
        f"vendored code changed without a manifest update: {changed}. Review the "
        "diff, then run `python3 tests/test_vendor_integrity.py --print-manifest`."
    )


def test_vendored_client_is_search_only_and_env_only():
    hits = []
    for path in sorted(VENDOR.rglob("*.js")) + sorted(VENDOR.rglob("*.mjs")):
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN.items():
            if pattern.search(text):
                hits.append(f"{path.relative_to(VENDOR)}: {label}")
    assert hits == [], "\n".join(hits)


if __name__ == "__main__" and "--print-manifest" in sys.argv:
    for name, digest in _actual().items():
        print(f"{digest}  {name}")
