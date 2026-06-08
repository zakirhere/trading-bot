from pathlib import Path

import pytest

from tradebot import config


@pytest.fixture(autouse=True)
def _clear_alpaca_env(monkeypatch):
    for name in (
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "ALPACA_BASE_URL",
        "TRADEBOT_LIVE",
        "TRADEBOT_CONFIRM_LIVE_ALPACA",
        "NOTIFY_PROVIDER",
        "SLACK_WEBHOOK_URL",
        "TRADEBOT_ICL_PAPER_AUTORUN",
        "TRADEBOT_DCA_PAPER_AUTORUN",
        "TRADEBOT_ORB_OBSERVE",
    ):
        monkeypatch.delenv(name, raising=False)


def _env_file(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / ".env"
    defaults = {
        "ALPACA_API_KEY_ID": "paper-key",
        "ALPACA_API_SECRET_KEY": "paper-secret",
        "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "TRADEBOT_LIVE": "0",
    }
    defaults.update(values)
    path.write_text("\n".join(f"{k}={v}" for k, v in defaults.items()))
    return path


def test_paper_config_accepts_paper_url(tmp_path):
    cfg = config.load_alpaca_config(_env_file(tmp_path))

    assert cfg.mode == "paper"
    assert not cfg.is_live
    assert cfg.base_url == "https://paper-api.alpaca.markets"


def test_paper_config_rejects_live_url(tmp_path):
    with pytest.raises(RuntimeError, match="TRADEBOT_LIVE=0"):
        config.load_alpaca_config(
            _env_file(
                tmp_path,
                ALPACA_BASE_URL="https://api.alpaca.markets",
                TRADEBOT_LIVE="0",
            )
        )


def test_live_config_rejects_paper_url(tmp_path):
    with pytest.raises(RuntimeError, match="requires ALPACA_BASE_URL"):
        config.load_alpaca_config(
            _env_file(
                tmp_path,
                ALPACA_BASE_URL="https://paper-api.alpaca.markets",
                TRADEBOT_LIVE="1",
                TRADEBOT_CONFIRM_LIVE_ALPACA="I_UNDERSTAND_THIS_USES_REAL_MONEY",
            )
        )


def test_live_config_requires_second_confirmation(tmp_path):
    with pytest.raises(RuntimeError, match="TRADEBOT_CONFIRM_LIVE_ALPACA"):
        config.load_alpaca_config(
            _env_file(
                tmp_path,
                ALPACA_BASE_URL="https://api.alpaca.markets",
                TRADEBOT_LIVE="1",
            )
        )


def test_live_config_accepts_explicit_live_confirmation(tmp_path):
    cfg = config.load_alpaca_config(
        _env_file(
            tmp_path,
            ALPACA_BASE_URL="https://api.alpaca.markets",
            TRADEBOT_LIVE="1",
            TRADEBOT_CONFIRM_LIVE_ALPACA="I_UNDERSTAND_THIS_USES_REAL_MONEY",
        )
    )

    assert cfg.mode == "live"
    assert cfg.is_live


def test_notify_config_disabled_by_default(tmp_path):
    cfg = config.load_notify_config(_env_file(tmp_path))

    assert not cfg.enabled
    assert cfg.provider == ""


def test_notify_config_accepts_slack(tmp_path):
    path = _env_file(
        tmp_path,
        NOTIFY_PROVIDER="slack",
        SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T000/B000/abc",
    )

    cfg = config.load_notify_config(path)

    assert cfg.enabled
    assert cfg.provider == "slack"


def test_notify_config_rejects_non_slack_webhook(tmp_path):
    path = _env_file(
        tmp_path,
        NOTIFY_PROVIDER="slack",
        SLACK_WEBHOOK_URL="https://example.com/not-slack",
    )

    with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL"):
        config.load_notify_config(path)


def test_strategy_config_accepts_icl_autorun(tmp_path):
    cfg = config.load_strategy_config(
        _env_file(tmp_path, TRADEBOT_ICL_PAPER_AUTORUN="1")
    )

    assert cfg.icl_paper_autorun


def test_strategy_config_accepts_dca_and_orb_flags(tmp_path):
    cfg = config.load_strategy_config(
        _env_file(
            tmp_path,
            TRADEBOT_DCA_PAPER_AUTORUN="1",
            TRADEBOT_ORB_OBSERVE="1",
        )
    )

    assert cfg.dca_paper_autorun
    assert cfg.orb_observe
