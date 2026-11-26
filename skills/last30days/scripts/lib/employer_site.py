"""A person's bio, read from their employer's own website. No key needed.

The lane works out the employer's site (an explicit domain, a work email's
domain, or the site the company lookup gives for an employer name), then
tries the pages a company usually lists its people on (``/team``,
``/about``, ``/leadership`` ...) and guesses at a page of the person's own
(``/team/dana-okafor``). A caller with a web search backend can pass one
site-scoped search as ``site_search``.

A page counts only when the person's full name appears on it as a run of
whole words. What is read is the block around the name (the card, list item,
section or article that holds it): its text, a title line, and the links in
that block, so the company's own header and footer links are never taken
for the person's. Every fetch stays on the employer's site and off private
networks, redirects included. Page text is remote content: the bio is
truncated and fenced as untrusted before the host reads it.
"""

from __future__ import annotations

import re
import time
import unicodedata
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

from . import company_resolve, health, http, jobs, linkedin, log, person, public_host, rerank

# Pages a company usually lists its people on, tried in this order.
TEAM_PATHS = ("/team", "/about", "/about-us", "/leadership", "/people", "/company")
# Sections under which a person may have a page of their own.
PERSON_PATH_PREFIXES = ("/team", "/people")
# Wall time for the whole lane when the caller gives no deadline.
DEFAULT_BUDGET_SECONDS = 20.0
PAGE_TIMEOUT_SECONDS = 8
# Pages from the site-scoped search tried at most.
SEARCH_RESULT_LIMIT = 2
# Characters of bio text the host sees, before the untrusted fence.
BIO_CHAR_LIMIT = 1500
TITLE_CHAR_LIMIT = 100
# Characters of page text kept on each side of the name when no block holds it.
WINDOW_CHARS = 600
# HTML read from one page.
PAGE_CHAR_LIMIT = 500_000

FOUND = "found"
NOT_FOUND = "not found"
FAILED = "failed"
TIMEOUT = "timeout"

_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
})
# Content never read: code, styling and the page's head.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
# Site-wide chrome: a name or link there is the company's, not the person's.
_CHROME_TAGS = frozenset({"header", "footer", "nav", "aside"})
# Elements that hold one person's card on a team page.
_BLOCK_TAGS = frozenset({"article", "li", "section", "div", "figure", "tr", "td", "dd"})
# Repeated siblings of these mean a list of people, not one person's card.
_ENTRY_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "li", "article", "figure", "tr"})
# Past these, the name has no block of its own.
_PAGE_TAGS = frozenset({"main", "body", "html", "root"})


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None"):
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    """A forgiving element tree: unclosed tags close with their parent, and
    a stray end tag is ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self._current = self.root
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping or tag in _SKIP_TAGS:
            if tag in _SKIP_TAGS and tag not in _VOID_TAGS:
                self._skipping += 1
            return
        node = _Node(tag, {name: value or "" for name, value in attrs}, self._current)
        self._current.children.append(node)
        if tag not in _VOID_TAGS:
            self._current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping or tag in _SKIP_TAGS:
            return
        self._current.children.append(_Node(tag, {name: value or "" for name, value in attrs}, self._current))

    def handle_endtag(self, tag: str) -> None:
        if self._skipping:
            if tag in _SKIP_TAGS:
                self._skipping -= 1
            return
        node: _Node | None = self._current
        while node is not None and node is not self.root and node.tag != tag:
            node = node.parent
        if node is not None and node is not self.root and node.parent is not None:
            self._current = node.parent

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._current.children.append(data)


def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    try:
        builder.feed(html[:PAGE_CHAR_LIMIT])
        builder.close()
    except Exception:  # noqa: BLE001 - a malformed page is a page with less on it
        pass
    return builder.root


def _segments(node: _Node, in_anchor: bool = False) -> Iterable[tuple[str, bool]]:
    """Each run of text under ``node``, whitespace collapsed, and whether it
    is a link's text."""
    for child in node.children:
        if isinstance(child, str):
            text = " ".join(child.split())
            if text:
                yield text, in_anchor
        elif child.tag not in _CHROME_TAGS:
            yield from _segments(child, in_anchor or child.tag == "a")


def _text(node: _Node) -> str:
    return " ".join(text for text, _anchor in _segments(node))


def _tokens(text: str) -> list[str]:
    return linkedin._normalize_name(text).split()


def _has_name(node: _Node, name: list[str], memo: dict[int, bool]) -> bool:
    key = id(node)
    if key not in memo:
        memo[key] = linkedin._token_run(name, _tokens(_text(node)))
    return memo[key]


