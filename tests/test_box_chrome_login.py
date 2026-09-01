"""Extras-host box-chrome X login helper (skills/.../scripts/box_chrome_login.py).

Locks: a MacBook never gets a launch command and never spawns box-chrome (even
with --exec); an extras host with box-chrome on PATH documents the last30days
extras port 18800; and the SKILL.md recipe is present in the Auto flow and the
repair section with the MacBook-skip and no-.env-cookie rules. No cookie values
are ever produced (the helper reads none).
"""

from pathlib import Path
from unittest import mock

import box_chrome_login as bcl
from lib import chrome_cdp

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "last30days" / "SKILL.md"


def test_extras_port_is_18800_and_matches_chrome_cdp():
    assert bcl.EXTRAS_CDP_PORT == 18800
    assert bcl.EXTRAS_CDP_PORT == chrome_cdp._BOX_CHROME_PORT


def test_macbook_gets_no_launch_command():
    with mock.patch("lib.env.x_extras_enabled", return_value=False):
        recipe = bcl.build_recipe({})
    assert recipe["applies"] is False
    assert recipe["command"] is None
    assert recipe["env"] is None
    rendered = bcl.render_recipe(recipe).lower()
    assert "not an extras host" in rendered or "no box-chrome login" in rendered.replace("-", "")


def test_extras_with_box_chrome_documents_18800():
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=True),
        mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
    ):
        recipe = bcl.build_recipe({})
    assert recipe["applies"] is True
    assert recipe["command"] == ["/usr/local/bin/box-chrome", "--new-window", "https://x.com/login"]
    assert recipe["env"]["SAND_CHROME_REMOTE_DEBUG_PORT"] == "18800"
    assert recipe["env"]["CHROME_USER_DATA_DIR"]
    rendered = bcl.render_recipe(recipe)
    assert "SAND_CHROME_REMOTE_DEBUG_PORT=18800" in rendered
    assert "box-chrome" in rendered
    # The pin guidance names the endpoint but never a cookie value.
    assert "BROWSER_CDP_URL=http://127.0.0.1:18800" in rendered


def test_helper_launches_via_box_chrome_without_custom_class():
    """Launch through the box-chrome wrapper (it sets --class=box-chrome); never
    a raw chrome with a custom --class, which is what failed live."""
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=True),
        mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
    ):
        recipe = bcl.build_recipe({})
    assert recipe["command"][0].endswith("box-chrome")
    assert not any(arg.startswith("--class") for arg in recipe["command"])
    assert "google-chrome" not in " ".join(recipe["command"])


def test_extras_without_box_chrome_guides_pin():
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=True),
        mock.patch("shutil.which", return_value=None),
    ):
        recipe = bcl.build_recipe({})
    assert recipe["applies"] is True
    assert recipe["command"] is None
    assert "BROWSER_CDP_URL" in recipe["note"]


def test_main_exec_never_spawns_on_macbook():
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=False),
        mock.patch.object(bcl.env, "get_config", return_value={}),
        mock.patch("subprocess.Popen", side_effect=AssertionError("no spawn on MacBook")),
    ):
        assert bcl.main(["--exec"]) == 0


def test_main_exec_missing_box_chrome_does_not_spawn():
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=True),
        mock.patch("shutil.which", return_value=None),
        mock.patch.object(bcl.env, "get_config", return_value={}),
        mock.patch("subprocess.Popen", side_effect=AssertionError("no spawn without box-chrome")),
    ):
        assert bcl.main(["--exec"]) == 0


def test_main_exec_spawns_box_chrome_on_extras_with_18800():
    popen = mock.MagicMock()
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=True),
        mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
        mock.patch.object(bcl.env, "get_config", return_value={}),
        mock.patch("os.makedirs"),
        mock.patch("subprocess.Popen", popen),
    ):
        rc = bcl.main(["--exec"])
    assert rc == 0
    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args[0] == ["/usr/local/bin/box-chrome", "--new-window", "https://x.com/login"]
    assert kwargs["env"]["SAND_CHROME_REMOTE_DEBUG_PORT"] == "18800"


def test_default_run_prints_but_does_not_spawn():
    with (
        mock.patch("lib.env.x_extras_enabled", return_value=True),
        mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
        mock.patch.object(bcl.env, "get_config", return_value={}),
        mock.patch("subprocess.Popen", side_effect=AssertionError("no --exec = no spawn")),
    ):
        assert bcl.main([]) == 0


# --- SKILL.md recipe contract ---------------------------------------------


def _skill():
    return SKILL.read_text()


def test_skill_md_references_helper_in_flows_and_repair():
    text = _skill()
    # Auto Modal flow, Non-Modal Prose flow, and the repair section each point
    # at the helper.
    assert text.count("box_chrome_login.py") >= 3


def test_skill_md_documents_18800_launch_and_macbook_skip():
    text = _skill()
    assert "SAND_CHROME_REMOTE_DEBUG_PORT=18800" in text
    assert "X on Linux / Grok Bot / Mac mini" in text
    # MacBook must be told to skip the box-chrome path.
    assert "MacBook SKIPS" in text or "MacBook does NOT" in text


def test_skill_md_pins_browser_cdp_url_not_raw_cookies():
    text = _skill()
    assert "BROWSER_CDP_URL=http://127.0.0.1:18800" in text
    # The recipe must forbid writing the raw cookie pair to .env.
    assert "never `AUTH_TOKEN`/`CT0`" in text or "Do NOT write `AUTH_TOKEN`" in text
