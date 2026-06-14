from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
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


def test_active_strategy_requests_excludes_blocked_error_and_dry_run(tmp_path):
    conn = _conn(tmp_path)
    try:
        filled = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={"strategy": "DCA", "legs": []},
        )
        filled = db.update_status(conn, filled.id, status=db.STATUS_FILLED, reason="filled")
        blocked = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={"strategy": "DCA", "legs": []},
        )
        blocked = db.update_status(conn, blocked.id, status=db.STATUS_BLOCKED, reason="test")
        errored = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={"strategy": "DCA", "legs": []},
        )
        errored = db.update_status(conn, errored.id, status=db.STATUS_ERROR, reason="test")

        active = strategy_runner.active_strategy_requests([filled, blocked, errored])

        assert [req.id for req in active] == [filled.id]
    finally:
        conn.close()


def test_active_strategy_spread_count_is_per_strategy(tmp_path):
    conn = _conn(tmp_path)
    try:
        dca = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={"strategy": "DCA", "legs": []},
        )
        db.update_status(conn, dca.id, status=db.STATUS_FILLED, reason="filled")
        blocked_dca = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={"strategy": "DCA", "legs": []},
        )
        db.update_status(conn, blocked_dca.id, status=db.STATUS_BLOCKED, reason="test")
        icl = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={"strategy": "ICL", "legs": []},
        )
        db.update_status(conn, icl.id, status=db.STATUS_SUBMITTED, reason="new")

        assert strategy_runner.active_strategy_spread_count(conn, strategy="DCA") == 1
        assert strategy_runner.active_strategy_spread_count(conn, strategy="ICL") == 1
    finally:
        conn.close()


def test_dca_limit_credit_chases_two_cents_over_quote():
    assert strategy_runner.dca_limit_credit(Decimal("0.18")) == Decimal("0.20")
    assert strategy_runner.dca_limit_credit(Decimal("0.22")) == Decimal("0.24")


def test_dca_daily_target_is_ten():
    assert strategy_runner.DCA_DAILY_TARGET_ENTRIES == 10


class OrbData:
    def stock_bars(self, *, symbol, start, end, timeframe):
        assert symbol != "AAPL"
        if symbol == "NVDA":
            return [
                {"t": "2026-06-08T13:30:00Z", "h": 100.00, "l": 99.00, "c": 99.50},
                {"t": "2026-06-08T13:44:00Z", "h": 101.00, "l": 99.20, "c": 100.50},
                {"t": "2026-06-08T14:00:00Z", "h": 102.00, "l": 100.50, "c": 101.50},
            ]
        return [
            {"t": "2026-06-08T13:30:00Z", "h": 100.00, "l": 99.00, "c": 99.50},
            {"t": "2026-06-08T13:44:00Z", "h": 101.00, "l": 99.20, "c": 100.50},
            {"t": "2026-06-08T14:00:00Z", "h": 100.50, "l": 99.50, "c": 100.00},
        ]


def test_orb_scan_excludes_aapl_and_finds_breakout():
    now = datetime(2026, 6, 8, 10, 0, tzinfo=ET)

    signals = strategy_runner.scan_orb_signals(data=OrbData(), now_et=now, existing=[])

    assert [signal.symbol for signal in signals] == ["NVDA"]
    assert signals[0].side == "breakout_up"


def test_orb_scan_does_not_repeat_existing_signal():
    now = datetime(2026, 6, 8, 10, 0, tzinfo=ET)
    event = db.StrategyEvent(
        id=1,
        strategy="ORB",
        symbol="NVDA",
        event_type="breakout_up",
        payload={},
        created_at=now.isoformat(),
    )

    signals = strategy_runner.scan_orb_signals(data=OrbData(), now_et=now, existing=[event])

    assert signals == []


def test_icl_close_target_debit_rounds_down():
    assert strategy_runner.icl_close_target_debit(Decimal("0.60")) == Decimal("0.30")
    assert strategy_runner.icl_close_target_debit(Decimal("0.59")) == Decimal("0.29")
    assert strategy_runner.icl_close_target_debit(Decimal("0.61")) == Decimal("0.30")


class _FakeQuoteData:
    def __init__(self, quotes: dict[str, dict]):
        self.quotes = quotes
        self.calls = 0

    def option_latest_quotes(self, *, symbols):
        self.calls += 1
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def close(self):
        pass