def _deepest_with_name(node: _Node, name: list[str], memo: dict[int, bool]) -> _Node | None:
    """The innermost element outside the site's chrome whose text holds the
    full name, or None when the page does not name the person."""
    if node.tag in _CHROME_TAGS or not _has_name(node, name, memo):
        return None
    for child in node.children:
        if isinstance(child, _Node):
            found = _deepest_with_name(child, name, memo)
            if found is not None:
                return found
    return node


def _person_block(holder: _Node, name: list[str]) -> _Node | None:
    """The nearest card-like element around the name that says more than the
    name, or None when only the whole page does."""
    node: _Node | None = holder
    while node is not None and node.tag not in _PAGE_TAGS:
        if node.tag in _BLOCK_TAGS and len(_tokens(_text(node))) > len(name):
            return node
        node = node.parent
    return None


def _shared_with_others(holder: _Node, block: _Node) -> bool:
    """True when the block holds a list of people rather than one person: on
    the way up from the name, an entry (heading, list item, card) has a
    sibling entry of the same kind, so a link in the block may be a
    colleague's."""
    node = holder
    while node is not block and node.parent is not None:
        if node.tag in _ENTRY_TAGS and any(
            isinstance(sibling, _Node) and sibling is not node and sibling.tag == node.tag
            for sibling in node.parent.children
        ):
            return True
        node = node.parent
    return False


def _title_line(segments: list[tuple[str, bool]], name: list[str]) -> str:
    """The short line after the name that reads as a job title, or ''."""
    after = None
    for index, (text, _anchor) in enumerate(segments):
        tokens = _tokens(text)
        if linkedin._token_run(name, tokens) or (tokens and tokens[-1] == name[-1]):
            after = index
            break
    if after is None:
        return ""
    for text, anchor in segments[after + 1:]:
        if anchor:
            continue
        words = text.split()
        if 1 <= len(words) <= 12 and len(text) <= TITLE_CHAR_LIMIT and not text.endswith("."):
            return _defang(text)
        return ""
    return ""


def _links(block: _Node, page_url: str, site_host: str) -> list[str]:
    """Absolute links in the block that leave the employer's site."""
    found: list[str] = []

    def walk(node: _Node) -> None:
        for child in node.children:
            if not isinstance(child, _Node) or child.tag in _CHROME_TAGS:
                continue
            if child.tag == "a" and child.attrs.get("href"):
                url = urljoin(page_url, child.attrs["href"].strip())
                parsed = urlparse(url)
                if (
                    parsed.scheme in ("http", "https") and parsed.hostname
                    and not jobs._same_site(parsed.hostname, site_host) and url not in found
                ):
                    found.append(url)
            walk(child)

    walk(block)
    return found


def _defang(text: str) -> str:
    return rerank._defang_untrusted_fence(text)


def _fence(text: str) -> str:
    """Truncate page text and fence it as untrusted data."""
    text = " ".join(text.split())
    if len(text) > BIO_CHAR_LIMIT:
        text = text[: BIO_CHAR_LIMIT - 3].rstrip() + "..."
    return f"<untrusted_content>\n{_defang(text)}\n</untrusted_content>"


def _window(page_text: str, name: list[str]) -> str:
    """Page text around the first mention of the name."""
    pattern = r"\W+".join(re.escape(token) for token in name)
    match = re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", page_text, re.I)
    if match is None:
        return page_text[: 2 * WINDOW_CHARS]
    return page_text[max(0, match.start() - WINDOW_CHARS): match.end() + WINDOW_CHARS]


def _read_page(html: str, page_url: str, name: list[str], site_host: str) -> dict[str, Any] | None:
    """The person's bio, title and links from one page, or None when the page
    does not name them in full."""
    root = _parse(html)
    holder = _deepest_with_name(root, name, {})
    if holder is None:
        return None
    block = _person_block(holder, name)
    if block is None:
        return {"title": "", "bio": _fence(_window(_text(root), name)), "links": []}
    segments = list(_segments(block))
    if _shared_with_others(holder, block):
        # Several people share the block: keep the text near the name and
        # take no links, since any of them may be a colleague's.
        return {
            "title": _title_line(segments, name),
            "bio": _fence(_window(_text(block), name)),
            "links": [],
        }
    return {
        "title": _title_line(segments, name),
        "bio": _fence(" ".join(text for text, _anchor in segments)),
        "links": _links(block, page_url, site_host),
    }


def _hostname(value: str) -> str:
    value = (value or "").strip()
    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[1]
    if not value:
        return ""
    try:
        host = urlparse(value if "://" in value else f"https://{value}").hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def _employer_host(
    domain: str | None, email_domain: str | None, employer_name: str | None,
) -> tuple[str, bool]:
    """The employer's site host ('' when none is known) and whether looking
    it up failed."""
    host = _hostname(domain or "")
    if host:
        return host, False
    host = _hostname(email_domain or "")
    if host and not person.is_freemail_domain(host):
        return host, False
    if domain or email_domain or not (employer_name or "").strip():
        return "", False
    found = company_resolve.resolve_company(employer_name.strip(), listing=False)
    if isinstance(found, company_resolve.FailedLookup):
        return "", True
    return _hostname(str(found.get("company_site") or "")), False


