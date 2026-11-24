"""Terminal UI utilities for last30days skill."""

import sys
import time
import threading
import random
from typing import Optional

from . import log
from .render import _skill_version

# Check if we're in a real terminal (not captured by Claude Code)
try:
    IS_TTY = bool(sys.stderr is not None and sys.stderr.isatty())
except OSError:
    IS_TTY = False


def _stderr_supports_utf8(stream=None) -> bool:
    """Return False when stderr declares a non-UTF encoding (e.g. cp1252, ascii)."""
    target = sys.stderr if stream is None else stream
    encoding = getattr(target, "encoding", None)
    if not encoding:
        return True
    normalized = encoding.lower().replace("_", "-")
    return normalized in ("utf-8", "utf8", "utf-16", "utf16", "utf-32", "utf32") or normalized.startswith("utf-")


def _write_stderr(text: str, *, flush: bool = True) -> None:
    log.safe_stderr_write(text, flush=flush)

# ANSI color codes
class Colors:
    PURPLE = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


BANNER = f"""{Colors.PURPLE}{Colors.BOLD}
  ██╗      █████╗ ███████╗████████╗██████╗  ██████╗ ██████╗  █████╗ ██╗   ██╗███████╗
  ██║     ██╔══██╗██╔════╝╚══██╔══╝╚════██╗██╔═████╗██╔══██╗██╔══██╗╚██╗ ██╔╝██╔════╝
  ██║     ███████║███████╗   ██║    █████╔╝██║██╔██║██║  ██║███████║ ╚████╔╝ ███████╗
  ██║     ██╔══██║╚════██║   ██║    ╚═══██╗████╔╝██║██║  ██║██╔══██║  ╚██╔╝  ╚════██║
  ███████╗██║  ██║███████║   ██║   ██████╔╝╚██████╔╝██████╔╝██║  ██║   ██║   ███████║
  ╚══════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═════╝  ╚═════╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝
{Colors.RESET}{Colors.DIM}  30 days of research. 30 seconds of work.{Colors.RESET}
"""

MINI_BANNER = f"""{Colors.PURPLE}{Colors.BOLD}/last30days{Colors.RESET} {Colors.DIM}· researching...{Colors.RESET}"""

# Fun status messages for each phase
REDDIT_MESSAGES = [
    "Diving into Reddit threads...",
    "Scanning subreddits for gold...",
    "Reading what Redditors are saying...",
    "Exploring the front page of the internet...",
    "Finding the good discussions...",
    "Upvoting mentally...",
    "Scrolling through comments...",
]

X_MESSAGES = [
    "Checking what X is buzzing about...",
    "Reading the timeline...",
    "Finding the hot takes...",
    "Scanning tweets and threads...",
    "Discovering trending insights...",
    "Following the conversation...",
    "Reading between the posts...",
]

ENRICHING_MESSAGES = [
    "Getting the juicy details...",
    "Fetching engagement metrics...",
    "Reading top comments...",
    "Extracting insights...",
    "Analyzing discussions...",
]

YOUTUBE_MESSAGES = [
    "Searching YouTube for videos...",
    "Finding relevant video content...",
    "Scanning YouTube channels...",
    "Discovering video discussions...",
    "Fetching transcripts...",
]

TIKTOK_MESSAGES = [
    "Searching TikTok for trending videos...",
    "Finding what's viral on TikTok...",
    "Scanning TikTok for relevant content...",
]

INSTAGRAM_MESSAGES = [
    "Searching Instagram Reels...",
    "Finding what's trending on Instagram...",
    "Scanning Instagram for relevant reels...",
]

HN_MESSAGES = [
    "Searching Hacker News...",
    "Scanning HN front page stories...",
    "Finding technical discussions...",
    "Discovering developer conversations...",
]

POLYMARKET_MESSAGES = [
    "Checking prediction markets...",
    "Finding what people are betting on...",
    "Scanning Polymarket for odds...",
    "Discovering prediction markets...",
]

