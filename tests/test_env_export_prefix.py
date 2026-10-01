"""``export KEY=value`` lines in a .env file set KEY (#930).

The loader and the setup wizard's writers must agree on which key a line
names: if only the loader understood ``export``, the wizard would miss a
hand-set ``export KEY=...`` line and append a duplicate that overrides it.
"""

import pytest

from lib import env, setup_wizard


def _write(tmp_path, text):
    env_path = tmp_path / ".env"
    env_path.write_text(text, encoding="utf-8")
    env_path.chmod(0o600)
    return env_path


def _load(tmp_path, text):
    return env.load_env_file(_write(tmp_path, text))


@pytest.mark.parametrize("sep", [" ", "\t", "   "])
def test_export_prefix_is_dropped_from_key(tmp_path, sep):
    assert _load(tmp_path, f"export{sep}FOO=bar\n") == {"FOO": "bar"}


def test_export_prefix_combines_with_quotes_and_inline_comment(tmp_path):
    loaded = _load(tmp_path, 'export NAME="Jane # Doe" # annotation\n')
    assert loaded == {"NAME": "Jane # Doe"}


@pytest.mark.parametrize("line, key", [
    ("export=literal\n", "export"),
    ("export =literal\n", "export"),
    ("exporter=1\n", "exporter"),
    ("EXPORT FOO=1\n", "EXPORT FOO"),
    ("set FOO=1\n", "set FOO"),
])
def test_export_is_only_a_prefix_when_a_key_follows(tmp_path, line, key):
    assert list(_load(tmp_path, line)) == [key]


def test_write_api_key_sees_export_line_as_present(tmp_path):
    env_path = _write(tmp_path, "export SCRAPECREATORS_API_KEY=hand_set\n")

    assert setup_wizard.write_api_key(env_path, "sc_new_value") is True

    assert env_path.read_text() == "export SCRAPECREATORS_API_KEY=hand_set\n"
    assert env.load_env_file(env_path)["SCRAPECREATORS_API_KEY"] == "hand_set"


def test_replace_rewrites_export_line_and_keeps_the_prefix(tmp_path):
    env_path = _write(tmp_path, "export X_BEARER_TOKEN=stale\nOTHER=1\n")

    setup_wizard.write_api_key(env_path, "fresh", key_name="X_BEARER_TOKEN", replace=True)

    # Rewritten in place, not appended, so the stale secret is gone; the
    # prefix stays so a shell that sources the file still exports the key.
    assert env_path.read_text() == "export X_BEARER_TOKEN=fresh\nOTHER=1\n"
    assert env.load_env_file(env_path) == {"X_BEARER_TOKEN": "fresh", "OTHER": "1"}


@pytest.mark.parametrize("text", [
    "X_BEARER_TOKEN=old\nexport X_BEARER_TOKEN=older\n",
    "export X_BEARER_TOKEN=old\nX_BEARER_TOKEN=older\n",
])
def test_replace_keeps_the_prefix_when_any_duplicate_is_exported(tmp_path, text):
    env_path = _write(tmp_path, text)

    setup_wizard.write_api_key(env_path, "fresh", key_name="X_BEARER_TOKEN", replace=True)

    # The duplicates collapse to one line; dropping the prefix would stop a
    # shell that sources the file from exporting the key it used to export.
    assert env_path.read_text() == "export X_BEARER_TOKEN=fresh\n"


def test_replace_does_not_add_the_prefix_to_a_plain_line(tmp_path):
    env_path = _write(tmp_path, "X_BEARER_TOKEN =stale\n")

    setup_wizard.write_api_key(env_path, "fresh", key_name="X_BEARER_TOKEN", replace=True)

    assert env_path.read_text() == "X_BEARER_TOKEN=fresh\n"


def test_write_setup_config_sees_export_line_as_present(tmp_path):
    env_path = _write(tmp_path, "export SETUP_COMPLETE=true\n")

    assert setup_wizard.write_setup_config(env_path) is True

    assert env_path.read_text() == "export SETUP_COMPLETE=true\n"
