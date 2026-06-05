from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradebot import nl


LOCAL = ZoneInfo("America/Los_Angeles")


def test_parse_immediate_stock_buy():
    parsed = nl.parse_stock_buy("buy 1 AAPL now", now=datetime(2026, 6, 5, 9, 0, tzinfo=LOCAL))

    assert parsed.symbol == "AAPL"
    assert parsed.qty == 1
    assert parsed.run_at is None
    assert not parsed.dry_run


def test_parse_dry_run_noon_today():
    parsed = nl.parse_stock_buy(
        "dry run buy 2 shares of MSFT at noon today",
        now=datetime(2026, 6, 5, 9, 0, tzinfo=LOCAL),
    )

    assert parsed.symbol == "MSFT"
    assert parsed.qty == 2
    assert parsed.dry_run
    assert parsed.run_at == "2026-06-05T19:00:00+00:00"


def test_parse_tomorrow_pm_time():
    parsed = nl.parse_stock_buy(
        "buy 1 AAPL tomorrow at 12:30 PM",
        now=datetime(2026, 6, 5, 9, 0, tzinfo=LOCAL),
    )

    assert parsed.run_at == "2026-06-06T19:30:00+00:00"


def test_rejects_ambiguous_time_without_ampm():
    with pytest.raises(nl.ParseError, match="AM or PM"):
        nl.parse_stock_buy("buy 1 AAPL at 12:30", now=datetime(2026, 6, 5, 9, 0, tzinfo=LOCAL))


def test_rejects_options_for_now():
    with pytest.raises(nl.ParseError, match="stock market buys only"):
        nl.parse_stock_buy("buy 1 SPY call")


def test_rejects_missing_action():
    with pytest.raises(nl.ParseError, match="Use a stock buy"):
        nl.parse_stock_buy("AAPL 1 share")
