from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

from . import broker, config, db, notify, spy_credit_strategy

ORB_UNIVERSE = ("SPY", "QQQ", "NVDA", "TSLA", "MSFT")
NEVER_TRADE_SYMBOLS = {"AAPL"}
ORB_RANGE_END = time(9, 45)
ORB_ENTRY_CUTOFF = time(11, 30)
DCA_TARGET_MIN = Decimal("0.18")
DCA_TARGET_MAX = Decimal("0.22")
DCA_REQUOTE_MIN = Decimal("0.17")
DCA_DAILY_TARGET_ENTRIES = 10
DCA_MAX_ACTIVE_SPREADS = 20
ICL_MAX_ACTIVE_SPREADS = 20
DCA_LIMIT_CREDIT_MARKUP = Decimal("0.02")
DCA_REJECT_AT_OR_ABOVE = Decimal("0.25")
DCA_QUOTE_BASIS = spy_credit_strategy.QUOTE_BASIS_MIDPOINT
ICL_PROFIT_TARGET_FRACTION = Decimal("0.5")
ICL_EXIT_MIN_POLL_INTERVAL = timedelta(minutes=5)

_icl_exit_last_run: datetime | None = None


@dataclass(frozen=True)
class OrbSignal:
    symbol: str
    side: str
    price: Decimal
    range_high: Decimal
    range_low: Decimal
    observed_at: datetime


def run_icl_scheduler_once(conn: sqlite3.Connection, *, now_et: datetime | None = None) -> db.TradeRequest | None:
    strategy_cfg = config.load_strategy_config()
    if not strategy_cfg.icl_paper_autorun:
        return None

    alpaca_cfg = config.load_alpaca_config()
    if alpaca_cfg.is_live:
        raise RuntimeError("ICL autorun is paper-only")

    if active_strategy_spread_count(conn, strategy="ICL") >= ICL_MAX_ACTIVE_SPREADS:
        return None

    now_et = now_et or datetime.now(spy_credit_strategy.ET)
    if not within_market_hours(now_et):
        return None

    existing = icl_requests_for_day(conn, now_et)
    active_existing = active_strategy_requests(existing)
    if len(active_existing) >= spy_credit_strategy.ICL_TARGET_ACTIVE_TRADES:
        return None
    if len(existing) >= spy_credit_strategy.ICL_MAX_DAILY_ENTRIES:
        return None

    due_slot = next_due_slot(now_et=now_et, existing=existing)
    if due_slot is None:
        return None

    blocked_symbols = open_option_symbols(alpaca_cfg)
    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        candidate = select_icl_candidate(
            data=data,
            now_et=now_et,
            existing=existing,
            blocked_symbols=blocked_symbols,
        )
    finally:
        data.close()
    if candidate is None:
        return None

    payload = {
        **spy_credit_strategy.candidate_payload(candidate),
        "target_min": "0.58",
        "target_max": "0.62",
        "reject_at_or_above": "0.65",
        "slot_time": due_slot.isoformat(),
        "selected_at": now_et.isoformat(),
    }
    return db.create_option_spread_open(
        conn,
        symbol="SPY",
        qty=1,
        side="sell",
        limit_credit=float(candidate.credit),
        dry_run=False,
        payload=payload,
    )


