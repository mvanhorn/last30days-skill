"""Xiaohongshu HTTP API search client for last30days.

Uses xpzouying/xiaohongshu-mcp REST endpoints:
- GET/POST /api/v1/feeds/search
- GET /api/v1/login/status
"""

from datetime import datetime, timezone
import ipaddress
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from . import http, public_host

_BLOCKED_METADATA_HOSTS = frozenset({
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.internal",
    "metadata.tencentyun.com",
    "metadata.packet.net",
    "metadata.platformequinix.com",
    "instance-data",
    "instance-data.ec2.internal",
})
_BLOCKED_METADATA_IPS = frozenset({
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})
_LOCAL_LAN_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv6Network("fc00::/7"),
)
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        ip in _BLOCKED_METADATA_IPS
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
    ):
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        for embedded in (
            ip.ipv4_mapped,
            ip.sixtofour,
            ip.teredo[1] if ip.teredo else None,
        ):
            if embedded is not None and _is_blocked_ip(embedded):
                return True
        if ip in _NAT64_WELL_KNOWN or ip in _NAT64_LOCAL:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            if _is_blocked_ip(embedded):
                return True
    return False


def _is_local_bridge_host(
    host: str,
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None,
) -> bool:
    if ip is not None:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return _is_local_bridge_host(str(ip.ipv4_mapped), ip.ipv4_mapped)
        return ip.is_loopback or any(ip in net for net in _LOCAL_LAN_NETWORKS)
    return (
        host == "localhost"
        or host.endswith(".localhost")
        or host in ("host.docker.internal", "gateway.docker.internal")
        or host.endswith(".local")
        or "." not in host
    )


