from contextlib import contextmanager
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
