"""CONFIGURATION.md must not advertise X backends the engine does not have (#942).

The X / Twitter row and the X env-var rows are the user-facing surface for the
X backend set, so they are held to ``env.X_BACKEND_KNOWN`` and to the official
X path copy (U8): the ``X_BEARER_TOKEN`` bearer path is described as about a
week, never as parity with the connector lane (R6).
"""

import re
from pathlib import Path

from lib import env

REPO = Path(__file__).resolve().parents[1]
BEARER_COVERAGE = (
    "recent posts, about the last week, unless your X developer project has "
    "full-archive access"
)


def _configuration() -> str:
    return (REPO / "CONFIGURATION.md").read_text(encoding="utf-8")


def _x_twitter_row() -> str:
    for line in _configuration().splitlines():
        if line.startswith("| X / Twitter |"):
            return line
    raise AssertionError("CONFIGURATION.md is missing the X / Twitter table row")


def _env_row(var: str) -> str:
    for line in _configuration().splitlines():
        if line.startswith(f"| `{var}` |"):
            return line
    raise AssertionError(f"CONFIGURATION.md is missing the env-var table row for {var}")


def test_configuration_x_row_omits_scrapecreators():
    row = _x_twitter_row()
    assert "SCRAPECREATORS_API_KEY" not in row
    assert "ScrapeCreators" not in row


def test_configuration_x_row_names_only_real_backends():
    """Every backend the row pins (LAST30DAYS_X_BACKEND=<name>) is a known backend."""
    row = _x_twitter_row()
    pinned = re.findall(r"LAST30DAYS_X_BACKEND=([a-z]+)", row)
    assert pinned, "the X row should show at least one opt-in pin"
    assert set(pinned) <= set(env.X_BACKEND_KNOWN)


def test_configuration_x_row_lists_bearer_with_coverage_caveat():
    row = _x_twitter_row()
    assert "X_BEARER_TOKEN" in row
    assert "LAST30DAYS_X_BACKEND=xapi" in row, "xapi is opt-in outside Grok Bot"
    assert BEARER_COVERAGE in row
    # The row must not sell the bearer path as full-window parity.
    assert "full 30-day" not in row.split("X_BEARER_TOKEN", 1)[1].split("`XAI_API_KEY`", 1)[0]


def test_configuration_env_rows_for_the_official_x_path():
    host = _env_row("LAST30DAYS_HOST")
    assert "grok-bot" in host
    bearer = _env_row("X_BEARER_TOKEN")
    assert BEARER_COVERAGE in bearer
    lane = _env_row("LAST30DAYS_X_HOST_LANE")
    # KTD10: the lane signal is read from the process environment only.
    assert "process environment" in lane
    assert ".env" in lane and "ignored" in lane


def test_configuration_pin_row_names_xapi_and_the_grok_bot_rule():
    row = _env_row("LAST30DAYS_X_BACKEND")
    assert "`xapi`" in row
    # KTD7: on a Grok Bot host the pin is the only way outside the official chain.
    assert "Grok Bot" in row
    assert "only way" in row
    assert "xapi → xai → xurl" in row


def test_engine_has_no_scrapecreators_x_backend():
    assert "scrapecreators" not in env.X_BACKEND_KNOWN
    assert env._x_backend_available("scrapecreators", {"SCRAPECREATORS_API_KEY": "k"}, False) is False
