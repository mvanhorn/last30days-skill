"""Opt-in episode passages from Exa's podcast library."""

from __future__ import annotations

import re
import threading
from urllib.parse import urlparse

from . import grounding, http, log


def _header_names(header: str, field: str) -> list[str]:
    """Read linked or plain names; malformed metadata is optional."""
    match = re.search(rf"^-\s+\*\*{field}:\*\*\s*([^\n]*)", header, re.MULTILINE)
    if not match:
        return []
    value = re.sub(r"\[([^\[\]]+)\]\([^\s()]+\)", r"\1", match[1]).strip()
    value = re.sub(r",?\s*\[[^\[\]]*(\]\([^\s()]*)?$", "", value)  # text cap can cut a link
    if "[" in value or "]" in value:
        return []
    return [name.strip() for name in value.split(",") if name.strip()]


def search_podcasts(
    query: str, date_range: tuple[str, str], api_key: str, count: int = 8,
    deadline_monotonic: float | None = None, cancel: threading.Event | None = None,
) -> tuple[list[dict], dict]:
    data = http.request(
        "POST", "https://api.exa.ai/search",
        headers={"x-api-key": api_key},
        json_data={
            "query": query,
            "type": "auto",
            "includeDomains": ["exa.ai/library/podcast"],
            "numResults": count * 2,
            "startPublishedDate": f"{date_range[0]}T00:00:00.000Z",
            "endPublishedDate": f"{date_range[1]}T23:59:59.999Z",
            "contents": {
                "highlights": {"query": query, "maxCharacters": 1500},
                "text": {"maxCharacters": 1000},
            },
        },
        timeout=15,
        deadline_monotonic=deadline_monotonic,
        cancel=cancel,
    )
    items = []
    seen_urls: set[str] = set()
    for result in data.get("results", []):
        if len(items) >= count:
            break
        if not isinstance(result, dict):
            continue
        url = result.get("url")
        if not isinstance(url, str):
            continue
        try:
            parsed = urlparse(url)
            if (parsed.hostname != "exa.ai" or "/library/podcast/" not in parsed.path
                    or "/episode/" not in parsed.path):
                continue
        except ValueError:
            continue
        raw_date = str(result.get("publishedDate") or "")
        pub_date = grounding._normalize_date(raw_date[:10])
        if not grounding._in_date_range(pub_date, date_range) or url in seen_urls:
            continue
        seen_urls.add(url)
        text = result.get("text")
        text = text if isinstance(text, str) else ""
        parts = re.split(r"^##\s+Transcript\s*$", text, maxsplit=1, flags=re.MULTILINE)
        header = parts[0]
        transcript = parts[1].strip() if len(parts) == 2 else ""
        show_names = _header_names(header, "Podcast")
        highlights = result.get("highlights") or []
        if not isinstance(highlights, list):
            highlights = []
        passage = " ".join(h for h in highlights if isinstance(h, str)).strip()
        items.append({
            "id": f"PC{len(items) + 1}",
            "title": str(result.get("title") or ""),
            "url": url,
            "source_domain": grounding._domain(url),
            "snippet": (passage or transcript)[:1200],
            "date": pub_date,
            "relevance": 0.8,
            "why_relevant": "Exa podcast search",
            "metadata": {
                "show": ", ".join(show_names),
                "hosts": _header_names(header, "Hosts"),
                "guests": _header_names(header, "Guests"),
            },
        })
    log.source_log("Podcasts", f"{len(items)} episodes in window", tty_only=False)
    return items, {"label": "exa-podcasts", "webSearchQueries": [query], "resultCount": len(items)}