def run_icl_exit_scheduler_once(
    conn: sqlite3.Connection,
    *,
    now_et: datetime | None = None,
) -> list[db.TradeRequest]:
    global _icl_exit_last_run
    strategy_cfg = config.load_strategy_config()
    if not strategy_cfg.icl_paper_autorun:
        return []

    alpaca_cfg = config.load_alpaca_config()
    if alpaca_cfg.is_live:
        raise RuntimeError("ICL autorun is paper-only")

    now_et = now_et or datetime.now(spy_credit_strategy.ET)
    if not within_market_hours(now_et):
        return []

    if (
        _icl_exit_last_run is not None
        and now_et - _icl_exit_last_run < ICL_EXIT_MIN_POLL_INTERVAL
    ):
        return []
    _icl_exit_last_run = now_et

    opens = db.list_strategy_spread_requests(conn, strategy="ICL", limit=1000)
    closes = db.list_strategy_close_requests(conn, strategy="ICL", limit=1000)
    closed_open_ids = {
        int(req.payload["open_request_id"])
        for req in closes
        if req.status not in {db.STATUS_BLOCKED, db.STATUS_ERROR}
        and req.payload.get("open_request_id") is not None
    }

    candidates: list[db.TradeRequest] = []
    for open_req in opens:
        if open_req.status != db.STATUS_FILLED:
            continue
        if open_req.id in closed_open_ids:
            continue
        if not {"short_symbol", "long_symbol", "credit"} <= set(open_req.payload):
            continue
        candidates.append(open_req)

    if not candidates:
        return []

    queued: list[db.TradeRequest] = []
    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        for open_req in candidates:
            queued_req = _maybe_queue_icl_close(conn, open_req, data=data, now_et=now_et)
            if queued_req is not None:
                queued.append(queued_req)
    finally:
        data.close()
    return queued


def _maybe_queue_icl_close(
    conn: sqlite3.Connection,
    open_req: db.TradeRequest,
    *,
    data: spy_credit_strategy.AlpacaMarketData,
    now_et: datetime,
) -> db.TradeRequest | None:
    payload = open_req.payload
    short_symbol = payload["short_symbol"]
    long_symbol = payload["long_symbol"]
    entry_credit = Decimal(str(payload["credit"]))
    target_debit = icl_close_target_debit(entry_credit)

    quotes = data.option_latest_quotes(symbols=[short_symbol, long_symbol])
    short_quote = quotes.get(short_symbol)
    long_quote = quotes.get(long_symbol)
    if not short_quote or not long_quote:
        return None
    quoted_debit = spy_credit_strategy.spread_debit_from_quotes(
        short_quote=short_quote,
        long_quote=long_quote,
    )
    if quoted_debit is None:
        return None
    if quoted_debit > target_debit:
        return None

    close_payload = {
        "strategy": "ICL",
        "open_request_id": open_req.id,
        "direction": payload.get("direction"),
        "expiration_date": payload.get("expiration_date"),
        "short_symbol": short_symbol,
        "long_symbol": long_symbol,
        "short_strike": payload.get("short_strike"),
        "long_strike": payload.get("long_strike"),
        "entry_credit": str(entry_credit),
        "profit_target_fraction": str(ICL_PROFIT_TARGET_FRACTION),
        "quoted_debit": str(quoted_debit),
        "selected_at": now_et.isoformat(),
        "legs": spy_credit_strategy.closing_legs(
            short_symbol=short_symbol,
            long_symbol=long_symbol,
        ),
    }
    return db.create_option_spread_close(
        conn,
        symbol=open_req.symbol,
        qty=int(open_req.qty),
        limit_debit=float(target_debit),
        payload=close_payload,
    )


def icl_close_target_debit(entry_credit: Decimal) -> Decimal:
    return (entry_credit * ICL_PROFIT_TARGET_FRACTION).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )


