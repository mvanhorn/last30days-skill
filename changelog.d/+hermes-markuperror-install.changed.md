HERMES_SETUP.md now documents the install-time `rich.errors.MarkupError` crash: Hermes prints the
scan report as rich markup, so a quoted regex containing `[/?#\s)\]` aborts an install before the
verdict appears. The entry names the crash, points readers at the Hermes CLI bug, and repeats
the hand-copy workaround.
