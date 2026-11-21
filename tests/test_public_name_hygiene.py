"""The public skill never names the hosted service's private repository or domain.

The skill talks to the hosted service only through a user-supplied
LAST30DAYS_API_BASE, and calls it "the hosted service". The forbidden names are
compared by SHA-256 so this guard does not spell them out itself.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN_SHA256 = frozenset({
    "a8f1e61dfeb30b0abc99ed7da20a055442cf4a679d99063016a96800495a5540",
    "bc413c626e226d752af2ca2e2c0ce1bc7fe79863850184a9a5527f40b1900060",
    "a5191e7f997452922618c7dc14d84bd926e3a92ddddbf94e8eaffdeb73702734",
})
_CANDIDATE = re.compile(r"last30days[-._][a-z0-9]+", re.IGNORECASE)
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "vendored", "dist", "build"}
_MAX_BYTES = 4 * 1024 * 1024


def _text_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                if path.stat().st_size > _MAX_BYTES:
                    continue
                yield path, path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue


def test_no_file_names_the_private_hosted_repo_or_domain():
    hits = []
    for path, text in _text_files():
        for match in _CANDIDATE.finditer(text):
            digest = hashlib.sha256(match.group(0).lower().encode("utf-8")).hexdigest()
            if digest in _FORBIDDEN_SHA256:
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(ROOT)}:{line}")
    assert not hits, "private hosted-service name found (say 'hosted service'): " + ", ".join(hits)
