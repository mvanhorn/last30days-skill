"""Exercise the suite's isolation in fresh pytest processes with dirty config."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("config_override", [None, "", "custom"])
def test_conftest_isolates_config_before_collection_and_between_tests(tmp_path, config_override):
    developer_home = tmp_path / "developer"
    global_dir = developer_home / ".config" / "last30days"
    custom_dir = tmp_path / "custom"
    project = tmp_path / "project"
    suite = project / "tests"
    suite.mkdir(parents=True)
    (project / ".git").mkdir()
    project_config = project / ".claude"
    project_config.mkdir()
    dummy_config = (
        "FROM_BROWSER=chrome\nBROWSER_CONSENT=1\nSETUP_COMPLETE=1\n"
        "OPENAI_API_KEY=dummy-file-key\nLAST30DAYS_TRUST_PROJECT_CONFIG=1\n"
    )
    for directory in (global_dir, custom_dir):
        directory.mkdir(parents=True)
        (directory / ".env").write_text(dummy_config, encoding="utf-8")
    (project_config / "last30days.env").write_text(dummy_config, encoding="utf-8")
    (suite / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text(), encoding="utf-8")
    (suite / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    child = textwrap.dedent(f"""
        import os
        import platform
        import shutil
        import subprocess
        from lib import env
        platform.system = lambda: 'Darwin'
        shutil.which = lambda name: name if name in ('security', 'pass') else None
        def store_lookup(command, **kwargs):
            assert command[0] != 'security', 'child read the macOS Keychain'
            assert os.environ['PASSWORD_STORE_DIR'] != {str(developer_home / '.password-store')!r}
            return subprocess.CompletedProcess(command, 1, '', '')
        subprocess.run = store_lookup
        assert env.get_config()['OPENAI_API_KEY'] is None
    """)
    (suite / "test_probe.py").write_text(textwrap.dedent(f"""
        import os
        import platform
        import shutil
        import subprocess
        import sys
        from pathlib import Path
        from unittest import mock
        from lib import env

        imported_config_file = env.CONFIG_FILE
        imported_browser = os.environ.get('FROM_BROWSER')
        imported_key = os.environ.get('OPENAI_API_KEY')

        def test_collection_was_isolated():
            assert imported_config_file not in {{
                Path({str(global_dir / '.env')!r}), Path({str(custom_dir / '.env')!r})
            }}
            assert imported_browser is None
            assert imported_key is None

        def test_defaults_and_explicit_config():
            config = env.get_config(env.ConfigLoadPolicy(allow_project_config=True))
            assert config['FROM_BROWSER'] is None
            assert config['OPENAI_API_KEY'] is None
            assert config['SETUP_COMPLETE'] is None
            assert config['INCLUDE_SOURCES'] == ''
            assert config['LAST30DAYS_X_BACKEND'] is None
            assert 'LAST30DAYS_SKIP_KEYCHAIN' not in os.environ
            env.CONFIG_FILE.write_text('OPENAI_API_KEY=dummy-test-key\\n')
            assert env.get_config()['OPENAI_API_KEY'] == 'dummy-test-key'
            local = Path.cwd() / '.claude'
            local.mkdir()
            (local / 'last30days.env').write_text('OPENAI_API_KEY=dummy-project-key\\n')
            assert env.get_config(env.ConfigLoadPolicy(allow_project_config=True))['OPENAI_API_KEY'] == 'dummy-project-key'
            os.environ['FROM_BROWSER'] = 'firefox'
            os.environ['LAST30DAYS_X_BACKEND'] = 'grok'

        def test_next_test_starts_clean():
            config = env.get_config(env.ConfigLoadPolicy(allow_project_config=True))
            assert config['FROM_BROWSER'] is None
            assert config['OPENAI_API_KEY'] is None
            assert config['LAST30DAYS_X_BACKEND'] is None
            with mock.patch.object(platform, 'system', return_value='Darwin'), mock.patch.object(
                subprocess, 'run', side_effect=AssertionError('ambient credential lookup')
            ):
                assert env._load_keychain(['OPENAI_API_KEY']) == {{}}
                assert env._load_pass(['OPENAI_API_KEY'], 'last30days/') == {{}}

        def test_cli_child_cannot_read_ambient_stores():
            result = subprocess.run([sys.executable, '-c', {child!r}], capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
            expected_home = str(Path.home())
            with mock.patch.dict(os.environ, {{}}, clear=True):
                result = subprocess.run(
                    [sys.executable, '-c', "import os; print(os.environ['HOME'])"],
                    capture_output=True, text=True,
                )
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == expected_home
    """), encoding="utf-8")
    environment = dict(os.environ)
    environment.update({
        "HOME": str(developer_home),
        "USERPROFILE": str(developer_home),
        "PASSWORD_STORE_DIR": str(developer_home / ".password-store"),
        "FROM_BROWSER": "chrome",
        "BROWSER_CONSENT": "1",
        "OPENAI_API_KEY": "dummy-ambient-key",
        "LAST30DAYS_X_BACKEND": "grok",
        "LAST30DAYS_TRUST_PROJECT_CONFIG": "1",
        "LAST30DAYS_SKIP_KEYCHAIN": "1",
        "INCLUDE_SOURCES": "threads",
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "skills" / "last30days" / "scripts")]),
    })
    environment.pop("LAST30DAYS_CONFIG_DIR", None)
    if config_override is not None:
        environment["LAST30DAYS_CONFIG_DIR"] = str(custom_dir) if config_override else ""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--confcutdir", str(suite), str(suite)],
        cwd=project, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout
