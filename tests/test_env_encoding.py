from lib import env


def test_load_env_file_strips_utf8_bom(tmp_path):
    # A Windows editor (e.g. Notepad) commonly saves .env with a UTF-8 BOM.
    # Without an explicit encoding, open() prepends the BOM to the first key,
    # corrupting it (KeyError below). utf-8-sig transparently strips the BOM.
    env_path = tmp_path / ".env"
    env_path.write_bytes("DISPLAY_NAME=café\nREAL_KEY=ok\n".encode("utf-8-sig"))

    loaded = env.load_env_file(env_path)

    assert loaded["DISPLAY_NAME"] == "café"
    assert loaded["REAL_KEY"] == "ok"


def test_load_env_file_falls_back_to_locale_encoding(tmp_path, monkeypatch):
    # A pre-existing .env saved in a legacy codepage (e.g. cp1252 on Windows)
    # loaded fine when open() used the locale decoder. Force the fallback locale
    # to cp1252 so the test is deterministic on any runner, and assert the value
    # decodes correctly rather than being replaced/corrupted.
    monkeypatch.setattr(
        env.locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252"
    )
    env_path = tmp_path / ".env"
    env_path.write_bytes("DISPLAY_NAME=Jos\xe9\nREAL_KEY=ok\n".encode("cp1252"))

    loaded = env.load_env_file(env_path)

    assert loaded["DISPLAY_NAME"] == "José"
    assert loaded["REAL_KEY"] == "ok"
