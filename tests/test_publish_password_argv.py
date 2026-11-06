"""--publish-password still works but warns: argv secrets leak via ps/history."""

import argparse

import last30days as cli


def test_argv_password_warns_but_is_honored(capsys):
    args = argparse.Namespace(publish_password="dummy-shared-pass")
    assert cli._publish_password_for_args(args, {}) == "dummy-shared-pass"
    err = capsys.readouterr().err
    assert "LAST30DAYS_PUBLISH_PASSWORD" in err
    assert "dummy-shared-pass" not in err


def test_env_password_does_not_warn(capsys, monkeypatch):
    monkeypatch.setenv("LAST30DAYS_PUBLISH_PASSWORD", "dummy-env-pass")
    args = argparse.Namespace(publish_password=None)
    assert cli._publish_password_for_args(args, {}) == "dummy-env-pass"
    assert capsys.readouterr().err == ""
