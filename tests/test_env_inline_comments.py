"""Inline comment handling in load_env_file (issue #930).

The Python parser must agree with the bash-side parser
(hooks/scripts/check-config.sh, which strips ${value%%[[:space:]]#*}):
a '#' starts a comment only when preceded by whitespace and outside
quotes. Values that legitimately contain '#' (URLs, quoted strings)
must survive untouched.
"""

from lib import env


def _load(tmp_path, text):
    env_path = tmp_path / ".env"
    env_path.write_text(text, encoding="utf-8")
    return env.load_env_file(env_path)


def test_inline_comment_stripped_with_value(tmp_path):
    # The exact documented example from CONFIGURATION.md (issue #930 repro).
    # Path literal split so the hardcoded-path guard stays quiet; trailing
    # spaces built at runtime so the source keeps no trailing whitespace.
    memory_dir = "~/Documents/" + "Last30Days"
    loaded = _load(
        tmp_path,
        f"LAST30DAYS_MEMORY_DIR={memory_dir}{' ' * 22}"
        "# POSIX default path when unset\n",
    )
    assert loaded["LAST30DAYS_MEMORY_DIR"] == memory_dir


def test_inline_comment_stripped_after_single_space(tmp_path):
    loaded = _load(tmp_path, "BSKY_SEARCH_HOST=api.bsky.app   # default\n")
    assert loaded["BSKY_SEARCH_HOST"] == "api.bsky.app"


def test_hash_without_preceding_space_is_literal(tmp_path):
    # URL fragment: no whitespace before '#', so it is part of the value.
    loaded = _load(tmp_path, "URL=https://example.com/path#frag\n")
    assert loaded["URL"] == "https://example.com/path#frag"


def test_hash_immediately_after_equals_is_literal(tmp_path):
    # Same rule as the bash side: '#' directly after '=' is not a comment.
    loaded = _load(tmp_path, "TOKEN=abc#def\n")
    assert loaded["TOKEN"] == "abc#def"


def test_quoted_value_keeps_internal_hash(tmp_path):
    # A '#' inside quotes is not a comment.
    loaded = _load(tmp_path, 'FOO="a # b"\n')
    assert loaded["FOO"] == "a # b"


def test_quoted_value_with_trailing_comment(tmp_path):
    # Comment after the closing quote is stripped; the quoted value survives.
    loaded = _load(tmp_path, 'FOO="bar" # keep me\n')
    assert loaded["FOO"] == "bar"


def test_full_line_comment_still_skipped(tmp_path):
    loaded = _load(tmp_path, "# WHOLE=line comment\nKEEP=yes\n")
    assert "WHOLE" not in loaded
    assert loaded["KEEP"] == "yes"


def test_comment_strip_applies_before_empty_check(tmp_path):
    # A value that is only a comment leaves the key unset, like bash's
    # `KEY=` with an empty value.
    loaded = _load(tmp_path, "EMPTY= # only a comment\n")
    assert "EMPTY" not in loaded


def test_mid_value_quote_is_literal(tmp_path):
    # `O'Reilly`: the quote is not a delimiter (only a value-START quote
    # opens a quoted region), so the trailing comment still strips,
    # matching the bash-side parser.
    loaded = _load(tmp_path, "NAME=O'Reilly # comment\n")
    assert loaded["NAME"] == "O'Reilly"


def test_escaped_quote_does_not_close_region(tmp_path):
    # A backslash-escaped quote inside a quoted value is literal; the '#' 
    # stays inside the quoted region and the closing quote still works.
    loaded = _load(tmp_path, 'FOO="a\\"b # c" # trailing\n')
    assert loaded["FOO"] == 'a\\"b # c'


def test_quoted_value_then_comment_with_escaped_quote(tmp_path):
    # Comment after the closing quote is stripped; the quoted value with
    # an escaped quote survives intact.
    loaded = _load(tmp_path, 'FOO="a\\"b" # keep me\n')
    assert loaded["FOO"] == 'a\\"b'


def test_whitespace_before_opening_quote(tmp_path):
    # Whitespace between '=' and the opening quote must not defeat quote
    # detection: the comment still strips, the quoted value survives.
    loaded = _load(tmp_path, 'FOO= "a # b" # trailing\n')
    assert loaded["FOO"] == "a # b"
