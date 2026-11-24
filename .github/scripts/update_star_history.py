#!/usr/bin/env python3
"""Update star history JSON from GitHub API and render clean light/dark SVGs.

Zero external dependencies (Python 3 stdlib only). Designed to run daily in
GitHub Actions and push only the generated JSON + SVGs to a dedicated orphan
branch (`star-history-assets`), avoiding GitHub's 40,000-star stargazer
pagination cap and keeping `main` free of automated commits.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#ffffff",
        "border": "#d0d7de",
        "text": "#1f2328",
        "muted": "#656d76",
        "grid": "#e5e7eb",
        "line": "#2563eb",
        "area_top": "#2563eb",
        "area_top_opacity": "0.20",
        "area_bottom_opacity": "0.01",
        "badge_bg": "#eff6ff",
        "badge_border": "#bfdbfe",
        "badge_text": "#1d4ed8",
        "star_fill": "#eab308",
        "dot_ring": "#ffffff",
        "streak_fill": "#2563eb",
        "streak_opacity": "0.08",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "grid": "#21262d",
        "line": "#58a6ff",
        "area_top": "#58a6ff",
        "area_top_opacity": "0.24",
        "area_bottom_opacity": "0.01",
        "badge_bg": "#16243a",
        "badge_border": "#1f4270",
        "badge_text": "#79c0ff",
        "star_fill": "#facc15",
        "dot_ring": "#0d1117",
        "streak_fill": "#58a6ff",
        "streak_opacity": "0.10",
    },
}

MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def fetch_live_stars(repo: str, token: str | None = None) -> int:
    """Fetch current stargazers_count via a single GitHub REST API call."""
    url = f"https://api.github.com/repos/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "last30days-star-history-action",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    count = int(payload["stargazers_count"])
    if count < 0:
        raise ValueError(f"Unexpected stargazers_count: {count}")
    return count


def merge_point(
    points: list[list[object]], day_iso: str, stars: int
) -> list[list[object]]:
    """Insert or update (day_iso, stars), preserving sorted monotonic dates."""
    by_date: dict[str, int] = {}
    for d, s in points:
        d_str = str(d)
        s_int = int(s)
        by_date[d_str] = max(by_date.get(d_str, 0), s_int)

    prev = by_date.get(day_iso, 0)
    by_date[day_iso] = max(prev, int(stars))

    ordered = sorted(by_date.items(), key=lambda kv: kv[0])
    return [[d, s] for d, s in ordered]


def nice_y_scale(max_val: int) -> tuple[int, list[int]]:
    """Compute a clean ceiling and 4-5 horizontal tick values."""
    target = max(1000, int(max_val * 1.08))
    raw_step = target / 4.0
    mag = 10 ** int(math.floor(math.log10(raw_step)))
    norm = raw_step / mag
    if norm <= 1.0:
        step = 1 * mag
    elif norm <= 2.0:
        step = 2 * mag
    elif norm <= 5.0:
        step = 5 * mag
    else:
        step = 10 * mag
    y_max = int(math.ceil(max_val * 1.04 / step) * step)
    if y_max - max_val < step * 0.15:
        y_max += step
    ticks = list(range(0, y_max + 1, step))
    return y_max, ticks


def fmt_k(val: int, decimals: int = 1) -> str:
    if val >= 1000:
        if val % 1000 == 0 and decimals == 0:
            return f"{val // 1000}K"
        s = f"{val / 1000:.{decimals}f}".rstrip("0").rstrip(".")
        return f"{s}K"
    return str(val)


def fmt_date_short(d: date) -> str:
    return f"{MONTH_NAMES[d.month]} {d.day}, {d.year}"


def iter_month_ticks(start: date, end: date) -> list[tuple[date, str]]:
    """Return evenly spaced month boundaries that never crowd the X-axis."""
    months: list[date] = []
    y, m = start.year, start.month + 1
    if m > 12:
        y += 1
        m = 1
    while date(y, m, 1) <= end:
        months.append(date(y, m, 1))
        m += 1
        if m > 12:
            y += 1
            m = 1

    if not months:
        return []

    # Thin ticks if the repo spans more than 12 months
    stride = max(1, math.ceil(len(months) / 11))
    show_year = start.year != end.year
    ticks: list[tuple[date, str]] = []
    for idx, d in enumerate(months):
        if idx % stride != 0:
            continue
        if show_year and (d.month == 1 or idx == 0):
            label = f"{MONTH_NAMES[d.month]} '{str(d.year)[2:]}"
        else:
            label = MONTH_NAMES[d.month]
        ticks.append((d, label))
    return ticks


def interpolate_stars(points: list[tuple[date, int]], target: date) -> int:
    """Linearly interpolate star count at a milestone date."""
    for d, s in points:
        if d == target:
            return s
    if target <= points[0][0]:
        return points[0][1]
    if target >= points[-1][0]:
        return points[-1][1]
    for (d1, s1), (d2, s2) in zip(points, points[1:]):
        if d1 <= target <= d2:
            span = (d2 - d1).days or 1
            frac = (target - d1).days / span
            return round(s1 + frac * (s2 - s1))
    return points[-1][1]


def compute_recent_delta(points: list[tuple[date, int]], days: int = 30) -> tuple[int, int]:
    """Return (actual_days, star_delta) over the trailing window."""
    end_d, end_s = points[-1]
    target = date.fromordinal(end_d.toordinal() - days)
    if target < points[0][0]:
        target = points[0][0]
    start_s = interpolate_stars(points, target)
    return (end_d - target).days, max(0, end_s - start_s)


def render_svg(data: dict, theme_name: str) -> str:
    t = THEMES[theme_name]
    repo = str(data["repo"])
    raw_points = data["points"]
    points: list[tuple[date, int]] = [
        (date.fromisoformat(str(d)), int(s)) for d, s in raw_points
    ]
    milestones = data.get("milestones", [])

    W, H = 840, 390
    pad_left, pad_right, pad_top, pad_bottom = 62, 86, 74, 54
    cw = W - pad_left - pad_right
    ch = H - pad_top - pad_bottom

    start_d = points[0][0]
    end_d = points[-1][0]
    total_days = max(1, (end_d - start_d).days)
    latest_stars = points[-1][1]
    y_max, y_ticks = nice_y_scale(latest_stars)

    def x_pos(d: date) -> float:
        return pad_left + ((d - start_d).days / total_days) * cw

    def y_pos(val: int) -> float:
        return pad_top + ch - (val / y_max) * ch

    coords = [(x_pos(d), y_pos(s), d, s) for d, s in points]
    line_d = " ".join(
        f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
        for i, (x, y, _, _) in enumerate(coords)
    )
    area_d = (
        f"{line_d} L{coords[-1][0]:.1f},{pad_top + ch:.1f} "
        f"L{coords[0][0]:.1f},{pad_top + ch:.1f} Z"
    )

    # Trailing 30-day delta for header subtitle
    win_days, delta_30d = compute_recent_delta(points, 30)
    delta_label = f"+{delta_30d:,} last {win_days}d" if delta_30d > 0 else ""

    # Build gridlines & Y ticks
    y_grid_svg: list[str] = []
    for tick in y_ticks:
        yp = y_pos(tick)
        label = "0" if tick == 0 else fmt_k(tick, decimals=0)
        dash = "" if tick == 0 else ' stroke-dasharray="4 4"'
        y_grid_svg.append(
            f'  <line x1="{pad_left}" y1="{yp:.1f}" x2="{W - pad_right}" y2="{yp:.1f}" '
            f'stroke="{t["grid"]}" stroke-width="1"{dash} />'
        )
        y_grid_svg.append(
            f'  <text x="{pad_left - 10}" y="{yp + 4:.1f}" text-anchor="end" '
            f'fill="{t["muted"]}" font-size="11.5" font-weight="500">{label}</text>'
        )

    # Build X ticks
    x_ticks_svg: list[str] = []
    for md, mlabel in iter_month_ticks(start_d, end_d):
        xp = x_pos(md)
        x_ticks_svg.append(
            f'  <line x1="{xp:.1f}" y1="{pad_top + ch:.1f}" x2="{xp:.1f}" y2="{pad_top + ch + 5:.1f}" '
            f'stroke="{t["border"]}" stroke-width="1" />'
        )
        x_ticks_svg.append(
            f'  <text x="{xp:.1f}" y="{pad_top + ch + 20:.1f}" text-anchor="middle" '
            f'fill="{t["muted"]}" font-size="11.5" font-weight="500">{escape(mlabel)}</text>'
        )

    # Build milestone callouts (skip any that would crowd the right edge)
    milestones_svg: list[str] = []
    for m in milestones:
        md = date.fromisoformat(str(m["date"]))
        if md < start_d or md > end_d:
            continue
        ms = interpolate_stars(points, md)
        mx = x_pos(md)
        my = y_pos(ms)
        dx = float(m.get("dx", -20))
        dy = float(m.get("dy", -30))
        tx = max(pad_left + 12, min(W - pad_right - 12, mx + dx))
        ty = max(pad_top + 14, min(pad_top + ch - 24, my + dy))
        anchor = "end" if dx < 0 else "start"
        leader_y = ty + (6 if dy < 0 else -11)
        milestones_svg.append(
            f'  <line x1="{mx:.1f}" y1="{my:.1f}" x2="{tx:.1f}" y2="{leader_y:.1f}" '
            f'stroke="{t["muted"]}" stroke-width="1" stroke-dasharray="2 2" opacity="0.65" />'
        )
        milestones_svg.append(
            f'  <circle cx="{mx:.1f}" cy="{my:.1f}" r="3.8" '
            f'fill="{t["bg"]}" stroke="{t["line"]}" stroke-width="2" />'
        )
        milestones_svg.append(
            f'  <text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" '
            f'fill="{t["text"]}" font-size="11.5" font-weight="600">{escape(str(m["label"]))}</text>'
        )
        if m.get("sub"):
            milestones_svg.append(
                f'  <text x="{tx:.1f}" y="{ty + 13:.1f}" text-anchor="{anchor}" '
                f'fill="{t["muted"]}" font-size="10.5">{escape(str(m["sub"]))}</text>'
            )

    last_x, last_y, _, _ = coords[-1]
    pill_text = f"{latest_stars:,}"
    pill_w = max(84, 30 + len(pill_text) * 8)
    pill_x = W - 24 - pill_w
    pill_y = 20

    grad_id = f"starArea_{theme_name}"
    font_sans = (
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    )
    font_mono = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

    date_span = f"{MONTH_NAMES[start_d.month]} {start_d.year} – {fmt_date_short(end_d)}"
    footer_right = f"Updated {fmt_date_short(end_d)} · Auto-updated daily"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub Star History for {escape(repo)}: {latest_stars:,} stars">
  <defs>
    <linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{t["area_top"]}" stop-opacity="{t["area_top_opacity"]}" />
      <stop offset="100%" stop-color="{t["area_top"]}" stop-opacity="{t["area_bottom_opacity"]}" />
    </linearGradient>
  </defs>

  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{t["bg"]}" stroke="{t["border"]}" stroke-width="1" />

  <g font-family="{font_sans}">
    <text x="24" y="34" fill="{t["text"]}" font-size="16" font-weight="700">Star History</text>
    <text x="126" y="34" fill="{t["muted"]}" font-size="13" font-family="{font_mono}">{escape(repo)}</text>
    <text x="24" y="53" fill="{t["muted"]}" font-size="12">{escape(date_span)}{("  ·  " + escape(delta_label)) if delta_label else ""}</text>

    <rect x="{pill_x}" y="{pill_y}" width="{pill_w}" height="28" rx="14" fill="{t["badge_bg"]}" stroke="{t["badge_border"]}" stroke-width="1" />
    <path d="M{pill_x + 16} {pill_y + 7.5}l1.85 3.75 4.14.6-3 2.92.71 4.13L{pill_x + 16} {pill_y + 16.95}l-3.7 1.95.71-4.13-3-2.92 4.14-.6z" fill="{t["star_fill"]}" />
    <text x="{pill_x + 27}" y="{pill_y + 18.5}" fill="{t["badge_text"]}" font-size="13" font-weight="700">{escape(pill_text)}</text>

{chr(10).join(y_grid_svg)}
{chr(10).join(x_ticks_svg)}

    <path d="{area_d}" fill="url(#{grad_id})" />
    <path d="{line_d}" fill="none" stroke="{t["line"]}" stroke-width="2.75" stroke-linejoin="round" stroke-linecap="round" />

{chr(10).join(milestones_svg)}

    <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="9" fill="{t["line"]}" opacity="0.22" />
    <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4.8" fill="{t["line"]}" stroke="{t["dot_ring"]}" stroke-width="2.2" />
    <text x="{last_x + 11:.1f}" y="{last_y + 4:.1f}" fill="{t["line"]}" font-size="13" font-weight="700">{escape(fmt_k(latest_stars, decimals=1))}</text>
    <text x="{last_x + 11:.1f}" y="{last_y + 18:.1f}" fill="{t["muted"]}" font-size="10.5">{escape(MONTH_NAMES[end_d.month])} {end_d.day}</text>

    <text x="24" y="{H - 14}" fill="{t["muted"]}" font-size="10.5">GitHub Stars</text>
    <text x="{W - 24}" y="{H - 14}" text-anchor="end" fill="{t["muted"]}" font-size="10.5">{escape(footer_right)}</text>
  </g>
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Update star history JSON and SVGs.")
    parser.add_argument("--data", required=True, help="Path to star-history.json (read/write)")
    parser.add_argument("--seed", help="Fallback seed JSON if --data does not exist yet")
    parser.add_argument("--out-light", required=True, help="Output path for light SVG")
    parser.add_argument("--out-dark", required=True, help="Output path for dark SVG")
    parser.add_argument("--repo", help="Override owner/repo for GitHub API fetch")
    parser.add_argument("--fetch", action="store_true", help="Fetch live star count from GitHub API")
    parser.add_argument("--stars", type=int, help="Explicit star count override (for testing/manual runs)")
    parser.add_argument("--date", help="Explicit UTC YYYY-MM-DD date override")
    args = parser.parse_args()

    data_path = Path(args.data)
    if data_path.exists():
        data = json.loads(data_path.read_text(encoding="utf-8"))
    elif args.seed and Path(args.seed).exists():
        data = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    else:
        raise FileNotFoundError(f"Neither --data ({data_path}) nor --seed ({args.seed}) exists.")

    repo = args.repo or data["repo"]
    data["repo"] = repo

    # Keep milestones synced with seed if seed exists
    if args.seed and Path(args.seed).exists():
        seed_data = json.loads(Path(args.seed).read_text(encoding="utf-8"))
        if "milestones" in seed_data:
            data["milestones"] = seed_data["milestones"]

    day_iso = args.date or datetime.now(timezone.utc).date().isoformat()

    if args.stars is not None:
        data["points"] = merge_point(data["points"], day_iso, args.stars)
    elif args.fetch:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        try:
            live = fetch_live_stars(repo, token=token)
        except urllib.error.URLError as exc:
            raise SystemExit(f"Failed to fetch stargazers_count for {repo}: {exc}") from exc
        data["points"] = merge_point(data["points"], day_iso, live)

    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    for theme_name, out_file in (("light", args.out_light), ("dark", args.out_dark)):
        out_path = Path(out_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_svg(data, theme_name), encoding="utf-8")

    latest_date, latest_stars = data["points"][-1]
    print(f"Updated {repo}: {latest_stars:,} stars as of {latest_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
