"""Provider-reported charges for a scoped watchlist subprocess."""

from __future__ import annotations

import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

JOURNAL_ENV = "LAST30DAYS_USAGE_JOURNAL"

_PAID_HOSTS = {
    "openrouter.ai": "openrouter",
    "api.perplexity.ai": "perplexity",
    "api.openai.com": "openai",
    "api.x.ai": "xai",
    "generativelanguage.googleapis.com": "gemini",
    "api.scrapecreators.com": "scrapecreators",
    "api.search.brave.com": "brave",
    "api.tavily.com": "tavily",
    "api.exa.ai": "exa",
    "google.serper.dev": "serper",
    "api.parallel.ai": "parallel",
    "api.looot.ai": "looot",
    "api.x.com": "x",
    "api.twitter.com": "x",
    "xquik.com": "xquik",
    "api.groq.com": "groq",
}


@contextmanager
def _connection(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def create_journal(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with _connection(path) as conn:
        conn.execute(
            "CREATE TABLE attempts (id TEXT PRIMARY KEY, provider TEXT NOT NULL, "
            "cost REAL, unknown INTEGER NOT NULL DEFAULT 1, "
            "prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0)"
        )


def begin(provider: str) -> tuple[str, str, str] | None:
    path = os.environ.get(JOURNAL_ENV)
    if not path:
        return None
    attempt_id = uuid.uuid4().hex
    with _connection(path) as conn:
        conn.execute("INSERT INTO attempts (id, provider) VALUES (?, ?)", (attempt_id, provider))
    return path, attempt_id, provider


def begin_http(url: str, method: str) -> tuple[str, str, str] | None:
    if not os.environ.get(JOURNAL_ENV):
        return None
    parts = urlsplit(url)
    provider = _PAID_HOSTS.get(parts.hostname)
    if provider == "perplexity" and method == "GET":
        return None  # Retrieving an existing background operation does not start another charge.
    if provider is None:
        for name in ("OPENAI", "XAI", "OPENROUTER"):
            override = os.environ.get(f"{name}_BASE_URL")
            if override and urlsplit(override).netloc == parts.netloc:
                provider = name.lower()
                break
    return begin(provider) if provider else None


def cancel(attempt: tuple[str, str, str] | None) -> None:
    """Remove an attempt when the transport provably never started."""
    if attempt is not None:
        path, attempt_id, _ = attempt
        with _connection(path) as conn:
            conn.execute("DELETE FROM attempts WHERE id = ?", (attempt_id,))


def finish(attempt: tuple[str, str, str] | None, payload: object) -> None:
    if attempt is None or not isinstance(payload, dict):
        return
    path, attempt_id, provider = attempt
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    cost = usage.get("cost")
    if provider == "perplexity":
        if not isinstance(cost, dict) or cost.get("currency") != "USD":
            return
        cost = cost.get("total_cost")
    elif provider != "openrouter":
        return
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        return
    # A background response may expose a running subtotal rather than its final bill.
    unknown = int(payload.get("status") in {"queued", "in_progress"})
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    prompt = prompt if type(prompt) is int and prompt >= 0 else 0
    completion = completion if type(completion) is int and completion >= 0 else 0
    with _connection(path) as conn:
        conn.execute(
            "UPDATE attempts SET cost = ?, unknown = ?, prompt_tokens = ?, completion_tokens = ? WHERE id = ?",
            (cost, unknown, prompt, completion, attempt_id),
        )


def read_journal(path: Path) -> dict:
    try:
        with _connection(path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0), COALESCE(SUM(unknown), 0), "
                "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) FROM attempts"
            ).fetchone()
    except sqlite3.Error:
        return {"token_cost": 0.0, "cost_unknown": 1, "prompt_tokens": 0, "completion_tokens": 0}
    return {
        "token_cost": row[0],
        "cost_unknown": int(row[1] > 0),
        "prompt_tokens": row[2],
        "completion_tokens": row[3],
    }
