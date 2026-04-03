"""Terminal UI utilities for last30days skill."""

import sys
import time
import threading
import random
from typing import Optional

# Check if we're in a real terminal (not captured by Claude Code)
IS_TTY = sys.stderr.isatty()

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

def _build_nux_message(diag: dict = None) -> str:
    """Build conversational NUX message with dynamic source status."""
    if diag:
        reddit = "✓" if diag.get("openai") else "✗"
        x = "✓" if diag.get("x_source") else "✗"
        youtube = "✓" if diag.get("youtube") else "✗"
        web = "✓" if diag.get("web_search_backend") else "✗"
        status_line = f"Reddit {reddit}, X {x}, YouTube {youtube}, Web {web}"
    else:
        status_line = "YouTube ✓, Web ✓, Reddit ✗, X ✗"

    return f"""
I just researched that for you. Here's what I've got right now:

{status_line}

You can unlock more sources with API keys or by signing in to Codex — just ask me how and I'll walk you through it. More sources means better research, but it works fine as-is.

Some examples of what you can do:
- "last30 what are people saying about Figma"
- "last30 watch my biggest competitor every week"
- "last30 watch Peter Steinberger every 30 days"
- "last30 watch AI video tools monthly"
- "last30 what have you found about AI video?"

Just start with "last30" and talk to me like normal.
"""

# Shorter promo for single missing key
PROMO_SINGLE_KEY = {
    "reddit": "\n💡 You can unlock Reddit with an OpenAI API key or by running `codex login` — just ask me how.\n",
    "x": "\n💡 You can unlock X with AUTH_TOKEN/CT0 or XAI_API_KEY - just ask me how.\n",
}

# Bird auth help (for local users with vendored Bird CLI)
BIRD_AUTH_HELP = f"""
{Colors.YELLOW}Bird authentication failed.{Colors.RESET}

To fix this:
1. Add AUTH_TOKEN and CT0 to ~/.config/last30days/.env or .claude/last30days.env
2. Or set XAI_API_KEY for the xAI fallback backend
"""

BIRD_AUTH_HELP_PLAIN = """
Bird authentication failed.

To fix this:
1. Add AUTH_TOKEN and CT0 to ~/.config/last30days/.env or .claude/last30days.env
2. Or set XAI_API_KEY for the xAI fallback backend
"""

# Spinner frames
SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
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
        while self.running:
            frame = SPINNER_FRAMES[self.frame_idx % len(SPINNER_FRAMES)]
            sys.stderr.write(f"\r{self.color}{frame}{Colors.RESET} {self.message}  ")
            sys.stderr.flush()
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
                sys.stderr.write(f"⏳ {self.message}\n")
                sys.stderr.flush()
                self.shown_static = True

    def update(self, message: str):
        self.message = message
        if not IS_TTY and not self.shown_static:
            # Print update in non-TTY mode
            sys.stderr.write(f"⏳ {message}\n")
            sys.stderr.flush()

    def stop(self, final_message: str = ""):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        if IS_TTY:
            # Clear the line in real terminal
            sys.stderr.write("\r" + " " * 80 + "\r")
        if final_message:
            sys.stderr.write(f"✓ {final_message}\n")
        sys.stderr.flush()