def run_dca_scheduler_once(conn: sqlite3.Connection, *, now_et: datetime | None = None) -> db.TradeRequest | None:
    strategy_cfg = config.load_strategy_config()
    if not strategy_cfg.dca_paper_autorun:
        return None

    alpaca_cfg = config.load_alpaca_config()
    if alpaca_cfg.is_live:
        raise RuntimeError("DCA autorun is paper-only")

    if active_strategy_spread_count(conn, strategy="DCA") >= DCA_MAX_ACTIVE_SPREADS:
        return None

    now_et = now_et or datetime.now(spy_credit_strategy.ET)
    if not within_market_hours(now_et):
        return None

    existing = strategy_requests_for_day(conn, now_et, strategy="DCA")
    active_existing = active_strategy_requests(existing)
    if len(active_existing) >= DCA_DAILY_TARGET_ENTRIES:
        return None

    due_slot = next_due_slot(now_et=now_et, existing=existing, target_count=DCA_DAILY_TARGET_ENTRIES)
    if due_slot is None:
        return None

    blocked_symbols = open_option_symbols(alpaca_cfg)
    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        candidate = select_dca_candidate(
            data=data,
            now_et=now_et,
            existing=existing,
            blocked_symbols=blocked_symbols,
        )
    finally:
        data.close()
    if candidate is None:
        return None

    payload = {
        **spy_credit_strategy.candidate_payload(candidate),
        "strategy": "DCA",
        "quote_basis": DCA_QUOTE_BASIS,
        "quoted_credit": str(candidate.credit),
        "limit_credit_markup": str(DCA_LIMIT_CREDIT_MARKUP),
        "target_min": str(DCA_REQUOTE_MIN),
        "selection_target_min": str(DCA_TARGET_MIN),
        "target_max": str(DCA_TARGET_MAX),
        "reject_at_or_above": str(DCA_REJECT_AT_OR_ABOVE),
        "slot_time": due_slot.isoformat(),
        "selected_at": now_et.isoformat(),
    }
    return db.create_option_spread_open(
        conn,
        symbol="SPY",
        qty=1,
        side="sell",
        limit_credit=float(dca_limit_credit(candidate.credit)),
        dry_run=False,
        payload=payload,
    )


def run_orb_observer_once(conn: sqlite3.Connection, *, now_et: datetime | None = None) -> list[OrbSignal]:
    strategy_cfg = config.load_strategy_config()
    if not strategy_cfg.orb_observe:
        return []

    alpaca_cfg = config.load_alpaca_config()
    now_et = now_et or datetime.now(spy_credit_strategy.ET)
    if not within_orb_observe_window(now_et):
        return []

    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        signals = scan_orb_signals(data=data, now_et=now_et, existing=orb_events_for_day(conn, now_et))
    finally:
        data.close()

    for signal in signals:
        payload = {
            "strategy": "ORB",
            "symbol": signal.symbol,
            "side": signal.side,
            "price": str(signal.price),
            "range_high": str(signal.range_high),
            "range_low": str(signal.range_low),
            "observed_at": signal.observed_at.isoformat(),
        }
        db.create_strategy_event(conn, strategy="ORB", symbol=signal.symbol, event_type=signal.side, payload=payload)
        notify.send(
            notify.Alert(
                level="info",
                title="ORB signal observed",
                message=f"{signal.symbol} {signal.side} breakout observed at {signal.price}. No order submitted.",
                fields={
                    "range_high": str(signal.range_high),
                    "range_low": str(signal.range_low),
                },
            )
        )
    return signals


def select_icl_candidate(
    *,
    data: spy_credit_strategy.AlpacaMarketData,
    now_et: datetime,
    existing: list[db.TradeRequest],
    blocked_symbols: set[str] | None = None,
) -> spy_credit_strategy.SpreadCandidate | None:
    day_start = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_OPEN, spy_credit_strategy.ET)
    spy_bars = data.stock_bars(symbol="SPY", start=day_start, end=now_et, timeframe="1Min")
    if not spy_bars:
        return None
    spy_price = spy_credit_strategy.bar_close_at_or_before(spy_bars, now_et)
    prev_close = spy_credit_strategy.previous_close(data, now_et.date())
    direction = spy_credit_strategy.income_direction(spy_price=spy_price, previous_close=prev_close)
    expiries = spy_credit_strategy.available_expiries(
        data,
        trading_date=now_et.date(),
        min_dte=8,
        max_dte=42,
    )
    blocked = blocked_spreads(existing)
    rng = random.Random(int(now_et.strftime("%Y%m%d")))
    for expiry in spy_credit_strategy.ranked_expiries(expiries, now_et, rng):
        candidate = spy_credit_strategy.find_best_candidate_from_latest_quotes(
            data=data,
            direction=direction,
            expiration_date=expiry,
            target_min=Decimal("0.58"),
            target_max=Decimal("0.62"),
            reject_at_or_above=Decimal("0.65"),
            spread_width=Decimal("1"),
            blocked_spreads=blocked,
            blocked_symbols=blocked_symbols,
            underlying_price=spy_price,
        )
        if candidate:
            return candidate
    return None


