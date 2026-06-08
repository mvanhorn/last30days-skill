"""Unit tests for render._skill_version() fallback paths.

The function reads version from .claude-plugin/plugin.json first, then falls back
to SKILL.md frontmatter. These tests use monkeypatch to swap the render module's
__file__ attribute, which controls where the walk starts.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from lib import render


class SkillVersionFallbackTests(unittest.TestCase):
    def setUp(self):
        # tmp_path equivalent for unittest
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_render_at(self, parent: Path) -> Path:
        """Place a dummy render.py inside parent and return its path."""
        parent.mkdir(parents=True, exist_ok=True)
        fake_render = parent / "render.py"
        fake_render.write_text("")
        return fake_render

    def _write_manifest(self, parent: Path, version: str | None) -> None:
        """Write .claude-plugin/plugin.json under parent. version=None writes corrupt JSON."""
        d = parent / ".claude-plugin"
        d.mkdir(parents=True, exist_ok=True)
        if version is None:
            (d / "plugin.json").write_text("{not valid json")
        else:
            (d / "plugin.json").write_text(f'{{"version": "{version}"}}')

    def _write_skill_md(self, parent: Path, frontmatter_version_line: str | None) -> None:
        """Write SKILL.md with frontmatter. None writes a SKILL.md with no version line."""
        if frontmatter_version_line is None:
            body = "---\nname: test\n---\n# body\n"
        else:
            body = f"---\nname: test\n{frontmatter_version_line}\n---\n# body\n"
        (parent / "SKILL.md").write_text(body)

    def test_manifest_absent_falls_back_to_skill_md_frontmatter(self):
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        self._write_skill_md(skill_dir, 'version: "9.9.9"')

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("9.9.9", render._skill_version())

    def test_manifest_corrupt_falls_back_to_skill_md_frontmatter(self):
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        self._write_manifest(skill_dir, version=None)  # corrupt
        self._write_skill_md(skill_dir, 'version: "8.8.8"')

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("8.8.8", render._skill_version())

    def test_manifest_missing_version_key_falls_back_to_skill_md_frontmatter(self):
        # Valid JSON, but no "version" key. Old behavior returned "?" and never tried
        # SKILL.md. Fix from greptile review: fall through to SKILL.md fallback.
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        (skill_dir / ".claude-plugin").mkdir()
        (skill_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "x"}')
        self._write_skill_md(skill_dir, 'version: "4.4.4"')

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("4.4.4", render._skill_version())

    def test_manifest_empty_version_string_falls_back_to_skill_md_frontmatter(self):
        # Manifest version present but empty — treat as missing so the badge
        # doesn't emit a useless "🌐 last30days v · synced ..." line.
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        (skill_dir / ".claude-plugin").mkdir()
        (skill_dir / ".claude-plugin" / "plugin.json").write_text('{"version": ""}')
        self._write_skill_md(skill_dir, 'version: "2.2.2"')

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("2.2.2", render._skill_version())

    def test_corrupt_inner_manifest_does_not_shadow_valid_outer_manifest(self):
        outer = self.tmp_path / "outer"
        inner = outer / "skill_root"
        fake_render = self._make_render_at(inner)
        self._write_manifest(inner, version=None)  # corrupt at inner
        self._write_manifest(outer, version="7.7.7")  # valid at outer

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("7.7.7", render._skill_version())

    def test_neither_source_present_returns_question_mark(self):
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        # No manifest, no SKILL.md anywhere under tmp_path

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("?", render._skill_version())

    def test_skill_md_without_version_returns_question_mark(self):
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        self._write_skill_md(skill_dir, frontmatter_version_line=None)

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("?", render._skill_version())

    def test_unquoted_yaml_version_is_accepted(self):
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        self._write_skill_md(skill_dir, "version: 6.6.6")

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("6.6.6", render._skill_version())

    def test_single_quoted_yaml_version_is_accepted(self):
        skill_dir = self.tmp_path / "skill_root"
        fake_render = self._make_render_at(skill_dir)
        self._write_skill_md(skill_dir, "version: '5.5.5'")

        with patch.object(render, "__file__", str(fake_render)):
            self.assertEqual("5.5.5", render._skill_version())

if __name__ == "__main__":
    unittest.main()
