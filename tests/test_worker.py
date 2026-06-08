from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

from tradebot import db, state, worker


def _conn(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    return conn


def test_reconcile_submitted_orders_marks_filled(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State(
        orders=[
            state.OrderRecord(
                idempotency_key="queue_1_option_spread_open_SPY",
                broker_order_id="broker-1",
                symbol="SPY",
                side="sell",
                qty=1,
                submitted_at="2026-06-08T16:00:00+00:00",
                status="pending_new",
            )
        ]
    )

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_order(self, order_id):
            assert order_id == "broker-1"
            return {"status": "filled"}

        def close(self):
            pass

    @contextmanager
    def fake_transaction():
        yield fake_state

    monkeypatch.setattr(worker.config, "load_alpaca_config", lambda: SimpleNamespace())
    monkeypatch.setattr(worker.broker, "AlpacaBroker", FakeBroker)
    monkeypatch.setattr(worker.state, "transaction", fake_transaction)

    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.58,
            payload={"legs": [], "max_risk": "42.00"},
        )
        db.update_status(
            conn,
            req.id,
            status=db.STATUS_SUBMITTED,
            broker_order_id="broker-1",
            client_order_id="queue_1_option_spread_open_SPY",
            reason="pending_new",
        )

        changed = worker.reconcile_submitted_orders(conn)

        assert len(changed) == 1
        updated = db.get(conn, req.id)
        assert updated.status == db.STATUS_FILLED
        assert updated.reason == "filled"
        assert fake_state.orders[0].status == "filled"
    finally:
        conn.close()


def test_mleg_credit_order_uses_negative_net_limit(tmp_path):
    conn = _conn(tmp_path)
    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.58,
            payload={"legs": [], "max_risk": "42.00"},
        )

        assert worker.mleg_net_limit_price(req) == -0.58
    finally:
        conn.close()


def test_execute_option_spread_submits_credit_as_negative_limit(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State()
    submitted = {}

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return []

        def submit_mleg_limit_order(self, *, qty, limit_price, legs, client_order_id, time_in_force="day"):
            submitted["qty"] = qty
            submitted["limit_price"] = limit_price
            submitted["legs"] = legs
            submitted["client_order_id"] = client_order_id
            return worker.broker.OrderResult(
                broker_order_id="broker-1",
                status="pending_new",
                symbol="MLEG",
                side="mleg",
                qty=qty,
                raw={},
            )

        def close(self):
            pass

    @contextmanager
    def fake_transaction():
        yield fake_state

    monkeypatch.setattr(worker.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(worker.broker, "AlpacaBroker", FakeBroker)
    monkeypatch.setattr(worker.state, "load", lambda: fake_state)
    monkeypatch.setattr(worker.state, "transaction", fake_transaction)
    monkeypatch.setattr(worker.notify, "send", lambda alert: None)
    monkeypatch.setattr(worker, "requote_credit_for_request", lambda cfg, req: Decimal("0.58"))

    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.58,
            payload={
                "strategy": "ICL",
                "direction": "put_credit",
                "short_symbol": "SPY260630P00754000",
                "long_symbol": "SPY260630P00753000",
                "legs": [
                    {"symbol": "SPY260630P00754000", "side": "sell"},
                    {"symbol": "SPY260630P00753000", "side": "buy"},
                ],
                "max_risk": "42.00",
                "target_min": "0.58",
                "target_max": "0.62",
                "reject_at_or_above": "0.65",
            },
        )

        updated = worker.execute_request(conn, req)

        assert submitted["limit_price"] == -0.58
        assert updated.status == db.STATUS_SUBMITTED
        assert updated.reason == "pending_new"
    finally:
        conn.close()


def test_execute_option_spread_blocks_out_of_band_requote(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State()
    submitted = {"called": False}

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return []

        def submit_mleg_limit_order(self, **kwargs):
            submitted["called"] = True
            raise AssertionError("should not submit out-of-band spread")

        def close(self):
            pass

    monkeypatch.setattr(worker.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(worker.broker, "AlpacaBroker", FakeBroker)
    monkeypatch.setattr(worker.state, "load", lambda: fake_state)
    monkeypatch.setattr(worker.notify, "send", lambda alert: None)
    monkeypatch.setattr(worker, "requote_credit_for_request", lambda cfg, req: Decimal("0.54"))

    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={
                "strategy": "DCA",
                "direction": "call_credit",
                "short_symbol": "SPY260630C00706000",
                "long_symbol": "SPY260630C00707000",
                "legs": [
                    {"symbol": "SPY260630C00706000", "side": "sell"},
                    {"symbol": "SPY260630C00707000", "side": "buy"},
                ],
                "max_risk": "80.00",
                "target_min": "0.20",
                "target_max": "0.22",
                "reject_at_or_above": "0.25",
            },
        )

        updated = worker.execute_request(conn, req)

        assert not submitted["called"]
        assert updated.status == db.STATUS_BLOCKED
        assert updated.reason == "requote credit 0.54 > target max 0.22"
    finally:
        conn.close()