PROCESSING_MESSAGES = [
    "Crunching the data...",
    "Scoring and ranking...",
    "Finding patterns...",
    "Removing duplicates...",
    "Organizing findings...",
]

WEB_ONLY_MESSAGES = [
    "Searching the web...",
    "Finding blogs and docs...",
    "Crawling news sites...",
    "Discovering tutorials...",
]

SOURCE_COMPLETION_ORDER = [
    "reddit",
    "x",
    "youtube",
    "tiktok",
    "instagram",
    "hackernews",
    "devto",
    "bluesky",
    "truthsocial",
    "polymarket",
    "grounding",
    "xiaohongshu",
    "digg",
    "arxiv",
    "techmeme",
    "trustpilot",
    "amazon",
    "meta_ads",
]

SOURCE_COMPLETION_META = {
    "reddit": ("Reddit", "thread", "threads", Colors.YELLOW),
    "x": ("X", "post", "posts", Colors.CYAN),
    "youtube": ("YouTube", "video", "videos", Colors.RED),
    "tiktok": ("TikTok", "video", "videos", Colors.PURPLE),
    "instagram": ("Instagram", "reel", "reels", Colors.PURPLE),
    "hackernews": ("HN", "story", "stories", Colors.YELLOW),
    "devto": ("dev.to", "article", "articles", Colors.GREEN),
    "bluesky": ("Bluesky", "post", "posts", Colors.BLUE),
    "truthsocial": ("Truth Social", "post", "posts", Colors.CYAN),
    "polymarket": ("Polymarket", "market", "markets", Colors.GREEN),
    "grounding": ("Web", "result", "results", Colors.GREEN),
    "xiaohongshu": ("Xiaohongshu", "post", "posts", Colors.RED),
    "digg": ("Digg", "cluster", "clusters", Colors.YELLOW),
    "arxiv": ("arXiv", "paper", "papers", Colors.RED),
    "techmeme": ("Techmeme", "headline", "headlines", Colors.CYAN),
    "trustpilot": ("Trustpilot", "review", "reviews", Colors.GREEN),
    "amazon": ("Amazon", "product", "products", Colors.YELLOW),
    "meta_ads": ("Meta Ads", "creative", "creatives", Colors.BLUE),
}


def _completion_sources(source_counts: dict[str, int], display_sources: list[str] | None) -> list[str]:
    requested = list(dict.fromkeys(display_sources or []))
    if not requested:
        requested = [source for source, count in source_counts.items() if count]
    if not requested and source_counts:
        requested = list(source_counts)

    candidate_set = set(requested) | set(source_counts)
    ordered = [source for source in SOURCE_COMPLETION_ORDER if source in candidate_set]
    for source in requested + list(source_counts):
        if source in candidate_set and source not in ordered:
            ordered.append(source)
    return ordered


def _format_completion_part(source: str, count: int, tty: bool) -> str:
    label, singular, plural, color = SOURCE_COMPLETION_META.get(
        source,
        (source.replace("_", " ").title(), "result", "results", Colors.RESET),
    )
    unit = singular if count == 1 else plural
    if tty:
        return f"{color}{label}:{Colors.RESET} {count} {unit}"
    return f"{label}: {count} {unit}"

def _build_nux_message(diag: dict = None) -> str:
    """Build conversational NUX message with dynamic source status."""
    available = set((diag or {}).get("available_sources", []))
    if diag:
        reddit = "✓" if "reddit" in available else "✗"
        x = "✓" if "x" in available else "✗"
        youtube = "✓" if "youtube" in available else "✗"
        web = "✓" if "grounding" in available else "✗"
        status_line = f"Reddit {reddit}, X {x}, YouTube {youtube}, Web {web}"
    else:
        status_line = "YouTube ✓, Web ✓, Reddit ✗, X ✗"

    return f"""
I just researched that for you. Here's what I've got right now:

{status_line}

More sources means better research, but it works fine as-is. You can unlock more for free - log into x.com in your browser for X, and run `brew install yt-dlp` for YouTube transcripts. That gives you Reddit (with comments), X, YouTube, HN, and Polymarket - all free.

Some examples of what you can do:
- "last30 what are people saying about Figma"
- "last30 watch my biggest competitor every week"
- "last30 watch AI video tools monthly"
- "last30 what have you found about AI video?"

Just start with "last30" and talk to me like normal.
"""

