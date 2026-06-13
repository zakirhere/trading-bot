from datetime import datetime
from zoneinfo import ZoneInfo

from tradebot import risk, state

ET = ZoneInfo("America/New_York")
MID_DAY = datetime(2026, 6, 5, 10, 0, tzinfo=ET)


def _state(halted=False, halt_reason=None):
    return state.State(halted=halted, halt_reason=halt_reason)


def test_happy_path():
    rc = risk.check_pretrade(
        s=_state(),
        is_live=False,
        expected_notional_usd=300,
        open_position_count=0,
        open_risk_usd=0,
        now_et=MID_DAY,
    )
    assert rc.allowed
    assert rc.reason is None


def test_halted_blocks():
    rc = risk.check_pretrade(
        s=_state(halted=True, halt_reason="manual"),
        is_live=False,
        expected_notional_usd=100,
        open_position_count=0,
        open_risk_usd=0,
        now_et=MID_DAY,
    )
    assert not rc.allowed
    assert "halted" in rc.reason


def test_per_trade_cap():
    rc = risk.check_pretrade(
        s=_state(),
        is_live=False,
        expected_notional_usd=600,
        open_position_count=0,
        open_risk_usd=0,
        now_et=MID_DAY,
    )
    assert not rc.allowed
    assert "per-trade cap" in rc.reason


def test_total_open_cap():
    rc = risk.check_pretrade(
        s=_state(),
        is_live=False,
        expected_notional_usd=400,
        open_position_count=2,
        open_risk_usd=9800,
        now_et=MID_DAY,
    )
    assert not rc.allowed
    assert "open risk" in rc.reason


def test_position_count_cap():
    rc = risk.check_pretrade(
        s=_state(),
        is_live=False,
        expected_notional_usd=100,
        open_position_count=40,
        open_risk_usd=0,
        now_et=MID_DAY,
    )
    assert not rc.allowed
    assert "position count" in rc.reason


def test_position_slots_count_vertical_spread_as_one():
    positions = [
        {
            "symbol": "SPY260630C00706000",
            "asset_class": "us_option",
            "qty": "-1",
            "market_value": "-4037",
        },
        {
            "symbol": "SPY260630C00707000",
            "asset_class": "us_option",
            "qty": "1",
            "market_value": "3788",
        },
    ]

    assert risk.estimate_position_slots(positions) == 1


def test_near_close_blocks():
    near_close = datetime(2026, 6, 5, 15, 57, tzinfo=ET)
    rc = risk.check_pretrade(
        s=_state(),
        is_live=False,
        expected_notional_usd=100,
        open_position_count=0,
        open_risk_usd=0,
        now_et=near_close,
    )
    assert not rc.allowed
    assert "close" in rc.reason


def test_estimate_open_risk_pairs_call_credit_spread():
    positions = [
        {
            "symbol": "SPY260630C00706000",
            "asset_class": "us_option",
            "qty": "-1",
            "market_value": "-4037",
        },
        {
            "symbol": "SPY260630C00707000",
            "asset_class": "us_option",
            "qty": "1",
            "market_value": "3788",
        },
    ]

    assert risk.estimate_open_risk_usd(positions) == 100.0


def test_estimate_open_risk_pairs_put_credit_spread_and_ignores_equity():
    positions = [
        {
            "symbol": "MSFT",
            "asset_class": "us_equity",
            "qty": "1",
            "market_value": "412.44",
        },
        {
            "symbol": "SPY260630P00754000",
            "asset_class": "us_option",
            "qty": "-1",
            "market_value": "-1734",
        },
        {
            "symbol": "SPY260630P00753000",
            "asset_class": "us_option",
            "qty": "1",
            "market_value": "1645",
        },
    ]

    assert risk.estimate_open_risk_usd(positions) == 100.0
