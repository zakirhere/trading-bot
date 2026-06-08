from datetime import datetime, timedelta, timezone

import pytest

from tradebot import db


def _conn(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    return conn


def test_create_and_list_stock_market_buy(tmp_path):
    conn = _conn(tmp_path)
    try:
        req = db.create_stock_market_buy(conn, symbol="msft", qty=1)

        assert req.id == 1
        assert req.kind == "stock_market_buy"
        assert req.symbol == "MSFT"
        assert req.qty == 1
        assert req.status == db.STATUS_QUEUED

        requests = db.list_requests(conn)
        assert [r.id for r in requests] == [1]
    finally:
        conn.close()


def test_aapl_stock_orders_are_blocked(tmp_path):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError, match="AAPL"):
            db.create_stock_market_buy(conn, symbol="AAPL", qty=1)
    finally:
        conn.close()


def test_create_option_spread_open(tmp_path):
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
                "direction": "call_credit",
                "legs": [],
                "max_risk": "40.00",
            },
        )

        assert req.kind == "option_spread_open"
        assert req.symbol == "SPY"
        assert req.side == "sell"
        assert req.order_type == "limit_credit"
        assert req.payload["strategy"] == "ICL"
        assert req.payload["limit_credit"] == 0.60
    finally:
        conn.close()


def test_due_requests_only_returns_due_queued(tmp_path):
    conn = _conn(tmp_path)
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    past = (now - timedelta(minutes=1)).isoformat()
    future = (now + timedelta(minutes=1)).isoformat()
    try:
        due = db.create_stock_market_buy(conn, symbol="NVDA", qty=1, run_at=past)
        db.create_stock_market_buy(conn, symbol="MSFT", qty=1, run_at=future)
        blocked = db.create_stock_market_buy(conn, symbol="TSLA", qty=1)
        db.update_status(conn, blocked.id, status=db.STATUS_BLOCKED, reason="test")

        requests = db.due_requests(conn, now=now.isoformat())

        assert [r.id for r in requests] == [due.id]
    finally:
        conn.close()


def test_update_status_records_order_metadata(tmp_path):
    conn = _conn(tmp_path)
    try:
        req = db.create_stock_market_buy(conn, symbol="MSFT", qty=1)

        updated = db.update_status(
            conn,
            req.id,
            status=db.STATUS_SUBMITTED,
            broker_order_id="broker-1",
            client_order_id="client-1",
            reason="pending_new",
        )

        assert updated.status == db.STATUS_SUBMITTED
        assert updated.broker_order_id == "broker-1"
        assert updated.client_order_id == "client-1"
        assert updated.reason == "pending_new"
    finally:
        conn.close()


def test_strategy_events_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    try:
        event = db.create_strategy_event(
            conn,
            strategy="ORB",
            symbol="NVDA",
            event_type="breakout_up",
            payload={"price": "123.45"},
        )

        events = db.list_strategy_events(conn, strategy="ORB", since=event.created_at)

        assert len(events) == 1
        assert events[0].symbol == "NVDA"
        assert events[0].event_type == "breakout_up"
        assert events[0].payload["price"] == "123.45"
    finally:
        conn.close()