# Shorter promo for single missing key
PROMO_SINGLE_KEY = {
    "reddit": "\n💡 Unlock TikTok and Instagram with SCRAPECREATORS_API_KEY - 10,000 free calls, no CC - scrapecreators.com\n",
    "x": "\n💡 Unlock X: log into x.com in your browser, then re-run. "
         "Firefox works on all platforms. Safari works on macOS (detected automatically). "
         "Chrome, Brave, Edge, Arc, Dia, Vivaldi, Opera, or Chromium on macOS require "
         "FROM_BROWSER=auto in .env (Keychain dialog). On Windows only Firefox is supported. "
         "Or add AUTH_TOKEN/CT0 or XAI_API_KEY.\n",
    "web": "\n💡 You can unlock native grounded web search with BRAVE_API_KEY or SERPER_API_KEY.\n",
}

# Bird auth help (for local users with vendored Bird CLI)
BIRD_AUTH_HELP = f"""
{Colors.YELLOW}Bird authentication failed.{Colors.RESET}

To fix this:
1. Add AUTH_TOKEN and CT0 to ~/.config/last30days/.env, or to trusted .claude/last30days.env with LAST30DAYS_TRUST_PROJECT_CONFIG=1
2. Or set XAI_API_KEY for the xAI fallback backend
"""

BIRD_AUTH_HELP_PLAIN = """
Bird authentication failed.

To fix this:
1. Add AUTH_TOKEN and CT0 to ~/.config/last30days/.env, or to trusted .claude/last30days.env with LAST30DAYS_TRUST_PROJECT_CONFIG=1
2. Or set XAI_API_KEY for the xAI fallback backend
"""

# Spinner frames
SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
ASCII_SPINNER_FRAMES = ['|', '/', '-', '\\']
DOTS_FRAMES = ['   ', '.  ', '.. ', '...']


class Spinner:
    """Animated spinner for long-running operations."""

    def __init__(self, message: str = "Working", color: str = Colors.CYAN, quiet: bool = False):
        self.message = message
        self.color = color
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_idx = 0
        self.shown_static = False
        self.quiet = quiet  # Suppress non-TTY start message (still shows ✓ completion)

    def _spin(self):
        frames = SPINNER_FRAMES if _stderr_supports_utf8() else ASCII_SPINNER_FRAMES
        while self.running:
            frame = frames[self.frame_idx % len(frames)]
            _write_stderr(f"\r{self.color}{frame}{Colors.RESET} {self.message}  ")
            self.frame_idx += 1
            time.sleep(0.08)

    def start(self):
        self.running = True
        if IS_TTY:
            # Real terminal - animate
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        else:
            # Not a TTY (Claude Code) - just print once
            if not self.shown_static and not self.quiet:
                prefix = "⏳" if _stderr_supports_utf8() else "->"
                _write_stderr(f"{prefix} {self.message}\n")
                self.shown_static = True

    def update(self, message: str):
        self.message = message
        if not IS_TTY and not self.shown_static:
            # Print update in non-TTY mode
            prefix = "⏳" if _stderr_supports_utf8() else "->"
            _write_stderr(f"{prefix} {message}\n")

    def stop(self, final_message: str = ""):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        if IS_TTY:
            # Clear the line in real terminal
            _write_stderr("\r" + " " * 80 + "\r", flush=not final_message)
        if final_message:
            ok_glyph = "✓" if _stderr_supports_utf8() else "[ok]"
            _write_stderr(f"{ok_glyph} {final_message}\n")


