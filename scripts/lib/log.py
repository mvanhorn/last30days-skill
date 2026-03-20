"""Structured logging for last30days skill.

Provides a configured logger that preserves the existing [Source] prefix
style while enabling level-based filtering via --debug.

Usage:
    from lib.log import logger
    logger.info("[Reddit] Using ScrapeCreators API")
    logger.debug("[Reddit] Raw response: %s", data)
    logger.warning("[Reddit] Rate limited, backing off")
    logger.error("[Reddit] ScrapeCreators failed: %s", err)
"""

import logging
import sys

# Single named logger for the entire skill
logger = logging.getLogger("last30days")

# Bare formatter: message only, matching existing stderr output style
_formatter = logging.Formatter("%(message)s")

# stderr handler (matches existing sys.stderr.write behavior)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_formatter)

# Prevent duplicate handlers if module is re-imported
if not logger.handlers:
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# Prevent propagation to root logger
logger.propagate = False


def set_debug(enabled: bool = True) -> None:
    """Enable or disable debug-level logging."""
    logger.setLevel(logging.DEBUG if enabled else logging.INFO)