class ProgressDisplay:
    """Progress display for research phases."""

    def __init__(self, topic: str, show_banner: bool = True):
        self.topic = topic
        self.spinner: Optional[Spinner] = None
        self.start_time = time.time()

        if show_banner:
            self._show_banner()

    def _show_banner(self):
        if IS_TTY:
            sys.stderr.write(MINI_BANNER + "\n")
            sys.stderr.write(f"{Colors.DIM}Topic: {Colors.RESET}{Colors.BOLD}{self.topic}{Colors.RESET}\n\n")
        else:
            # Simple text for non-TTY
            sys.stderr.write(f"/last30days · researching: {self.topic}\n")
        sys.stderr.flush()

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

    def show_complete(self, reddit_count: int, x_count: int, youtube_count: int = 0, hn_count: int = 0, pm_count: int = 0, tiktok_count: int = 0, ig_count: int = 0, v2ex_count: int = 0, hf_count: int = 0):
        elapsed = time.time() - self.start_time
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ Research complete{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write(f"  {Colors.YELLOW}Reddit:{Colors.RESET} {reddit_count} threads  ")
            sys.stderr.write(f"{Colors.CYAN}X:{Colors.RESET} {x_count} posts")
            if youtube_count:
                sys.stderr.write(f"  {Colors.RED}YouTube:{Colors.RESET} {youtube_count} videos")
            if tiktok_count:
                sys.stderr.write(f"  {Colors.PURPLE}TikTok:{Colors.RESET} {tiktok_count} videos")
            if ig_count:
                sys.stderr.write(f"  {Colors.PURPLE}Instagram:{Colors.RESET} {ig_count} reels")
            if hn_count:
                sys.stderr.write(f"  {Colors.YELLOW}HN:{Colors.RESET} {hn_count} stories")
            if pm_count:
                sys.stderr.write(f"  {Colors.GREEN}Polymarket:{Colors.RESET} {pm_count} markets")
            sys.stderr.write("\n\n")
        else:
            parts = [f"Reddit: {reddit_count} threads", f"X: {x_count} posts"]
            if youtube_count:
                parts.append(f"YouTube: {youtube_count} videos")
            if tiktok_count:
                parts.append(f"TikTok: {tiktok_count} videos")
            if ig_count:
                parts.append(f"Instagram: {ig_count} reels")
            if hn_count:
                parts.append(f"HN: {hn_count} stories")
            if pm_count:
                parts.append(f"Polymarket: {pm_count} markets")
            sys.stderr.write(f"✓ Research complete ({elapsed:.1f}s) - {', '.join(parts)}\n")
        sys.stderr.flush()

    def show_cached(self, age_hours: float = None):
        if age_hours is not None:
            age_str = f" ({age_hours:.1f}h old)"
        else:
            age_str = ""
        sys.stderr.write(f"{Colors.GREEN}⚡{Colors.RESET} {Colors.DIM}Using cached results{age_str} - use --refresh for fresh data{Colors.RESET}\n\n")
        sys.stderr.flush()

    def show_error(self, message: str):
        sys.stderr.write(f"{Colors.RED}✗ Error:{Colors.RESET} {message}\n")
        sys.stderr.flush()

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
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ Ready for web search{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write(f"  {Colors.GREEN}Web:{Colors.RESET} assistant will search blogs, docs & news\n\n")
        else:
            sys.stderr.write(f"✓ Ready for web search ({elapsed:.1f}s)\n")
        sys.stderr.flush()

    def show_promo(self, missing: str = "both", diag: dict = None):
        """Show NUX / promotional message for missing API keys.

        Args:
            missing: 'both', 'all', 'reddit', or 'x' - which keys are missing
            diag: Optional diagnostics dict for dynamic source status
        """
        if missing in ("both", "all"):
            sys.stderr.write(_build_nux_message(diag))
        elif missing in PROMO_SINGLE_KEY:
            sys.stderr.write(PROMO_SINGLE_KEY[missing])
        sys.stderr.flush()

    def show_bird_auth_help(self):
        """Show Bird authentication help."""
        if IS_TTY:
            sys.stderr.write(BIRD_AUTH_HELP)
        else:
            sys.stderr.write(BIRD_AUTH_HELP_PLAIN)
        sys.stderr.flush()


def _build_status_banner(diag: dict) -> list[str]:
    """Build the status banner lines (plain text, no ANSI).

    Returns a list of strings, each being a line of the banner box.

    Args:
        diag: Dict with keys:
            setup_complete, reddit_source, x_source, x_method,
            youtube, tiktok, instagram, hackernews, polymarket,
            bluesky, truthsocial, xiaohongshu, scrapecreators,
            web_search_backend
    """
    setup_complete = diag.get("setup_complete", False)
    has_sc = diag.get("scrapecreators", False)

    # --- Build active sources list: (label, method_label) ---
    active: list[str] = []

    # Reddit — always available; what matters to users is comments or not
    reddit_src = diag.get("reddit_source")
    if reddit_src == "scrapecreators":
        active.append("Reddit (with comments)")
    else:
        active.append("Reddit (threads only)")

    # X/Twitter
    x_source = diag.get("x_source")
    x_method = diag.get("x_method")
    if x_source:
        if x_method and x_method.startswith("browser-"):
            browser = x_method.split("-", 1)[1].capitalize()
            active.append(f"X ({browser})")
        elif x_method == "env":
            active.append("X (env)")
        elif x_method == "api":
            active.append("X (xAI)")
        else:
            active.append("X")

    # YouTube
    if diag.get("youtube"):
        active.append("YouTube")

    # HN — always available
    if diag.get("hackernews"):
        active.append("HN")

    # Polymarket — always available
    if diag.get("polymarket"):
        active.append("Polymarket")

    # TikTok (requires SC or Apify)
    if diag.get("tiktok"):
        active.append("TikTok")

    # Instagram (requires SC)
    if diag.get("instagram"):
        active.append("Instagram")

    # Bluesky
    if diag.get("bluesky"):
        active.append("Bluesky")

    # Truth Social
    if diag.get("truthsocial"):
        active.append("Truth Social")

    # Xiaohongshu
    if diag.get("xiaohongshu"):
        active.append("Xiaohongshu")

    # --- Format active sources into wrapped lines ---
    BOX_INNER = 53  # characters inside the box (between │ and │)
    PREFIX = "  "    # 2-space indent inside box

    def _wrap_sources(sources: list[str]) -> list[str]:
        """Wrap source labels into lines that fit the box width."""
        result_lines: list[str] = []
        current = PREFIX
        for i, s in enumerate(sources):
            token = f"✅ {s}"
            sep = "  " if current != PREFIX else ""
            if len(current) + len(sep) + len(token) > BOX_INNER:
                result_lines.append(current)
                current = PREFIX + token
            else:
                current += sep + token
        if current.strip():
            result_lines.append(current)
        return result_lines

    source_lines = _wrap_sources(active)

    # --- Title ---
    if not setup_complete:
        title = "/last30days v3.0 — First Run"
    else:
        title = "/last30days v3.0 — Source Status"

    # --- Build upgrade suggestions ---
    suggestions: list[str] = []

    if not setup_complete:
        suggestions.append("Run /last30days setup to unlock more sources")
    else:
        # Recommend ScrapeCreators if missing
        if not has_sc:
            suggestions.append("⭐ Add SCRAPECREATORS_API_KEY for Reddit comments")
            suggestions.append("   + TikTok + Instagram")
            suggestions.append("   100 free calls, no CC — scrapecreators.com (no affiliation)")

    # --- Assemble box lines ---
    # Collect all content lines, then determine box width dynamically.
    content: list[str] = []
    content.append(f" {title}")
    content.append("")  # blank line

    for sl in source_lines:
        content.append(sl)

    if suggestions:
        content.append("")  # blank line
        for sg in suggestions:
            content.append(f"  {sg}")

    content.append("")  # blank line
    content.append("  Config: ~/.config/last30days/.env")

    # Width = widest content line + 1 for right margin
    width = max(len(line) for line in content) + 1
    if width < 53:
        width = 53

    lines: list[str] = []
    lines.append("\u250c" + "\u2500" * width + "\u2510")
    for c in content:
        lines.append("\u2502" + c.ljust(width) + "\u2502")
    lines.append("\u2514" + "\u2500" * width + "\u2518")

    return lines


def _colorize_banner(lines: list[str]) -> list[str]:
    """Apply ANSI colors to plain-text banner lines for TTY output."""
    colored: list[str] = []
    for line in lines:
        if line.startswith("\u250c") or line.startswith("\u2514"):
            colored.append(f"{Colors.DIM}{line}{Colors.RESET}")
        elif line.startswith("\u2502"):
            inner = line[1:-1]  # strip box chars on both sides
            inner_width = len(inner)
            # Colorize check marks green, star yellow
            inner = inner.replace("\u2705", f"{Colors.GREEN}\u2705{Colors.RESET}")
            inner = inner.replace("\u2b50", f"{Colors.YELLOW}\u2b50{Colors.RESET}")
            # Bold the title line
            if "/last30days v3.0" in inner:
                stripped = inner.strip()
                inner = f" {Colors.BOLD}{stripped}{Colors.RESET}"
                # Re-pad to original width (ANSI codes are zero-width)
                visible_len = 1 + len(stripped)
                inner = inner + " " * max(0, inner_width - visible_len)
            colored.append(f"{Colors.DIM}\u2502{Colors.RESET}{inner}{Colors.DIM}\u2502{Colors.RESET}")
        else:
            colored.append(line)
    return colored


def show_diagnostic_banner(diag: dict):
    """Show pre-flight source status banner.

    Free-first design: leads with what's working (✅), not what's broken.
    Shows upgrade suggestions only when relevant.

    Args:
        diag: Dict with keys:
            setup_complete, reddit_source, x_source, x_method,
            youtube, tiktok, instagram, hackernews, polymarket,
            bluesky, truthsocial, xiaohongshu, scrapecreators,
            web_search_backend
    """
    lines = _build_status_banner(diag)

    if IS_TTY:
        lines = _colorize_banner(lines)

    sys.stderr.write("\n".join(lines) + "\n\n")
    sys.stderr.flush()


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
    sys.stderr.write(f"{color}▸{Colors.RESET} {message}\n")
    sys.stderr.flush()
