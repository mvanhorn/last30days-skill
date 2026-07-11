"""Deterministic Atom rendering for the saved research library."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from xml.etree import ElementTree as ET

from .library import LibraryEntry


ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM_NS)


def render_atom(
    entries: Sequence[LibraryEntry],
    *,
    entry_urls: Mapping[str, str] | None = None,
    feed_url: str | None = None,
    title: str = "last30days research library",
    author: str = "last30days research library",
) -> str:
    """Render an Atom feed whose IDs and timestamps are stable across runs."""
    urls = entry_urls or {}
    root = ET.Element(_tag("feed"))
    ET.SubElement(root, _tag("id")).text = "urn:last30days:research-library"
    ET.SubElement(root, _tag("title")).text = title
    author_node = ET.SubElement(root, _tag("author"))
    ET.SubElement(author_node, _tag("name")).text = author
    updated = entries[0].published_date.isoformat() if entries else "1970-01-01"
    ET.SubElement(root, _tag("updated")).text = f"{updated}T00:00:00Z"
    if feed_url:
        ET.SubElement(root, _tag("link"), {"rel": "self", "href": feed_url})

    for item in entries:
        node = ET.SubElement(root, _tag("entry"))
        ET.SubElement(node, _tag("id")).text = item.entry_id
        ET.SubElement(node, _tag("title")).text = item.headline
        ET.SubElement(node, _tag("updated")).text = f"{item.published_date.isoformat()}T00:00:00Z"
        ET.SubElement(node, _tag("published")).text = f"{item.published_date.isoformat()}T00:00:00Z"
        ET.SubElement(node, _tag("category"), {"term": item.topic})
        url = urls.get(item.entry_id, f"briefs/{item.output_name}")
        ET.SubElement(node, _tag("link"), {"href": url})
        ET.SubElement(node, _tag("summary"), {"type": "text"}).text = item.summary

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _tag(name: str) -> str:
    return f"{{{ATOM_NS}}}{name}"