def _validate_base_url(base_url: Any) -> tuple[str, bool]:
    """Validate XIAOHONGSHU_API_BASE and return ``(normalized_base, is_local_bridge)``.

    Permits local/Docker/LAN self-hosted bridges over http(s) and remote bridges
    over https:// while rejecting non-http(s) schemes, cloud metadata endpoints,
    cleartext public internet URLs, embedded credentials, queries, and fragments.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Missing Xiaohongshu API base URL")
    raw = base_url.strip()
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise ValueError("Invalid Xiaohongshu API base URL")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid Xiaohongshu API base URL") from exc

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported Xiaohongshu API base URL scheme: {scheme or '<empty>'}")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Xiaohongshu API base URL must not contain embedded credentials")
    if "\\" in parts.netloc:
        raise ValueError("Invalid Xiaohongshu API base URL")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Invalid Xiaohongshu API base URL port")
    if parts.query or parts.fragment:
        raise ValueError("Xiaohongshu API base URL must not contain query or fragment")

    lower_path = parts.path.lower()
    if ".." in parts.path.split("/") or any(tok in lower_path for tok in ("%2e", "%2f", "%5c")):
        raise ValueError("Invalid Xiaohongshu API base URL path")

    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("Missing hostname in Xiaohongshu API base URL")
    if host in _BLOCKED_METADATA_HOSTS or host.endswith(".metadata.google.internal"):
        raise ValueError("Refused cloud metadata host in Xiaohongshu API base URL")

    ip = public_host._parse_ip_literal(host)
    if ip is not None and _is_blocked_ip(ip):
        raise ValueError("Refused blocked IP address in Xiaohongshu API base URL")

    is_local_bridge = _is_local_bridge_host(host, ip)
    if scheme == "http" and not is_local_bridge:
        raise ValueError("Xiaohongshu API base URL must use https:// for remote hosts")

    normalized = urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    return normalized, is_local_bridge


def _same_origin_redirect_guard(base_url: str) -> Callable[[str], bool]:
    expected_origin = http._request_origin(base_url)

    def _guard(newurl: str) -> bool:
        if not isinstance(newurl, str) or not newurl or newurl != newurl.strip():
            return False
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in newurl):
            return False
        try:
            parts = urlsplit(newurl)
            _ = parts.port
        except ValueError:
            return False
        if parts.username is not None or parts.password is not None or "\\" in parts.netloc:
            return False
        return http._request_origin(newurl) == expected_origin

    return _guard


def _to_int(value: Any) -> int:
    """Convert Xiaohongshu count strings to int.

    Supports plain ints and Chinese suffixes like 1.2万 / 3亿.
    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().lower().replace(",", "")
    if not text:
        return 0

    try:
        if text.endswith("万"):
            return int(float(text[:-1]) * 10000)
        if text.endswith("亿"):
            return int(float(text[:-1]) * 100000000)
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _timestamp_to_date_ms(ts: Any) -> Optional[str]:
    """Convert millisecond timestamp to YYYY-MM-DD."""
    try:
        iv = int(ts)
        if iv <= 0:
            return None
        # API examples use milliseconds.
        dt = datetime.fromtimestamp(iv / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _relevance_from_interactions(likes: int, comments: int, favorites: int) -> float:
    """Heuristic relevance score from engagement metrics."""
    # Weighted engagement with soft caps to [0, 1].
    weighted = (likes * 1.0) + (comments * 2.5) + (favorites * 1.5)
    # 5000 weighted engagement ~= strong relevance.
    score = min(1.0, max(0.05, weighted / 5000.0))
    return round(score, 3)


def _build_note_url(feed_id: str, xsec_token: str) -> str:
    """Build a stable Xiaohongshu note URL."""
    if xsec_token:
        return f"https://www.xiaohongshu.com/explore/{feed_id}?xsec_token={xsec_token}"
    return f"https://www.xiaohongshu.com/explore/{feed_id}"


def search_feeds(
    topic: str,
    from_date: str,
    to_date: str,
    base_url: str,
    depth: str = "default",
) -> List[Dict[str, Any]]:
    """Search Xiaohongshu feeds and normalize to web-item shape."""
    base, is_local_bridge = _validate_base_url(base_url)
    req_kwargs: Dict[str, Any] = {
        "redirect_guard": _same_origin_redirect_guard(base),
    }
    if not is_local_bridge:
        req_kwargs["public_only"] = True

    # Quick login sanity check.
    login = http.get(f"{base}/api/v1/login/status", timeout=8, retries=1, **req_kwargs)
    login_data = login.get("data") if isinstance(login, dict) else None
    is_logged_in = (
        bool(login_data.get("is_logged_in"))
        if isinstance(login_data, dict) else False
    )
    if not is_logged_in:
        raise http.HTTPError("Xiaohongshu API reachable but not logged in")

    # API supports filters; use recency-oriented defaults.
    publish_time = "一天内" if depth == "quick" else "一周内" if depth == "default" else "半年内"
    payload = {
        "keyword": topic,
        "filters": {
            "sort_by": "综合",
            "note_type": "不限",
            "publish_time": publish_time,
            "search_scope": "不限",
            "location": "不限",
        },
    }

    resp = http.post(f"{base}/api/v1/feeds/search", payload, timeout=20, retries=1, **req_kwargs)
    resp_data = resp.get("data") if isinstance(resp, dict) else None
    feeds = resp_data.get("feeds") if isinstance(resp_data, dict) else []
    if not isinstance(feeds, list):
        feeds = []

    # Cap source volume similarly to other web sources.
    limit = {"quick": 8, "default": 15, "deep": 25}.get(depth, 15)
    items: List[Dict[str, Any]] = []

    for i, feed in enumerate(feeds[:limit]):
        if not isinstance(feed, dict):
            continue
        note = feed.get("noteCard") or {}
        if not isinstance(note, dict):
            note = {}
        interact = note.get("interactInfo") or {}
        if not isinstance(interact, dict):
            interact = {}

        feed_id = str(feed.get("id") or note.get("noteId") or "").strip()
        if not feed_id:
            continue

        xsec_token = str(feed.get("xsecToken") or note.get("xsecToken") or "").strip()
        title = str(
            note.get("displayTitle")
            or note.get("title")
            or ""
        ).strip()
        snippet = str(
            note.get("desc")
            or note.get("displayDesc")
            or title
            or ""
        ).strip()

        likes = _to_int(interact.get("likedCount"))
        comments = _to_int(interact.get("commentCount"))
        favorites = _to_int(interact.get("collectedCount"))

        date_value = _timestamp_to_date_ms(note.get("time"))
        why = f"Xiaohongshu engagement: likes={likes}, comments={comments}, favorites={favorites}"

        items.append({
            "id": f"XHS{i+1}",
            "title": title[:200] if title else f"Xiaohongshu note {feed_id}",
            "url": _build_note_url(feed_id, xsec_token),
            "source_domain": "xiaohongshu.com",
            "snippet": snippet[:500],
            "date": date_value,
            "date_confidence": "high" if date_value else "low",
            "relevance": _relevance_from_interactions(likes, comments, favorites),
            "why_relevant": why,
            # Keep raw engagement for debugging/possible future rendering.
            "engagement": {
                "likes": likes,
                "comments": comments,
                "favorites": favorites,
            },
        })

    return items
