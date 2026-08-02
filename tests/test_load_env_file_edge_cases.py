"""Regression tests for load_env_file edge cases.

Covers GitHub issue #930: inline comments, export prefix, empty values.
"""

from lib import env


def test_inline_comment_stripped(tmp_path):
    """Inline # comment after whitespace must not become part of the value."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HOST=api.example.com   # default — change only if host moves\n",
        encoding="utf-8",
    )
    loaded = env.load_env_file(env_path)
    assert loaded["HOST"] == "api.example.com"


def test_inline_comment_with_path(tmp_path):
    """A documented path followed by an inline comment should keep only the path."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DIR=~/Documents/Last30Days                      # POSIX — defaults to this path when unset\n",
        encoding="utf-8",
    )
    loaded = env.load_env_file(env_path)
    assert loaded["DIR"] == "~/Documents/Last30Days"


def test_hash_inside_quoted_value_preserved(tmp_path):
    """A # inside quotes (e.g. an API key) must survive comment stripping."""
    env_path = tmp_path / ".env"
    env_path.write_text('API_KEY="secret#key#value"\n', encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert loaded["API_KEY"] == "secret#key#value"


def test_hash_in_single_quoted_value_preserved(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("TOKEN='my#token'\n", encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert loaded["TOKEN"] == "my#token"


def test_no_space_before_hash_is_not_comment(tmp_path):
    """A # directly attached to the value (no preceding space) is kept."""
    env_path = tmp_path / ".env"
    env_path.write_text("COLOR=#ff0000\n", encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert loaded["COLOR"] == "#ff0000"


def test_export_prefix_stripped(tmp_path):
    """Lines beginning with 'export ' should have the prefix removed."""
    env_path = tmp_path / ".env"
    env_path.write_text("export FOO=bar\n", encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert "FOO" in loaded
    assert loaded["FOO"] == "bar"
    assert "export FOO" not in loaded


def test_export_prefix_with_spaces(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("  export   MY_VAR=hello\n", encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert loaded["MY_VAR"] == "hello"


def test_empty_value_preserved(tmp_path):
    """KEY= should yield an empty string, not be dropped."""
    env_path = tmp_path / ".env"
    env_path.write_text("EMPTY=\nALSO_EMPTY=\nSET=real\n", encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert loaded["EMPTY"] == ""
    assert loaded["ALSO_EMPTY"] == ""
    assert loaded["SET"] == "real"


def test_empty_value_with_inline_comment(tmp_path):
    """An empty value followed by a comment should yield empty string."""
    env_path = tmp_path / ".env"
    env_path.write_text("BLANK=   # was set before\n", encoding="utf-8")
    loaded = env.load_env_file(env_path)
    assert loaded["BLANK"] == ""


def test_combined_export_and_comment(tmp_path):
    """export prefix + inline comment + quoted value: all three fixes together."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        'export SECRET="my#secret"   # do not share\n'
        "export PLAIN=value   # comment\n",
        encoding="utf-8",
    )
    loaded = env.load_env_file(env_path)
    assert loaded["SECRET"] == "my#secret"
    assert loaded["PLAIN"] == "value"


def test_full_documented_example(tmp_path):
    """Reproduction case from the issue: lines from CONFIGURATION.md."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LAST30DAYS_MEMORY_DIR=~/Documents/Last30Days                      # POSIX — defaults to this path when unset\n"
        "BSKY_SEARCH_HOST=api.bsky.app   # default — change only if Bluesky moves\n"
        "EMPTY=\n"
        "export FOO=bar\n",
        encoding="utf-8",
    )
    loaded = env.load_env_file(env_path)

    assert loaded["LAST30DAYS_MEMORY_DIR"] == "~/Documents/Last30Days"
    assert loaded["BSKY_SEARCH_HOST"] == "api.bsky.app"
    assert loaded["EMPTY"] == ""
    assert loaded["FOO"] == "bar"
