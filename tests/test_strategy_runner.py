from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradebot import db, spy_credit_strategy, strategy_runner

ET = ZoneInfo("America/New_York")


def _conn(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    return conn


def test_next_due_slot_waits_until_first_slot():
    now = datetime(2026, 6, 8, 9, 31, tzinfo=ET)

    slot = strategy_runner.next_due_slot(now_et=now, existing=[])

    assert slot is None


def test_next_due_slot_skips_used_slot(tmp_path):
    conn = _conn(tmp_path)
    now = datetime(2026, 6, 8, 16, 0, tzinfo=ET)
    first_slot = spy_credit_strategy.random_entry_times(
        now.date(),
        spy_credit_strategy.ICL_TARGET_ACTIVE_TRADES,
        __import__("random").Random(20260608),
    )[0]
    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "direction": "call_credit",
                "expiration_date": "2026-06-19",
                "short_strike": "740",
                "long_strike": "741",
                "slot_time": first_slot.isoformat(),
                "legs": [],
            },
        )

        slot = strategy_runner.next_due_slot(now_et=now, existing=[req])

        assert slot is not None
        assert slot != first_slot
    finally:
        conn.close()


def test_blocked_spreads_uses_payload_identity(tmp_path):
    conn = _conn(tmp_path)
    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "direction": "put_credit",
                "expiration_date": "2026-06-19",
                "short_strike": "740",
                "long_strike": "739",
                "legs": [],
            },
        )

        blocked = strategy_runner.blocked_spreads([req])

        assert (
            "put_credit",
            datetime(2026, 6, 19).date(),
            Decimal("740"),
            Decimal("739"),
        ) in blocked
    finally:
        conn.close()