def select_dca_candidate(
    *,
    data: spy_credit_strategy.AlpacaMarketData,
    now_et: datetime,
    existing: list[db.TradeRequest],
    blocked_symbols: set[str] | None = None,
) -> spy_credit_strategy.SpreadCandidate | None:
    day_start = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_OPEN, spy_credit_strategy.ET)
    spy_bars = data.stock_bars(symbol="SPY", start=day_start, end=now_et, timeframe="1Min")
    if not spy_bars:
        return None
    spy_price = spy_credit_strategy.bar_close_at_or_before(spy_bars, now_et)
    prev_close = spy_credit_strategy.previous_close(data, now_et.date())
    direction = "call_credit" if spy_price > prev_close else "put_credit"
    expiries = spy_credit_strategy.available_expiries(
        data,
        trading_date=now_et.date(),
        min_dte=8,
        max_dte=42,
    )
    blocked = blocked_spreads(existing)
    rng = random.Random(int(now_et.strftime("%Y%m%d")))
    candidate: spy_credit_strategy.SpreadCandidate | None = None
    for expiry in spy_credit_strategy.ranked_expiries(expiries, now_et, rng):
        candidate = spy_credit_strategy.find_best_candidate_from_latest_quotes(
            data=data,
            direction=direction,
            expiration_date=expiry,
            target_min=DCA_TARGET_MIN,
            target_max=DCA_TARGET_MAX,
            reject_at_or_above=DCA_REJECT_AT_OR_ABOVE,
            spread_width=Decimal("1"),
            blocked_spreads=blocked,
            blocked_symbols=blocked_symbols,
            underlying_price=spy_price,
            quote_basis=DCA_QUOTE_BASIS,
            prefer_farther_otm=True,
        )
        if candidate:
            return candidate
    return None


def dca_limit_credit(quoted_credit: Decimal) -> Decimal:
    return spy_credit_strategy.money(quoted_credit + DCA_LIMIT_CREDIT_MARKUP)


def icl_requests_for_day(conn: sqlite3.Connection, now_et: datetime) -> list[db.TradeRequest]:
    return strategy_requests_for_day(conn, now_et, strategy="ICL")


def strategy_requests_for_day(conn: sqlite3.Connection, now_et: datetime, *, strategy: str) -> list[db.TradeRequest]:
    rows = db.list_requests(conn, limit=500)
    out: list[db.TradeRequest] = []
    for req in rows:
        if req.kind != "option_spread_open":
            continue
        if req.payload.get("strategy") != strategy:
            continue
        created = datetime.fromisoformat(req.created_at).astimezone(spy_credit_strategy.ET)
        if created.date() == now_et.date():
            out.append(req)
    return out


def blocked_spreads(requests: list[db.TradeRequest]) -> set[tuple[str, date, Decimal, Decimal]]:
    blocked = set()
    for req in requests:
        payload = req.payload
        if not {"direction", "expiration_date", "short_strike", "long_strike"} <= set(payload):
            continue
        blocked.add(
            spy_credit_strategy.spread_key(
                direction=payload["direction"],
                expiration_date=date.fromisoformat(payload["expiration_date"]),
                short_strike=Decimal(payload["short_strike"]),
                long_strike=Decimal(payload["long_strike"]),
            )
        )
    return blocked


def open_option_symbols(alpaca_cfg: config.AlpacaConfig) -> set[str]:
    b = broker.AlpacaBroker(alpaca_cfg)
    try:
        return {
            str(position.get("symbol"))
            for position in b.get_positions()
            if position.get("asset_class") == "us_option"
            and abs(float(position.get("qty") or 0)) > 0
            and position.get("symbol")
        }
    finally:
        b.close()