class ProgressDisplay:
    """Progress display for research phases."""

    def __init__(self, topic: str, show_banner: bool = True):
        self.topic = topic
        self.spinner: Optional[Spinner] = None
        self.start_time = time.time()

        if show_banner:
            self._show_banner()

    def _show_banner(self):
        utf8 = _stderr_supports_utf8()
        if IS_TTY:
            banner = (
                MINI_BANNER
                if utf8
                else f"{Colors.PURPLE}{Colors.BOLD}/last30days{Colors.RESET} {Colors.DIM}- researching...{Colors.RESET}"
            )
            _write_stderr(
                banner + "\n"
                + f"{Colors.DIM}Topic: {Colors.RESET}{Colors.BOLD}{self.topic}{Colors.RESET}\n\n"
            )
        else:
            # Simple text for non-TTY
            sep = "·" if utf8 else "-"
            _write_stderr(f"/last30days {sep} researching: {self.topic}\n")

    def start_reddit(self):
        msg = random.choice(REDDIT_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}Reddit{Colors.RESET} {msg}", Colors.YELLOW)
        self.spinner.start()

    def end_reddit(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}Reddit{Colors.RESET} Found {count} threads")

    def start_reddit_enrich(self, current: int, total: int):
        if self.spinner:
            self.spinner.stop()
        msg = random.choice(ENRICHING_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}Reddit{Colors.RESET} [{current}/{total}] {msg}", Colors.YELLOW)
        self.spinner.start()

    def update_reddit_enrich(self, current: int, total: int):
        if self.spinner:
            msg = random.choice(ENRICHING_MESSAGES)
            self.spinner.update(f"{Colors.YELLOW}Reddit{Colors.RESET} [{current}/{total}] {msg}")

    def end_reddit_enrich(self):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}Reddit{Colors.RESET} Enriched with engagement data")

    def start_x(self):
        msg = random.choice(X_MESSAGES)
        self.spinner = Spinner(f"{Colors.CYAN}X{Colors.RESET} {msg}", Colors.CYAN)
        self.spinner.start()

    def end_x(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.CYAN}X{Colors.RESET} Found {count} posts")

    def start_youtube(self):
        msg = random.choice(YOUTUBE_MESSAGES)
        self.spinner = Spinner(f"{Colors.RED}YouTube{Colors.RESET} {msg}", Colors.RED)
        self.spinner.start()

    def end_youtube(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.RED}YouTube{Colors.RESET} Found {count} videos")

    def start_tiktok(self):
        msg = random.choice(TIKTOK_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}TikTok{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_tiktok(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.PURPLE}TikTok{Colors.RESET} Found {count} videos")

    def start_instagram(self):
        msg = random.choice(INSTAGRAM_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}Instagram{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_instagram(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.PURPLE}Instagram{Colors.RESET} Found {count} reels")

    def start_hackernews(self):
        msg = random.choice(HN_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}HN{Colors.RESET} {msg}", Colors.YELLOW, quiet=True)
        self.spinner.start()

    def end_hackernews(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}HN{Colors.RESET} Found {count} stories")

    def start_polymarket(self):
        msg = random.choice(POLYMARKET_MESSAGES)
        self.spinner = Spinner(f"{Colors.GREEN}Polymarket{Colors.RESET} {msg}", Colors.GREEN, quiet=True)
        self.spinner.start()

    def end_polymarket(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.GREEN}Polymarket{Colors.RESET} Found {count} markets")

    def start_processing(self):
        msg = random.choice(PROCESSING_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}Processing{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_processing(self):
        if self.spinner:
            self.spinner.stop()

    def show_complete(
        self,
        reddit_count: int = 0,
        x_count: int = 0,
        youtube_count: int = 0,
        hn_count: int = 0,
        pm_count: int = 0,
        tiktok_count: int = 0,
        ig_count: int = 0,
        *,
        source_counts: dict[str, int] | None = None,
        display_sources: list[str] | None = None,
    ):
        elapsed = time.time() - self.start_time
        if source_counts is None:
            source_counts = {
                "reddit": reddit_count,
                "x": x_count,
                "youtube": youtube_count,
                "tiktok": tiktok_count,
                "instagram": ig_count,
                "hackernews": hn_count,
                "polymarket": pm_count,
            }
            if display_sources is None:
                display_sources = [source for source, count in source_counts.items() if count]
                if not display_sources:
                    display_sources = ["reddit", "x"]

        ordered_sources = _completion_sources(source_counts, display_sources)
        parts = [
            _format_completion_part(source, source_counts.get(source, 0), tty=IS_TTY)
            for source in ordered_sources
        ]
        ok_glyph = "✓" if _stderr_supports_utf8() else "[ok]"
        if IS_TTY:
            _write_stderr(
                f"\n{Colors.GREEN}{Colors.BOLD}{ok_glyph} Research complete{Colors.RESET} "
                f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n"
                + "  " + "  ".join(parts)
                + "\n\n"
            )
        else:
            _write_stderr(f"{ok_glyph} Research complete ({elapsed:.1f}s) - {', '.join(parts)}\n")

    def show_cached(self, age_hours: float = None):
        if age_hours is not None:
            age_str = f" ({age_hours:.1f}h old)"
        else:
            age_str = ""
        bolt = "⚡" if _stderr_supports_utf8() else "*"
        _write_stderr(f"{Colors.GREEN}{bolt}{Colors.RESET} {Colors.DIM}Using cached results{age_str} - use --refresh for fresh data{Colors.RESET}\n\n")

    def show_error(self, message: str):
        err_glyph = "✗" if _stderr_supports_utf8() else "[x]"
        _write_stderr(f"{Colors.RED}{err_glyph} Error:{Colors.RESET} {message}\n")

    def start_web_only(self):
        """Show web-only mode indicator."""
        msg = random.choice(WEB_ONLY_MESSAGES)
        self.spinner = Spinner(f"{Colors.GREEN}Web{Colors.RESET} {msg}", Colors.GREEN)
        self.spinner.start()

    def end_web_only(self):
        """End web-only spinner."""
        if self.spinner:
            self.spinner.stop(f"{Colors.GREEN}Web{Colors.RESET} assistant will search the web")

    def show_web_only_complete(self):
        """Show completion for web-only mode."""
        elapsed = time.time() - self.start_time
        ok_glyph = "✓" if _stderr_supports_utf8() else "[ok]"
        if IS_TTY:
            _write_stderr(
                f"\n{Colors.GREEN}{Colors.BOLD}{ok_glyph} Ready for web search{Colors.RESET} "
                f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n"
                f"  {Colors.GREEN}Web:{Colors.RESET} assistant will search blogs, docs & news\n\n"
            )
        else:
            _write_stderr(f"{ok_glyph} Ready for web search ({elapsed:.1f}s)\n")

    def show_promo(self, missing: str = "both", diag: dict = None):
        """Show NUX / promotional message for missing API keys.

        Args:
            missing: 'both', 'all', 'reddit', or 'x' - which keys are missing
            diag: Optional diagnostics dict for dynamic source status
        """
        text = ""
        if missing in ("both", "all"):
            text = _build_nux_message(diag)
        elif missing in PROMO_SINGLE_KEY:
            text = PROMO_SINGLE_KEY[missing]
        if text:
            if not _stderr_supports_utf8():
                text = text.replace("✓", "[ok]").replace("✗", "[x]").replace("💡", "Tip:")
            _write_stderr(text)

    def show_bird_auth_help(self):
        """Show Bird authentication help."""
        if IS_TTY:
            _write_stderr(BIRD_AUTH_HELP)
        else:
            _write_stderr(BIRD_AUTH_HELP_PLAIN)


def show_diagnostic_banner(diag: dict):
    """Show pre-flight source status banner when sources are missing.

    Args:
        diag: Dict from pipeline.diagnose() with available_sources, x_backend,
            bird status, provider availability, and native web backend info.
    """
    available_sources = set(diag.get("available_sources") or [])
    has_reddit = "reddit" in available_sources
    has_scrapecreators = diag.get("has_scrapecreators", False)
    has_x = "x" in available_sources
    has_youtube = "youtube" in available_sources
    has_web = "grounding" in available_sources
    has_xiaohongshu = "xiaohongshu" in available_sources
    x_backend = diag.get("x_backend")
    native_web_backend = diag.get("native_web_backend")

    # If everything is available, no banner needed
    if has_reddit and has_x and has_youtube and has_web:
        return

    lines = []
    utf8 = _stderr_supports_utf8()
    ok_mark = "✅" if utf8 else "[ok]"
    err_mark = "❌" if utf8 else "[x]"
    warn_mark = "⚡" if utf8 else "[!]"
    dash = "—" if utf8 else "-"
    branch = "└─" if utf8 else "->"
    top = "┌─────────────────────────────────────────────────────┐" if utf8 else "+-----------------------------------------------------+"
    bot = "└─────────────────────────────────────────────────────┘" if utf8 else "+-----------------------------------------------------+"
    bar = "│" if utf8 else "|"

    if IS_TTY:
        lines.append(f"{Colors.DIM}{top}{Colors.RESET}")
        _header = f"/last30days v{_skill_version()} - Source Status"
        lines.append(f"{Colors.DIM}{bar}{Colors.RESET} {Colors.BOLD}{_header}{Colors.RESET}{' ' * (52 - len(_header))}{Colors.DIM}{bar}{Colors.RESET}")
        lines.append(f"{Colors.DIM}{bar}{Colors.RESET}                                                     {Colors.DIM}{bar}{Colors.RESET}")

        # Reddit
        if has_reddit and has_scrapecreators:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.GREEN}{ok_mark} Reddit{Colors.RESET}    {dash} full threads with comments          {Colors.DIM}{bar}{Colors.RESET}")
        elif has_reddit:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.GREEN}{ok_mark} Reddit{Colors.RESET}    {dash} public threads (titles + scores)   {Colors.DIM}{bar}{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.RED}{err_mark} Reddit{Colors.RESET}    {dash} unavailable                         {Colors.DIM}{bar}{Colors.RESET}")

        # X/Twitter
        if has_x:
            username = diag.get("bird_username", "")
            label = f"Bird ({username})" if x_backend == "bird" and username else str(x_backend or "xai").upper()
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.GREEN}{ok_mark} X/Twitter{Colors.RESET} {dash} {label}                          {Colors.DIM}{bar}{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.RED}{err_mark} X/Twitter{Colors.RESET} {dash} No X auth or fallback key        {Colors.DIM}{bar}{Colors.RESET}")
            if diag.get("bird_installed"):
                lines.append(f"{Colors.DIM}{bar}{Colors.RESET}     {branch} Add AUTH_TOKEN/CT0 or XAI_API_KEY      {Colors.DIM}{bar}{Colors.RESET}")
            else:
                lines.append(f"{Colors.DIM}{bar}{Colors.RESET}     {branch} Needs Node.js 22+ (Bird is bundled)           {Colors.DIM}{bar}{Colors.RESET}")

        # YouTube
        if has_youtube:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.GREEN}{ok_mark} YouTube{Colors.RESET}   {dash} yt-dlp found                      {Colors.DIM}{bar}{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.RED}{err_mark} YouTube{Colors.RESET}   {dash} yt-dlp not installed                {Colors.DIM}{bar}{Colors.RESET}")
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}     {branch} Fix: brew install yt-dlp (free)                {Colors.DIM}{bar}{Colors.RESET}")

        # Xiaohongshu (only show when configured)
        if has_xiaohongshu:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.GREEN}{ok_mark} Xiaohongshu{Colors.RESET} {dash} API connected + logged in         {Colors.DIM}{bar}{Colors.RESET}")

        # Web
        if has_web:
            backend = native_web_backend or "native"
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.GREEN}{ok_mark} Web{Colors.RESET}       {dash} {backend} API                       {Colors.DIM}{bar}{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  {Colors.YELLOW}{warn_mark} Web{Colors.RESET}       {dash} Add BRAVE_API_KEY or SERPER_API_KEY {Colors.DIM}{bar}{Colors.RESET}")

        lines.append(f"{Colors.DIM}{bar}{Colors.RESET}                                                     {Colors.DIM}{bar}{Colors.RESET}")
        lines.append(f"{Colors.DIM}{bar}{Colors.RESET}  Config: {Colors.BOLD}~/.config/last30days/.env{Colors.RESET}                  {Colors.DIM}{bar}{Colors.RESET}")
        lines.append(f"{Colors.DIM}{bot}{Colors.RESET}")
    else:
        # Plain text for non-TTY (Claude Code / Codex)
        lines.append(top)
        _header_plain = f"/last30days v{_skill_version()} - Source Status"
        lines.append(f"{bar} {_header_plain}{' ' * (52 - len(_header_plain))}{bar}")
        lines.append(f"{bar}                                                     {bar}")

        if has_reddit and has_scrapecreators:
            lines.append(f"{bar}  {ok_mark} Reddit    {dash} full threads with comments          {bar}")
        elif has_reddit:
            lines.append(f"{bar}  {ok_mark} Reddit    {dash} public threads (titles + scores)   {bar}")
        else:
            lines.append(f"{bar}  {err_mark} Reddit    {dash} unavailable                         {bar}")

        if has_x:
            lines.append(f"{bar}  {ok_mark} X/Twitter {dash} available                            {bar}")
        else:
            lines.append(f"{bar}  {err_mark} X/Twitter {dash} No X auth or fallback key          {bar}")
            if diag.get("bird_installed"):
                lines.append(f"{bar}     {branch} Add AUTH_TOKEN/CT0 or XAI_API_KEY           {bar}")
            else:
                lines.append(f"{bar}     {branch} Needs Node.js 22+ (Bird is bundled)           {bar}")

        if has_youtube:
            lines.append(f"{bar}  {ok_mark} YouTube   {dash} yt-dlp found                        {bar}")
        else:
            lines.append(f"{bar}  {err_mark} YouTube   {dash} yt-dlp not installed                {bar}")
            lines.append(f"{bar}     {branch} Fix: brew install yt-dlp (free)                {bar}")

        if has_xiaohongshu:
            lines.append(f"{bar}  {ok_mark} Xiaohongshu {dash} API connected + logged in         {bar}")

        if has_web:
            backend = native_web_backend or "native"
            lines.append(f"{bar}  {ok_mark} Web       {dash} {backend} API available{' ' * max(0, 13 - len(backend))}{bar}")
        else:
            lines.append(f"{bar}  {warn_mark} Web       {dash} Add BRAVE_API_KEY or SERPER_API_KEY {bar}")

        lines.append(f"{bar}                                                     {bar}")
        lines.append(f"{bar}  Config: ~/.config/last30days/.env                  {bar}")
        lines.append(bot)

    _write_stderr("\n".join(lines) + "\n\n")


def print_phase(phase: str, message: str):
    """Print a phase message."""
    colors = {
        "reddit": Colors.YELLOW,
        "x": Colors.CYAN,
        "process": Colors.PURPLE,
        "done": Colors.GREEN,
        "error": Colors.RED,
    }
    color = colors.get(phase, Colors.RESET)
    arrow = "▸" if _stderr_supports_utf8() else "->"
    _write_stderr(f"{color}{arrow}{Colors.RESET} {message}\n")