def _slug(full_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _candidates(host: str, full_name: str) -> list[str]:
    base = f"https://{host}"
    slug = _slug(full_name)
    urls = [base + path for path in TEAM_PATHS]
    if slug:
        urls += [f"{base}{prefix}/{slug}" for prefix in PERSON_PATH_PREFIXES]
    return urls


def _search_urls(site_search: Callable[[str], Any], host: str, full_name: str) -> list[str]:
    """Same-site public pages from one site-scoped search."""
    try:
        results = site_search(f'site:{host.removeprefix("www.")} "{full_name}"') or []
    except Exception as exc:  # noqa: BLE001 - a search backend failing is not the lane failing
        log.source_log("Employer", f"site search failed: {type(exc).__name__}: {exc}", tty_only=False)
        return []
    urls: list[str] = []
    for result in results:
        url = str((result.get("url") if isinstance(result, dict) else result) or "").strip()
        if url and url not in urls and public_host.public_url(url, host):
            urls.append(url)
        if len(urls) >= SEARCH_RESULT_LIMIT:
            break
    return urls


def _fetch(url: str, host: str, deadline: float) -> tuple[str | None, bool]:
    """The page at ``url`` and whether the request timed out. A guessed page
    that is not there (403, 404, 410, or a redirect off the site) is no
    failure of the run's; a timeout or a server error still is."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, True
    landed: list[str] = []
    with http.expected_misses(403, 404, 410), http.tee_failures() as failures:
        try:
            text = http.get_text(
                url, timeout=PAGE_TIMEOUT_SECONDS, retries=1, accept="text/html", retry_dns=False,
                deadline_monotonic=time.monotonic() + min(remaining, PAGE_TIMEOUT_SECONDS),
                landed=landed, redirect_guard=public_host.redirect_guard(url), public_only=True,
            )
        except UnicodeDecodeError:
            text = None
    timed_out = any(failure.outcome_state == health.TIMEOUT for failure in failures)
    if text is not None and landed and not public_host.public_url(str(landed[-1]), host):
        return None, timed_out
    return text, timed_out


def _result(status: str, host: str, url: str = "", page: dict[str, Any] | None = None) -> dict[str, Any]:
    page = page or {}
    return {
        "status": status,
        "url": url,
        "title": page.get("title", ""),
        "bio": page.get("bio", ""),
        "links": list(page.get("links", [])),
        "domain": host,
    }


def find_bio(
    full_name: str,
    *,
    domain: str | None = None,
    employer_name: str | None = None,
    email_domain: str | None = None,
    deadline_monotonic: float | None = None,
    site_search: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """The person's bio from their employer's site.

    The site is ``domain`` (a host or URL) when given, else the domain of
    ``email_domain`` (a domain or an address) unless it is a mailbox
    provider's, else the official site the company lookup gives for
    ``employer_name``. ``site_search(query)`` is optional: called at most
    once, after the fixed pages miss, it returns URLs (or dicts with a
    ``url``); only pages on the employer's site are read.

    Returns ``status`` ("found", "not found", "failed" when the site could
    not be looked up or is not a public host, "timeout" when the deadline
    passed first), ``url`` of the page read, ``title``, ``bio`` (truncated
    and fenced in ``<untrusted_content>``), ``links`` (the absolute,
    off-site links in the person's own block) and ``domain``.
    """
    deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + DEFAULT_BUDGET_SECONDS
    name = _tokens(full_name)
    host, lookup_failed = _employer_host(domain, email_domain, employer_name)
    if lookup_failed:
        return _result(FAILED, "")
    if not host or len(name) < 2:
        return _result(NOT_FOUND, host)
    if not public_host.public_url(f"https://{host}/", host):
        log.source_log("Employer", f"{host} is not a public host; not fetched", tty_only=False)
        return _result(FAILED, host)

    tried: set[str] = set()

    def read(urls: list[str]) -> dict[str, Any] | None:
        for url in urls:
            if url in tried:
                continue
            if time.monotonic() >= deadline:
                return _result(TIMEOUT, host)
            tried.add(url)
            html, _timed_out = _fetch(url, host, deadline)
            if html:
                page = _read_page(html, url, name, host)
                if page is not None:
                    return _result(FOUND, host, url, page)
        return None

    found = read(_candidates(host, full_name))
    if found is not None:
        return found
    if site_search is not None and time.monotonic() < deadline:
        found = read(_search_urls(site_search, host, full_name))
        if found is not None:
            return found
    if time.monotonic() >= deadline:
        return _result(TIMEOUT, host)
    return _result(NOT_FOUND, host)
