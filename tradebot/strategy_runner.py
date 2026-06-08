from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime
from decimal import Decimal

from . import config, db, spy_credit_strategy


def run_icl_scheduler_once(conn: sqlite3.Connection, *, now_et: datetime | None = None) -> db.TradeRequest | None:
    strategy_cfg = config.load_strategy_config()
    if not strategy_cfg.icl_paper_autorun:
        return None

    alpaca_cfg = config.load_alpaca_config()
    if alpaca_cfg.is_live:
        raise RuntimeError("ICL autorun is paper-only")

    now_et = now_et or datetime.now(spy_credit_strategy.ET)
    if not within_market_hours(now_et):
        return None

    existing = icl_requests_for_day(conn, now_et)
    if len(existing) >= spy_credit_strategy.ICL_TARGET_ACTIVE_TRADES:
        return None
    if len(existing) >= spy_credit_strategy.ICL_MAX_DAILY_ENTRIES:
        return None

    due_slot = next_due_slot(now_et=now_et, existing=existing)
    if due_slot is None:
        return None

    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        candidate = select_icl_candidate(data=data, now_et=now_et, existing=existing)
    finally:
        data.close()
    if candidate is None:
        return None

    payload = {
        **spy_credit_strategy.candidate_payload(candidate),
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


def select_icl_candidate(
    *,
    data: spy_credit_strategy.AlpacaMarketData,
    now_et: datetime,
    existing: list[db.TradeRequest],
) -> spy_credit_strategy.SpreadCandidate | None:
    day_start = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_OPEN, spy_credit_strategy.ET)
    day_end = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_CLOSE, spy_credit_strategy.ET)
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
    return spy_credit_strategy.find_best_income_candidate(
        data=data,
        direction=direction,
        expiries=expiries,
        entry_time=now_et,
        close_time=day_end,
        rng=rng,
        target_min=Decimal("0.58"),
        target_max=Decimal("0.62"),
        reject_at_or_above=Decimal("0.65"),
        spread_width=Decimal("1"),
        blocked_spreads=blocked,
    )


def icl_requests_for_day(conn: sqlite3.Connection, now_et: datetime) -> list[db.TradeRequest]:
    rows = db.list_requests(conn, limit=500)
    out: list[db.TradeRequest] = []
    for req in rows:
        if req.kind != "option_spread_open":
            continue
        if req.payload.get("strategy") != "ICL":
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


def next_due_slot(*, now_et: datetime, existing: list[db.TradeRequest]) -> datetime | None:
    rng = random.Random(int(now_et.strftime("%Y%m%d")))
    slots = spy_credit_strategy.random_entry_times(
        now_et.date(),
        spy_credit_strategy.ICL_TARGET_ACTIVE_TRADES,
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
