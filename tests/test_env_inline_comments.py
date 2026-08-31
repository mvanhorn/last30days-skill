"""Env-file parsing contract for inline comments and `export` prefixes (#930).

CONFIGURATION.md documents `.env` lines that carry a trailing `# comment`, and
`hooks/scripts/check-config.sh` already stripped them. `lib/env.py` did not, so
copy-pasting the documented examples produced silently corrupted values — a
memory dir whose name contained the doc comment, and a Bluesky search host that
could not resolve.

These tests pin the loader to the documented shape, including the cases that
must NOT be read as comments (a `#` glued to the value, or inside quotes).
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

from lib import env


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DOC = ROOT / "CONFIGURATION.md"


def _write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return path


def _load_one(tmp_path: Path, line: str) -> dict[str, str]:
    return env.load_env_file(_write_env(tmp_path, line + "\n"))


class TestInlineComments:
    @pytest.mark.parametrize(
        ("key", "line", "expected"),
        [
            # Shapes from the documented examples in the issue report. The
            # literal CONFIGURATION.md lines are exercised by
            # TestDocumentedExamplesRoundTrip below; the memory-dir default
            # path is not repeated here because
            # test_version_consistency.test_no_stray_hardcoded_memory_dir_paths
            # forbids hardcoding it outside doc-default lines.
            (
                "LAST30DAYS_MEMORY_DIR",
                "LAST30DAYS_MEMORY_DIR=~/Research/briefs      # POSIX — tilde is expanded downstream",
                "~/Research/briefs",
            ),
            (
                "BSKY_SEARCH_HOST",
                "BSKY_SEARCH_HOST=api.bsky.app   # default — change only if Bluesky moves",
                "api.bsky.app",
            ),
            (
                "LAST30DAYS_REGISTER",
                "LAST30DAYS_REGISTER=exec  # default | exec | dev | creator | eli5",
                "exec",
            ),
            # Single space, tab, and comment-without-space variants.
            ("KEY", "KEY=value # note", "value"),
            ("KEY", "KEY=value\t# note", "value"),
            ("KEY", "KEY=value    #note", "value"),
        ],
    )
    def test_trailing_comment_is_stripped(self, tmp_path, key, line, expected):
        assert _load_one(tmp_path, line)[key] == expected

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            # No whitespace before '#': part of the value, never a comment.
            ("KEY=abc#def", "abc#def"),
            ("KEY=https://example.com/page#frag", "https://example.com/page#frag"),
            ("KEY=pa##word", "pa##word"),
            ("KEY=#value", "#value"),
            # Quoted values are literal, '#' included.
            ('KEY="a b  # literal"', "a b  # literal"),
            ("KEY='a b  # literal'", "a b  # literal"),
            ('KEY="# leading hash"', "# leading hash"),
        ],
    )
    def test_hash_inside_value_survives(self, tmp_path, line, expected):
        assert _load_one(tmp_path, line)["KEY"] == expected

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('KEY="a b"   # note', "a b"),
            ("KEY='a b'   # note", "a b"),
            ('KEY="a b"# note', "a b"),
            # Closing quote, then a comment that itself contains quotes.
            ('KEY="host"  # was "other-host"', "host"),
        ],
    )
    def test_comment_after_closing_quote(self, tmp_path, line, expected):
        assert _load_one(tmp_path, line)["KEY"] == expected

    def test_whole_line_comment_still_ignored(self, tmp_path):
        assert env.load_env_file(_write_env(tmp_path, "# KEY=value\n  # KEY2=value\n")) == {}

    def test_matching_outer_quotes_still_stripped(self, tmp_path):
        # Pre-#930 behavior preserved: matching outer quotes come off even when
        # the value contains the same quote character.
        assert _load_one(tmp_path, "KEY='don't'")["KEY"] == "don't"

    def test_wizard_written_quoted_value_round_trips(self, tmp_path):
        # setup_wizard._format_env_value quotes values with whitespace; the
        # loader must still hand back the original string.
        assert _load_one(tmp_path, 'SOME_KEY="two words"')["SOME_KEY"] == "two words"


class TestExportPrefix:
    @pytest.mark.parametrize(
        "line",
        [
            "export KEY=value",
            "export    KEY=value",
            "\texport KEY=value",
            "export KEY=value  # note",
        ],
    )
    def test_export_prefix_is_ignored(self, tmp_path, line):
        loaded = _load_one(tmp_path, line)
        assert loaded == {"KEY": "value"}

    def test_export_with_quotes_and_comment(self, tmp_path):
        assert _load_one(tmp_path, 'export KEY="a b"  # note') == {"KEY": "a b"}

    def test_key_literally_named_export_is_untouched(self, tmp_path):
        loaded = env.load_env_file(_write_env(tmp_path, "export=value\nexportKEY=v2\n"))
        assert loaded == {"export": "value", "exportKEY": "v2"}


class TestEmptyValue:
    def test_empty_value_is_absent_not_empty_string(self, tmp_path):
        loaded = env.load_env_file(_write_env(tmp_path, "KEY=\nKEY2=   # only a comment\n"))
        assert loaded == {}

    def test_blanked_key_falls_back_to_documented_default(self, tmp_path, monkeypatch):
        # Deliberate semantics: get_config() resolves defaults with
        # merged_env.get(key, default), so treating `KEY=` as an empty string
        # would erase the default instead of falling back to it.
        config_file = _write_env(tmp_path, "LAST30DAYS_YT_SUB_LANGS=\n")
        monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("LAST30DAYS_YT_SUB_LANGS", raising=False)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(env, "CONFIG_FILE", config_file)
        monkeypatch.chdir(tmp_path)

        with (
            mock.patch.object(env, "_load_keychain", return_value={}),
            mock.patch.object(env, "_load_pass", return_value={}),
        ):
            config = env.get_config()

        assert config["LAST30DAYS_YT_SUB_LANGS"] == "en,es,pt"


class TestDocumentedExamplesRoundTrip:
    def test_configuration_md_inline_comment_examples_parse_clean(self, tmp_path):
        """Every `KEY=value  # comment` example in CONFIGURATION.md must load
        without its comment — the contract the issue reported broken."""
        # Matches live examples (`KEY=value  # note`) and the commented-out
        # "uncomment the line that matches your OS" variants, which land in a
        # real .env the moment a user uncomments them.
        pattern = re.compile(
            r"^(?:#\s*)?(?P<key>[A-Z][A-Z0-9_]*)=(?P<rest>\S.*\s#\s.*)$", re.M
        )
        examples = pattern.findall(CONFIG_DOC.read_text(encoding="utf-8"))
        assert len(examples) >= 3, "expected CONFIGURATION.md to keep inline-comment examples"

        for key, rest in examples:
            value = _load_one(tmp_path, f"{key}={rest}").get(key, "")
            assert value, f"{key} example parsed to nothing"
            assert "#" not in value, f"{key} kept its doc comment: {value!r}"
            assert value == value.strip(), f"{key} kept trailing whitespace: {value!r}"
