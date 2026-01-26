"""Terminal UI utilities for last30days skill."""

import os
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

# Promo message for users without API keys
PROMO_MESSAGE = f"""
{Colors.YELLOW}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.RESET}
{Colors.YELLOW}⚡ UNLOCK THE FULL POWER OF /last30days{Colors.RESET}

{Colors.DIM}Right now you're using web search only. Add API keys to unlock:{Colors.RESET}

  {Colors.YELLOW}🟠 Reddit{Colors.RESET} - Real upvotes, comments, and community insights
     └─ Add OPENAI_API_KEY (uses OpenAI's web_search for Reddit)

  {Colors.CYAN}🔵 X (Twitter){Colors.RESET} - Real-time posts, likes, reposts from creators
     └─ Add XAI_API_KEY (uses xAI's live X search)

{Colors.DIM}Setup:{Colors.RESET} Edit {Colors.BOLD}~/.config/last30days/.env{Colors.RESET}
{Colors.YELLOW}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.RESET}
"""

PROMO_MESSAGE_PLAIN = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ UNLOCK THE FULL POWER OF /last30days

Right now you're using web search only. Add API keys to unlock:

  🟠 Reddit - Real upvotes, comments, and community insights
     └─ Add OPENAI_API_KEY (uses OpenAI's web_search for Reddit)

  🔵 X (Twitter) - Real-time posts, likes, reposts from creators
     └─ Add XAI_API_KEY (uses xAI's live X search)

Setup: Edit ~/.config/last30days/.env
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# Shorter promo for single missing key
PROMO_SINGLE_KEY = {
    "reddit": f"""
{Colors.DIM}💡 Tip: Add {Colors.YELLOW}OPENAI_API_KEY{Colors.RESET}{Colors.DIM} to ~/.config/last30days/.env for Reddit data with real engagement metrics!{Colors.RESET}
""",
    "x": f"""
{Colors.DIM}💡 Tip: Add {Colors.CYAN}XAI_API_KEY{Colors.RESET}{Colors.DIM} to ~/.config/last30days/.env for X/Twitter data with real likes & reposts!{Colors.RESET}
""",
}

PROMO_SINGLE_KEY_PLAIN = {
    "reddit": "\n💡 Tip: Add OPENAI_API_KEY to ~/.config/last30days/.env for Reddit data with real engagement metrics!\n",
    "x": "\n💡 Tip: Add XAI_API_KEY to ~/.config/last30days/.env for X/Twitter data with real likes & reposts!\n",
}

# Spinner frames
SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
DOTS_FRAMES = ['   ', '.  ', '.. ', '...']


class Spinner:
    """Animated spinner for long-running operations."""

    def __init__(self, message: str = "Working", color: str = Colors.CYAN):
        self.message = message
        self.color = color
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_idx = 0
        self.shown_static = False

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
            if not self.shown_static:
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

    def start_processing(self):
        msg = random.choice(PROCESSING_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}Processing{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_processing(self):
        if self.spinner:
            self.spinner.stop()

    def show_complete(self, reddit_count: int, x_count: int):
        elapsed = time.time() - self.start_time
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ Research complete{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write(f"  {Colors.YELLOW}Reddit:{Colors.RESET} {reddit_count} threads  ")
            sys.stderr.write(f"{Colors.CYAN}X:{Colors.RESET} {x_count} posts\n\n")
        else:
            sys.stderr.write(f"✓ Research complete ({elapsed:.1f}s) - Reddit: {reddit_count} threads, X: {x_count} posts\n")
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
            self.spinner.stop(f"{Colors.GREEN}Web{Colors.RESET} Claude will search the web")

    def show_web_only_complete(self):
        """Show completion for web-only mode."""
        elapsed = time.time() - self.start_time
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ Ready for web search{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write(f"  {Colors.GREEN}Web:{Colors.RESET} Claude will search blogs, docs & news\n\n")
        else:
            sys.stderr.write(f"✓ Ready for web search ({elapsed:.1f}s)\n")
        sys.stderr.flush()

    def show_promo(self, missing: str = "both"):
        """Show promotional message for missing API keys.

        Args:
            missing: 'both', 'reddit', or 'x' - which keys are missing
        """
        if missing == "both":
            if IS_TTY:
                sys.stderr.write(PROMO_MESSAGE)
            else:
                sys.stderr.write(PROMO_MESSAGE_PLAIN)
        elif missing in PROMO_SINGLE_KEY:
            if IS_TTY:
                sys.stderr.write(PROMO_SINGLE_KEY[missing])
            else:
                sys.stderr.write(PROMO_SINGLE_KEY_PLAIN[missing])
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