def _seed_filled_icl_open(conn, *, credit, short="SPY260619C00755000", long="SPY260619C00756000"):
    req = db.create_option_spread_open(
        conn,
        symbol="SPY",
        qty=1,
        side="sell",
        limit_credit=float(credit),
        payload={
            "strategy": "ICL",
            "direction": "call_credit",
            "expiration_date": "2026-06-19",
            "short_symbol": short,
            "long_symbol": long,
            "short_strike": "755",
            "long_strike": "756",
            "credit": str(credit),
            "max_risk": "40.00",
            "legs": [],
        },
    )
    return db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")


def _enable_icl_autorun(monkeypatch, *, fake_data, alpaca_is_live=False):
    monkeypatch.setattr(
        strategy_runner.config,
        "load_strategy_config",
        lambda: SimpleNamespace(icl_paper_autorun=True, dca_paper_autorun=False, orb_observe=False),
    )
    monkeypatch.setattr(
        strategy_runner.config,
        "load_alpaca_config",
        lambda: SimpleNamespace(is_live=alpaca_is_live),
    )
    monkeypatch.setattr(strategy_runner.spy_credit_strategy, "AlpacaMarketData", lambda cfg: fake_data)
    # Default: pretend the broker has every leg the fake quote data knows about.
    # Tests that exercise the broker-presence guard can override this after.
    monkeypatch.setattr(
        strategy_runner,
        "open_option_symbols",
        lambda cfg: set(fake_data.quotes.keys()),
    )
    strategy_runner._icl_exit_last_run = None


def test_icl_exit_scheduler_queues_close_when_debit_at_target(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    # short_ask 0.40 - long_bid 0.10 = 0.30 = exactly the 50% target on a 0.60 credit.
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        open_req = _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        now = datetime(2026, 6, 12, 11, 0, tzinfo=ET)

        queued = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=now)

        assert len(queued) == 1
        close_req = queued[0]
        assert close_req.kind == "option_spread_close"
        assert close_req.order_type == "limit_debit"
        assert close_req.payload["limit_debit"] == 0.30
        assert close_req.payload["open_request_id"] == open_req.id
        assert close_req.payload["legs"][0]["position_intent"] == "buy_to_close"
        assert close_req.payload["legs"][1]["position_intent"] == "sell_to_close"
    finally:
        conn.close()


def test_icl_exit_scheduler_skips_when_debit_above_target(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.45", "bp": "0.43"},  # short_ask 0.45 - long_bid 0.10 = 0.35 > 0.30
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        now = datetime(2026, 6, 12, 11, 0, tzinfo=ET)

        queued = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=now)

        assert queued == []
        assert db.list_strategy_close_requests(conn, strategy="ICL") == []
    finally:
        conn.close()


def test_icl_exit_scheduler_idempotent_with_existing_close(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        open_req = _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        now = datetime(2026, 6, 12, 11, 0, tzinfo=ET)
        first = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=now)
        assert len(first) == 1

        # Run again 6 minutes later — should NOT double-queue, even though throttle has elapsed.
        strategy_runner._icl_exit_last_run = None
        later = datetime(2026, 6, 12, 11, 6, tzinfo=ET)
        second = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=later)

        assert second == []
        closes = db.list_strategy_close_requests(conn, strategy="ICL")
        assert len(closes) == 1
        assert closes[0].payload["open_request_id"] == open_req.id
    finally:
        conn.close()


def test_icl_exit_scheduler_throttles_within_5_minutes(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        now = datetime(2026, 6, 12, 11, 0, tzinfo=ET)
        first = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=now)
        assert len(first) == 1
        first_call_count = fake.calls

        # Same fake_data; second invocation 1 minute later should short-circuit before fetching quotes.
        soon = datetime(2026, 6, 12, 11, 1, tzinfo=ET)
        second = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=soon)

        assert second == []
        assert fake.calls == first_call_count
    finally:
        conn.close()


def test_icl_exit_scheduler_only_acts_on_filled_opens(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        # Submitted but not yet filled — should be ignored.
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "direction": "call_credit",
                "short_symbol": short,
                "long_symbol": long,
                "short_strike": "755",
                "long_strike": "756",
                "credit": "0.60",
                "legs": [],
            },
        )
        db.update_status(conn, req.id, status=db.STATUS_SUBMITTED, reason="new")
        now = datetime(2026, 6, 12, 11, 0, tzinfo=ET)

        queued = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=now)

        assert queued == []
    finally:
        conn.close()


