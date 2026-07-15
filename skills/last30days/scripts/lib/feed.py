"""Deterministic Atom rendering for the saved research library."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from xml.etree import ElementTree as ET

from .library import LibraryEntry


ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM_NS)


def render_atom(
    entries: Sequence[LibraryEntry],
    *,
    library_id: str,
    entry_urls: Mapping[str, str] | None = None,
    feed_url: str | None = None,
    title: str = "last30days research library",
    author: str = "last30days research library",
) -> str:
    """Render an Atom feed whose IDs and timestamps are stable across runs."""
    urls = entry_urls or {}
    feed_id = f"urn:last30days:research-library:{library_id}"
    root = ET.Element(_tag("feed"))
    ET.SubElement(root, _tag("id")).text = feed_id
    ET.SubElement(root, _tag("title")).text = title
    author_node = ET.SubElement(root, _tag("author"))
    ET.SubElement(author_node, _tag("name")).text = author
    updated = max((item.source_updated_at for item in entries), default=None)
    ET.SubElement(root, _tag("updated")).text = (
        _format_timestamp(updated) if updated else "1970-01-01T00:00:00Z"
    )
    if feed_url:
        ET.SubElement(root, _tag("link"), {"rel": "self", "href": feed_url})

    for item in entries:
        node = ET.SubElement(root, _tag("entry"))
        entry_id = item.entry_id.removeprefix("urn:last30days:")
        ET.SubElement(node, _tag("id")).text = f"{feed_id}:{entry_id}"
        ET.SubElement(node, _tag("title")).text = item.headline
        ET.SubElement(node, _tag("updated")).text = _format_timestamp(item.source_updated_at)
        ET.SubElement(node, _tag("published")).text = f"{item.published_date.isoformat()}T00:00:00Z"
        ET.SubElement(node, _tag("category"), {"term": item.topic})
        url = urls.get(item.entry_id, f"briefs/{item.output_name}")
        ET.SubElement(node, _tag("link"), {"href": url})
        ET.SubElement(node, _tag("summary"), {"type": "text"}).text = item.summary

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _tag(name: str) -> str:
    return f"{{{ATOM_NS}}}{name}"


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
