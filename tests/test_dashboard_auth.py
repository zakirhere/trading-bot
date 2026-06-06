from tradebot import api, config


def test_session_value_is_deterministic():
    assert api._session_value("token") == api._session_value("token")
    assert api._session_value("token") != api._session_value("other-token")


def test_cookie_value_extracts_named_cookie():
    header = "x=1; tradebot_session=abc; y=2"

    assert api._cookie_value(header, "tradebot_session") == "abc"
    assert api._cookie_value(header, "missing") is None


def test_dashboard_token_generated_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "DASHBOARD_TOKEN_FILE", tmp_path / "dashboard_token")
    env_path = tmp_path / ".env"
    env_path.write_text("")

    cfg = config.load_dashboard_auth_config(env_path)

    assert len(cfg.token) >= 32
    assert cfg.source == str(tmp_path / "dashboard_token")
    assert (tmp_path / "dashboard_token").exists()


def test_dashboard_token_can_come_from_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DASHBOARD_TOKEN=0123456789abcdef\n")

    cfg = config.load_dashboard_auth_config(env_path)

    assert cfg.token == "0123456789abcdef"
    assert cfg.source == "env"