def active_strategy_requests(requests: list[db.TradeRequest]) -> list[db.TradeRequest]:
    inactive_statuses = {db.STATUS_BLOCKED, db.STATUS_ERROR, db.STATUS_DRY_RUN}
    return [req for req in requests if req.status not in inactive_statuses]


def active_strategy_spread_count(conn: sqlite3.Connection, *, strategy: str) -> int:
    return len(active_strategy_requests(db.list_strategy_spread_requests(conn, strategy=strategy, limit=1000)))


def next_due_slot(
    *,
    now_et: datetime,
    existing: list[db.TradeRequest],
    target_count: int = spy_credit_strategy.ICL_TARGET_ACTIVE_TRADES,
) -> datetime | None:
    rng = random.Random(int(now_et.strftime("%Y%m%d")))
    slots = spy_credit_strategy.random_entry_times(
        now_et.date(),
        target_count,
        rng,
    )
    used_slots = {req.payload.get("slot_time") for req in existing}
    for slot in slots:
        if slot.isoformat() in used_slots:
            continue
        if slot <= now_et:
            return slot
        return None
    return None


def within_market_hours(now_et: datetime) -> bool:
    open_dt = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_OPEN, spy_credit_strategy.ET)
    close_dt = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_CLOSE, spy_credit_strategy.ET)
    cutoff = close_dt - spy_credit_strategy.timedelta(minutes=config.NO_NEW_TRADES_BEFORE_CLOSE_MIN)
    return open_dt <= now_et <= cutoff


def within_orb_observe_window(now_et: datetime) -> bool:
    start = datetime.combine(now_et.date(), ORB_RANGE_END, spy_credit_strategy.ET)
    end = datetime.combine(now_et.date(), ORB_ENTRY_CUTOFF, spy_credit_strategy.ET)
    return start <= now_et <= end


def scan_orb_signals(
    *,
    data: spy_credit_strategy.AlpacaMarketData,
    now_et: datetime,
    existing: list[db.StrategyEvent],
) -> list[OrbSignal]:
    existing_keys = {(event.symbol, event.event_type) for event in existing}
    signals: list[OrbSignal] = []
    day_start = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_OPEN, spy_credit_strategy.ET)
    range_end = datetime.combine(now_et.date(), ORB_RANGE_END, spy_credit_strategy.ET)
    for symbol in ORB_UNIVERSE:
        if symbol in NEVER_TRADE_SYMBOLS:
            continue
        bars = data.stock_bars(symbol=symbol, start=day_start, end=now_et, timeframe="1Min")
        opening = [
            bar for bar in bars
            if day_start <= datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(spy_credit_strategy.ET) < range_end
        ]
        if not opening:
            continue
        range_high = spy_credit_strategy.money(max(Decimal(str(bar["h"])) for bar in opening))
        range_low = spy_credit_strategy.money(min(Decimal(str(bar["l"])) for bar in opening))
        latest = spy_credit_strategy.latest_close(bars, now_et)
        if latest is None:
            continue
        if latest > range_high and (symbol, "breakout_up") not in existing_keys:
            signals.append(
                OrbSignal(
                    symbol=symbol,
                    side="breakout_up",
                    price=latest,
                    range_high=range_high,
                    range_low=range_low,
                    observed_at=now_et,
                )
            )
        elif latest < range_low and (symbol, "breakout_down") not in existing_keys:
            signals.append(
                OrbSignal(
                    symbol=symbol,
                    side="breakout_down",
                    price=latest,
                    range_high=range_high,
                    range_low=range_low,
                    observed_at=now_et,
                )
            )
    return signals


def orb_events_for_day(conn: sqlite3.Connection, now_et: datetime) -> list[db.StrategyEvent]:
    since = datetime.combine(now_et.date(), time(0, 0), spy_credit_strategy.ET).astimezone(timezone.utc)
    return db.list_strategy_events(
        conn,
        strategy="ORB",
        since=since.isoformat(),
        limit=500,
    )