def test_icl_exit_scheduler_runs_inside_no_new_trades_window(tmp_path, monkeypatch):
    # The opener stops 5 min before close (NO_NEW_TRADES_BEFORE_CLOSE_MIN), but the
    # exit scheduler must keep polling until 4:00 pm — closing reduces risk.
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        late = datetime(2026, 6, 12, 15, 58, tzinfo=ET)

        # Sanity: the opener's window says we're already shut.
        assert not strategy_runner.within_market_hours(late)
        # But the exit window is still open.
        assert strategy_runner.within_exit_window(late)

        queued = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=late)

        assert len(queued) == 1
        assert queued[0].kind == "option_spread_close"
    finally:
        conn.close()


def test_icl_exit_scheduler_blocks_after_market_close(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)

    try:
        _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        after = datetime(2026, 6, 12, 16, 5, tzinfo=ET)

        queued = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=after)

        assert queued == []
    finally:
        conn.close()


def test_icl_exit_scheduler_skips_when_legs_missing_from_broker(tmp_path, monkeypatch):
    # Locally-filled ICL whose broker legs are gone (manually closed, expired,
    # whatever) must NOT have a phantom close queued against it.
    conn = _conn(tmp_path)
    short = "SPY260619C00755000"
    long = "SPY260619C00756000"
    fake = _FakeQuoteData({
        short: {"ap": "0.40", "bp": "0.38"},
        long: {"ap": "0.12", "bp": "0.10"},
    })
    _enable_icl_autorun(monkeypatch, fake_data=fake)
    monkeypatch.setattr(strategy_runner, "open_option_symbols", lambda cfg: set())

    try:
        _seed_filled_icl_open(conn, credit=Decimal("0.60"), short=short, long=long)
        now = datetime(2026, 6, 12, 11, 0, tzinfo=ET)

        queued = strategy_runner.run_icl_exit_scheduler_once(conn, now_et=now)

        assert queued == []
        assert db.list_strategy_close_requests(conn, strategy="ICL") == []
        # And we should NOT have wasted a quote fetch — broker check kills it first.
        assert fake.calls == 0
    finally:
        conn.close()


def test_active_strategy_requests_excludes_open_with_filled_close(tmp_path):
    conn = _conn(tmp_path)
    try:
        open_req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "short_symbol": "SPY260619C00755000",
                "long_symbol": "SPY260619C00756000",
                "legs": [],
            },
        )
        db.update_status(conn, open_req.id, status=db.STATUS_FILLED, reason="filled")
        close_req = db.create_option_spread_close(
            conn,
            symbol="SPY",
            qty=1,
            limit_debit=0.30,
            payload={
                "strategy": "ICL",
                "open_request_id": open_req.id,
                "short_symbol": "SPY260619C00755000",
                "long_symbol": "SPY260619C00756000",
                "legs": [],
            },
        )
        db.update_status(conn, close_req.id, status=db.STATUS_FILLED, reason="filled")

        # Without the closed-id filter the open looks active. With it, the slot frees.
        all_reqs = db.list_strategy_spread_requests(conn, strategy="ICL", limit=100)
        assert strategy_runner.active_strategy_requests(all_reqs) == [
            db.get(conn, open_req.id)
        ]
        closed = strategy_runner.filled_close_open_ids(conn, strategy="ICL")
        assert open_req.id in closed
        active = strategy_runner.active_strategy_requests(
            all_reqs, closed_open_ids=closed
        )
        assert active == []

        # End-to-end: the spread-count helper should agree.
        assert strategy_runner.active_strategy_spread_count(conn, strategy="ICL") == 0
    finally:
        conn.close()


def test_active_strategy_spread_count_still_includes_open_with_pending_close(tmp_path):
    # Idempotency shouldn't free the slot prematurely — only a *filled* close retires the open.
    conn = _conn(tmp_path)
    try:
        open_req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={"strategy": "ICL", "legs": []},
        )
        db.update_status(conn, open_req.id, status=db.STATUS_FILLED, reason="filled")
        close_req = db.create_option_spread_close(
            conn,
            symbol="SPY",
            qty=1,
            limit_debit=0.30,
            payload={
                "strategy": "ICL",
                "open_request_id": open_req.id,
                "legs": [],
            },
        )
        # Close is queued/submitted but NOT filled — broker still has the legs.
        db.update_status(conn, close_req.id, status=db.STATUS_SUBMITTED, reason="new")

        assert strategy_runner.active_strategy_spread_count(conn, strategy="ICL") == 1
    finally:
        conn.close()
