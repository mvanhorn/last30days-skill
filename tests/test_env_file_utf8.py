from lib import env


def test_load_env_file_reads_utf8_regardless_of_locale(tmp_path):
    # setup_wizard writes .env with encoding="utf-8"; the loader must read it
    # back with the same encoding instead of the platform default (cp1252 on
    # most Windows locales), or non-ASCII values raise UnicodeDecodeError.
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# café notes ☕\nDISPLAY_NAME=Zoë\nAPI_LABEL=日本語キー\n",
        encoding="utf-8",
    )

    loaded = env.load_env_file(env_path)

    assert loaded["DISPLAY_NAME"] == "Zoë"
    assert loaded["API_LABEL"] == "日本語キー"
