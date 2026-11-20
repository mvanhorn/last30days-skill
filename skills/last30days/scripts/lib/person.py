"""Person run context: who the run is about and how to tell them apart.

A person run starts when the host passes ``--employer``, ``--linkedin``, or
``--email`` for a person topic. The context holds the identity anchors that
later stages filter on (employer name, employer domain, and the email domain
unless it is a personal-mail provider) plus status fields those stages fill
in. With none of the flags there is no context and the run is unchanged.

The email address itself may drive a lookup but is never an anchor, never
logged, and never serialized.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

# Personal-mail providers: an address here says nothing about an employer.
FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",
    "msn.com", "icloud.com", "me.com", "mac.com",
    "proton.me", "pm.me", "protonmail.com", "protonmail.ch",
    "mail.com", "email.com", "fastmail.com", "fastmail.fm",
    "ymail.com", "rocketmail.com", "zoho.com", "zohomail.com",
    "tutanota.com", "tuta.io", "hey.com", "mail.ru", "web.de",
    "qq.com", "163.com", "126.com", "naver.com",
})

# Providers with per-country domains (yahoo.co.uk, hotmail.fr, gmx.de).
FREEMAIL_PROVIDER_LABELS = frozenset({
    "yahoo", "hotmail", "outlook", "live", "aol", "gmx", "yandex",
})

LINKEDIN_CHECKED = "checked"
LINKEDIN_NO_KEY = "no key"
LINKEDIN_FAILED = "failed"
LINKEDIN_NOT_FOUND = "profile not found"
LINKEDIN_REJECTED = "match rejected"
LINKEDIN_STATUSES = (
    LINKEDIN_CHECKED, LINKEDIN_NO_KEY, LINKEDIN_FAILED, LINKEDIN_NOT_FOUND, LINKEDIN_REJECTED,
)

_DOMAIN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
_EMAIL = re.compile(r"[^@\s]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)")
_LINKEDIN_SLUG = re.compile(r"[A-Za-z0-9_%-]+")


def is_freemail_domain(domain: str) -> bool:
    """True when ``domain`` belongs to a personal-mail provider."""
    domain = (domain or "").strip().lower().rstrip(".")
    if domain in FREEMAIL_DOMAINS:
        return True
    label, _, suffix = domain.partition(".")
    # yahoo.co.uk or hotmail.com.br, but not outlook-freight.example.
    return (
        label in FREEMAIL_PROVIDER_LABELS
        and bool(suffix)
        and all(0 < len(part) <= 3 for part in suffix.split("."))
    )


def _site_domain(site: str) -> str:
    """A bare lowercase host from a domain or URL, without ``www.``, or ""."""
    text = (site or "").strip()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text, flags=re.IGNORECASE)
    host = re.split(r"[/?#:]", host, maxsplit=1)[0].strip().lower().rstrip(".")
    if not _DOMAIN.fullmatch(host):
        return ""
    return host[4:] if host.startswith("www.") else host


def parse_employer(raw: str) -> tuple[str, str]:
    """``"Name[:domain]"`` -> (name, domain). Raises ValueError when malformed.

    The name is everything before the first colon; the domain may be a bare
    domain or a URL and reduces to its host.
    """
    name, colon, site = (raw or "").partition(":")
    name = " ".join(name.split())[:80]
    if not name:
        raise ValueError('--employer needs a company name, as "Name" or "Name:domain"')
    domain = ""
    if colon and site.strip():
        domain = _site_domain(site)
        if not domain:
            raise ValueError(
                f"--employer {name}: the part after the colon is not a domain or URL"
            )
    return name, domain


def normalize_linkedin_url(raw: str) -> str:
    """An http(s) linkedin.com/in/<slug> URL -> https://www.linkedin.com/in/<slug>.

    Raises ValueError for anything else.
    """
    error = ValueError(
        "--linkedin must be a profile URL like https://www.linkedin.com/in/<name>"
    )
    text = (raw or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        raise error from None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in {"http", "https"}:
        raise error
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise error
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2 or segments[0].lower() != "in":
        raise error
    slug = segments[1]
    if not _LINKEDIN_SLUG.fullmatch(slug) or not unquote(slug).strip("-_"):
        raise error
    return f"https://www.linkedin.com/in/{slug.lower()}"


def _email_domain(raw: str) -> str:
    """The domain of a plausible email address. Raises ValueError otherwise.

    The message never repeats the address.
    """
    match = _EMAIL.fullmatch((raw or "").strip().lower())
    if not match:
        raise ValueError("--email is not an email address (expected name@domain)")
    return match.group(1)


@dataclass
class PersonContext:
    """Identity inputs and resolution state for one person run."""

    full_name: str
    employer_name: str = ""
    employer_domain: str = ""
    # Domain of a work address; "" for a personal-mail address or none.
    email_domain: str = ""
    # The full address, for a lookup only. Never logged or serialized.
    email: str = field(default="", repr=False)
    linkedin_url: str = ""
    # Text anchors an item must match to count as this person.
    anchors: list[str] = field(default_factory=list)
    # No anchor and no user-supplied profile: identity is uncertain.
    identity_weak: bool = False
    linkedin_status: str | None = None
    match_basis: list[str] = field(default_factory=list)
    linkedin_mismatch: str = ""
    profile: dict = field(default_factory=dict)
    bio: dict = field(default_factory=dict)
    derived_handles: dict = field(default_factory=dict)
    older_posts: list[dict] = field(default_factory=list)
    dropped_counts: dict = field(default_factory=dict)
    timed_out: bool = False
    # Posts from the person's own LinkedIn inside the window, and every
    # first-party item (LinkedIn posts plus the resolved X and GitHub
    # accounts) inside the window. Older activity shows only when the
    # second count is thin.
    in_window_posts: int = 0
    first_party_in_window: int = 0

    def add_anchor(self, anchor: str) -> None:
        """Add a text anchor once, ignoring case."""
        anchor = " ".join((anchor or "").split())
        if anchor and anchor.lower() not in {a.lower() for a in self.anchors}:
            self.anchors.append(anchor)

    def set_linkedin_status(self, status: str) -> None:
        if status not in LINKEDIN_STATUSES:
            raise ValueError(f"unknown LinkedIn status {status!r}")
        self.linkedin_status = status

    def to_dict(self) -> dict:
        """JSON-safe view for artifacts. Omits the email address."""
        return {
            "full_name": self.full_name,
            "employer_name": self.employer_name,
            "employer_domain": self.employer_domain,
            "email_domain": self.email_domain,
            "email_given": bool(self.email),
            "linkedin_url": self.linkedin_url,
            "anchors": list(self.anchors),
            "identity_weak": self.identity_weak,
            "linkedin_status": self.linkedin_status,
            "match_basis": list(self.match_basis),
            "linkedin_mismatch": self.linkedin_mismatch,
            "profile": dict(self.profile),
            "bio": dict(self.bio),
            "derived_handles": dict(self.derived_handles),
            "older_posts": list(self.older_posts),
            "dropped_counts": dict(self.dropped_counts),
            "timed_out": self.timed_out,
            "in_window_posts": self.in_window_posts,
            "first_party_in_window": self.first_party_in_window,
        }


def build_person_context(
    topic: str,
    employer: str | None = None,
    linkedin: str | None = None,
    email: str | None = None,
) -> PersonContext | None:
    """The person context for these flags, or None when none was passed.

    Raises ValueError, naming the flag, when a value is malformed.
    """
    if employer is None and linkedin is None and email is None:
        return None
    ctx = PersonContext(full_name=" ".join((topic or "").split()))
    if employer is not None:
        ctx.employer_name, ctx.employer_domain = parse_employer(employer)
        ctx.add_anchor(ctx.employer_name)
        ctx.add_anchor(ctx.employer_domain)
    if linkedin is not None:
        ctx.linkedin_url = normalize_linkedin_url(linkedin)
    if email is not None:
        domain = _email_domain(email)
        ctx.email = email.strip()
        if not is_freemail_domain(domain):
            ctx.email_domain = domain
            ctx.add_anchor(domain)
    ctx.identity_weak = not ctx.anchors and not ctx.linkedin_url
    return ctx


# -- handle discovery ----------------------------------------------------------

_NON_SITE_HOSTS = (
    "linkedin.com", "x.com", "twitter.com", "github.com", "facebook.com",
    "instagram.com", "youtube.com", "tiktok.com", "threads.net", "bsky.app",
    "medium.com", "substack.com",
)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _company_markers(ctx: PersonContext) -> list[str]:
    """Compact forms of the employer's name and site labels. A handle or site
    containing one is the company's account, not the person's."""
    from . import company_names

    markers = [_compact(company_names.name_key(ctx.employer_name))]
    for domain in (ctx.employer_domain, ctx.email_domain):
        if domain:
            markers.append(_compact(domain.split(".")[0]))
    return [marker for marker in markers if len(marker) >= 3]


def _is_company(name: str, ctx: PersonContext) -> bool:
    compact = _compact(name)
    return any(marker in compact for marker in _company_markers(ctx))


def same_or_subdomain(host: str, domain: str) -> bool:
    return bool(host and domain) and (host == domain or host.endswith("." + domain))


def discover_handles(ctx: PersonContext) -> dict:
    """X handle, GitHub user and personal site from the person's own links.

    Links from the person's block on the employer's bio page come before the
    profile's listed websites. A link whose handle or host names the employer
    (its name key or its domain) is the company's account and is skipped.
    Returns only what was found, e.g. ``{"x": ..., "github": ..., "site": ...}``.
    """
    from . import resolve

    links = list((ctx.bio or {}).get("links") or []) + list((ctx.profile or {}).get("websites") or [])
    found: dict = {}
    for raw in links:
        url = str(raw or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        x_match = resolve._X_PROFILE_LINK.match(url)
        if x_match:
            handle = x_match.group(1)
            if "x" not in found and handle.lower() not in resolve._X_NON_ACCOUNTS and not _is_company(handle, ctx):
                found["x"] = handle
            continue
        gh_match = resolve._GITHUB_OWNER_LINK.match(url)
        if gh_match:
            user = gh_match.group(1)
            if "github" not in found and user.lower() not in resolve._GITHUB_NON_OWNERS and not _is_company(user, ctx):
                found["github"] = user.lower()
            continue
        host = _site_domain(url)
        if (
            "site" in found
            or not host
            or any(same_or_subdomain(host, social) for social in _NON_SITE_HOSTS)
            or any(same_or_subdomain(host, d) for d in (ctx.employer_domain, ctx.email_domain))
            or _is_company(host.rsplit(".", 1)[0], ctx)
        ):
            continue
        found["site"] = url
    return found


def apply_derived_handles(
    ctx: PersonContext, *, x_handle: str | None, github_user: str | None,
) -> tuple[str | None, str | None]:
    """Fill empty handle slots from the person's own links.

    Host-passed handles always win. What fills a slot (and any personal site)
    is recorded in ``ctx.derived_handles``, which marks it as discovered by
    the engine rather than supplied. Returns the (x_handle, github_user) to use.
    """
    found = discover_handles(ctx)
    derived: dict = {}
    if not x_handle and found.get("x"):
        x_handle = derived["x"] = found["x"]
    if not github_user and found.get("github"):
        github_user = derived["github"] = found["github"]
    if found.get("site"):
        derived["site"] = found["site"]
    ctx.derived_handles = derived
    return x_handle, github_user
