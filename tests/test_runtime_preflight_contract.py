"""Contract tests for the SKILL.md runtime preflight snippet."""

import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.skill_contract import contract_documents, reference_text, root_text

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"


class RuntimePreflightContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_md = reference_text("runtime")

    def test_windows_localappdata_python_install_dir_is_scanned_first(self) -> None:
        scan_command = 'find "$windows_python_root" -maxdepth 2 -type f -iname python.exe'

        self.assertIn('windows_path_to_unix "$LOCALAPPDATA")/Programs/Python', self.skill_md)
        self.assertIn('windows_path_to_unix "$ProgramFiles"', self.skill_md)
        self.assertIn("printenv 'ProgramFiles(x86)'", self.skill_md)
        self.assertIn("cygpath -u", self.skill_md)
        self.assertIn(scan_command, self.skill_md)
        self.assertNotIn("/c/Users/", self.skill_md)
        self.assertNotIn("Python314/python.exe", self.skill_md)
        self.assertLess(self.skill_md.index(scan_command), self.skill_md.index("python3.14"))

    def test_candidate_selection_accepts_any_python_3_12_or_newer(self) -> None:
        self.assertIn("try_last30days_python()", self.skill_md)
        self.assertIn("sys.version_info >= (3, 12)", self.skill_md)

    def test_preflight_allows_explicit_interpreter_override(self) -> None:
        self.assertIn('if [ -z "${LAST30DAYS_PYTHON:-}" ]; then', self.skill_md)
        self.assertIn('ERROR: LAST30DAYS_PYTHON must point to Python 3.12+.', self.skill_md)

    def test_code_has_no_bare_positional_parameters(self) -> None:
        # Claude Code replaces $<digit> in a skill body with words from the
        # invocation arguments before the model reads it (anthropics/claude-code#94709),
        # so shell/awk code in SKILL.md spells positional parameters as ${1} / $(2).
        text = "\n".join(contract_documents().values())
        fenced = re.findall(r"```.*?```", text, re.S)
        inline = re.findall(r"`[^`\n]+`", re.sub(r"```.*?```", "", text, flags=re.S))
        hits = [m.group(0) for block in fenced + inline for m in re.finditer(r"\$\d+(?![A-Za-z0-9_])", block)]
        self.assertEqual(hits, [])


@unittest.skipUnless(shutil.which("bash") and shutil.which("awk"), "requires bash and awk")
class RuntimePreflightExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_md = reference_text("runtime")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("printenv", "find", "sort", "awk"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.skill_dir = self.root / "installed skill"
        self.skill_dir.symlink_to(SKILL_MD.parent, target_is_directory=True)
        self.save_dir = self.root / "saved research"
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "SKILL_DIR": str(self.skill_dir),
            "LAST30DAYS_MEMORY_DIR": str(self.save_dir),
        }

    def _substitute_arguments(self, source: str) -> str:
        arguments = shlex.split('runtime "Python releases" today')
        return re.sub(
            r"\$(\d+)(?!\w)",
            lambda match: arguments[int(match[1])]
            if int(match[1]) < len(arguments)
            else match[0],
            source,
        )

    def _python_candidate(self, name: str, version: tuple[int, int]) -> Path:
        candidate = self.bin / name
        version_probe = f"import sys; sys.version_info = {version!r}; exec(sys.argv[1])"
        candidate.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-c" ]; then\n'
            f"  exec {shlex.quote(sys.executable)} -c {shlex.quote(version_probe)} \"$2\"\n"
            "fi\n"
            f'exec {shlex.quote(sys.executable)} "$@"\n',
            encoding="utf-8",
        )
        candidate.chmod(0o755)
        return candidate

    def _run(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [shutil.which("bash"), "--noprofile", "--norc", "-c", self._substitute_arguments(source)],
            env=self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def _run_preflight(self) -> subprocess.CompletedProcess[str]:
        snippet = re.search(r"```bash\n(try_last30days_python\(\).*?)\n```", self.skill_md, re.S)
        self.assertIsNotNone(snippet)
        resolution = re.search(r"## Save-directory resolution.*?```bash\n(.*?)\n```", self.skill_md, re.S)
        self.assertIsNotNone(resolution)
        self.assertLess(snippet.start(), resolution.start())
        return self._run(snippet[1] + "\n" + resolution[1] + '\nprintf "Selected: <%s>\\n" "$LAST30DAYS_PYTHON"')

    def test_missing_python_stops_before_engine(self) -> None:
        result = self._run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: last30days v3 requires Python 3.12+", result.stderr)
        self.assertNotIn("Resolved save directory:", result.stderr)

    def test_old_python_stops_before_engine(self) -> None:
        self._python_candidate("python3", (3, 11))
        result = self._run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: last30days v3 requires Python 3.12+", result.stderr)
        self.assertNotIn("Resolved save directory:", result.stderr)

    def test_selects_supported_python_after_rejecting_old_candidate(self) -> None:
        self._python_candidate("python3.14", (3, 11))
        self._python_candidate("python3.13", (3, 13))
        self._python_candidate("python3.12", (3, 12))
        result = self._run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Selected: <python3.13>\n")
        self.assertIn(f"Resolved save directory: <{self.save_dir}>", result.stderr)

    def test_explicit_python_path_with_spaces_is_preserved(self) -> None:
        candidate = self._python_candidate("supported python", (3, 12))
        self.env["LAST30DAYS_PYTHON"] = str(candidate)
        result = self._run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"Selected: <{candidate}>\n")
        self.assertIn(f"Resolved save directory: <{self.save_dir}>", result.stderr)

    def test_invalid_explicit_python_does_not_fall_back(self) -> None:
        self._python_candidate("python3.12", (3, 12))
        old_python = self._python_candidate("old python", (3, 11))
        for candidate in (old_python, self.bin / "missing python"):
            with self.subTest(candidate=candidate):
                self.env["LAST30DAYS_PYTHON"] = str(candidate)
                result = self._run_preflight()
                self.assertEqual(result.returncode, 1)
                self.assertIn("ERROR: LAST30DAYS_PYTHON must point to Python 3.12+.", result.stderr)
                self.assertNotIn("Resolved save directory:", result.stderr)

    def test_windows_install_directory_with_spaces_precedes_path(self) -> None:
        local_appdata = self.root / "Local App Data"
        candidate = local_appdata / "Programs" / "Python" / "Python314" / "python.exe"
        candidate.parent.mkdir(parents=True)
        self._python_candidate("windows python", (3, 14)).replace(candidate)
        self._python_candidate("python3.12", (3, 12))
        self.env["LOCALAPPDATA"] = str(local_appdata)
        result = self._run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"Selected: <{candidate}>\n")
        self.assertIn(f"Resolved save directory: <{self.save_dir}>", result.stderr)

    def test_badge_version_awk_fallback_survives_argument_substitution(self) -> None:
        command = re.search(r"`(jq -r '\.version'.*?)`", root_text())
        self.assertIsNotNone(command)
        installed_skill = self.root / "badge skill"
        installed_skill.mkdir()
        self.env["SKILL_DIR"] = str(installed_skill)
        for version_line in ('version: "3.12.7"', "version: 3.12.7"):
            with self.subTest(version_line=version_line):
                (installed_skill / "SKILL.md").write_text(f"---\n{version_line}\n---\n", encoding="utf-8")
                result = self._run(command[1])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "3.12.7\n")


if __name__ == "__main__":
    unittest.main()
