from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal

from . import broker, config, db, journal, notify, risk, spy_credit_strategy, state, strategy_runner

log = logging.getLogger(__name__)

_BROKER_WORKING_STATUSES = {
    "accepted",
    "pending_new",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_replace",
}
_BROKER_ERROR_STATUSES = {"canceled", "expired", "rejected", "failed", "done_for_day"}


def _client_order_id(req: db.TradeRequest) -> str:
    return f"queue_{req.id}_{req.kind}_{req.symbol}"


def mleg_net_limit_price(req: db.TradeRequest) -> float:
    # Alpaca MLeg net prices are signed: positive is debit, negative is credit.
    if req.order_type == "limit_credit":
        return -abs(float(req.payload["limit_credit"]))
    if req.order_type == "limit_debit":
        return abs(float(req.payload["limit_debit"]))
    return float(req.payload["limit_price"])


def _order_result_from_broker_order(order: dict) -> broker.OrderResult:
    return broker.OrderResult(
        broker_order_id=order["id"],
        status=order["status"],
        symbol=order.get("symbol") or "MLEG",
        side=order.get("side") or "mleg",
        qty=float(order["qty"]),
        raw=order,
    )


def _is_duplicate_client_order_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "client_order_id" in text and "must be unique" in text


def requote_credit_for_request(
    alpaca_cfg: config.AlpacaConfig,
    req: db.TradeRequest,
) -> Decimal | None:
    legs = req.payload.get("legs", [])
    sell_leg = next((leg for leg in legs if leg.get("side") == "sell"), None)
    buy_leg = next((leg for leg in legs if leg.get("side") == "buy"), None)
    if not sell_leg or not buy_leg:
        return None

    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        quotes = data.option_latest_quotes(symbols=[sell_leg["symbol"], buy_leg["symbol"]])
    finally:
        data.close()
    sell_quote = quotes.get(sell_leg["symbol"])
    buy_quote = quotes.get(buy_leg["symbol"])
    if not sell_quote or not buy_quote:
        return None
    return spy_credit_strategy.spread_credit_from_quotes(
        short_quote=sell_quote,
        long_quote=buy_quote,
        quote_basis=req.payload.get("quote_basis", spy_credit_strategy.QUOTE_BASIS_CONSERVATIVE),
    )


def credit_band_check(req: db.TradeRequest, credit: Decimal) -> risk.RiskCheck:
    if req.order_type != "limit_credit":
        return risk.RiskCheck(True)
    payload = req.payload
    if not {"target_min", "target_max", "reject_at_or_above"} <= set(payload):
        return risk.RiskCheck(True)
    target_min = Decimal(str(payload["target_min"]))
    target_max = Decimal(str(payload["target_max"]))
    reject_at_or_above = Decimal(str(payload["reject_at_or_above"]))
    if credit < target_min:
        return risk.RiskCheck(False, f"requote credit {credit} < target min {target_min}")
    if credit > target_max:
        return risk.RiskCheck(False, f"requote credit {credit} > target max {target_max}")
    if credit >= reject_at_or_above:
        return risk.RiskCheck(False, f"requote credit {credit} >= reject {reject_at_or_above}")
    return risk.RiskCheck(True)


def latest_underlying_price_for_request(
    alpaca_cfg: config.AlpacaConfig,
    req: db.TradeRequest,
) -> Decimal | None:
    now_et = datetime.now(spy_credit_strategy.ET)
    day_start = datetime.combine(now_et.date(), spy_credit_strategy.MARKET_OPEN, spy_credit_strategy.ET)
    data = spy_credit_strategy.AlpacaMarketData(alpaca_cfg)
    try:
        bars = data.stock_bars(symbol=req.symbol, start=day_start, end=now_et, timeframe="1Min")
    finally:
        data.close()
    if not bars:
        return None
    return spy_credit_strategy.latest_close(bars, now_et)


def option_spread_moneyness_check(req: db.TradeRequest, underlying_price: Decimal) -> risk.RiskCheck:
    if req.kind != "option_spread_open":
        return risk.RiskCheck(True)
    payload = req.payload
    if not {"direction", "short_strike"} <= set(payload):
        return risk.RiskCheck(False, "missing option spread moneyness fields")
    direction = str(payload["direction"])
    short_strike = Decimal(str(payload["short_strike"]))
    if not spy_credit_strategy.spread_is_otm(
        direction=direction,
        short_strike=short_strike,
        underlying_price=underlying_price,
    ):
        return risk.RiskCheck(
            False,
            f"{direction} short strike {short_strike} is not OTM vs {req.symbol} {underlying_price}",
        )
    return risk.RiskCheck(True)


def duplicate_open_option_leg_check(req: db.TradeRequest, positions: list[dict]) -> risk.RiskCheck:
    if req.kind != "option_spread_open":
        return risk.RiskCheck(True)
    open_symbols = {
        str(position.get("symbol"))
        for position in positions
        if position.get("asset_class") == "us_option"
        and abs(float(position.get("qty") or 0)) > 0
        and position.get("symbol")
    }
    duplicate_symbols = sorted(
        {
            str(leg.get("symbol"))
            for leg in req.payload.get("legs", [])
            if leg.get("symbol") in open_symbols
        }
    )
    if duplicate_symbols:
        return risk.RiskCheck(
            False,
            f"spread leg already open: {', '.join(duplicate_symbols)}",
        )
    return risk.RiskCheck(True)


def execute_request(
    conn: sqlite3.Connection,
    req: db.TradeRequest,
    *,
    force_closed: bool = False,
) -> db.TradeRequest:
    if req.kind not in {"stock_market_buy", "option_spread_open", "option_spread_close"}:
        return db.update_status(
            conn,
            req.id,
            status=db.STATUS_ERROR,
            reason=f"unsupported request kind {req.kind!r}",
        )

    cfg = config.load_alpaca_config()
    b = broker.AlpacaBroker(cfg)
    try:
        acct = b.get_account()
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="Alpaca account is blocked",
            )

        clock = b.get_clock()
        if not clock.get("is_open") and not req.dry_run and not force_closed:
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="market is closed",
            )

        if cfg.is_live and req.kind in {"option_spread_open", "option_spread_close"}:
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="option spread automation is paper-only",
            )

        if (
            req.kind in {"option_spread_open", "option_spread_close"}
            and req.symbol in db.NEVER_TRADE_OPTION_UNDERLYINGS
        ):
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason=f"{req.symbol} options are blocked",
            )

        current_state = state.load()
        positions = b.get_positions()
        if req.kind == "option_spread_open":
            duplicate_check = duplicate_open_option_leg_check(req, positions)
            if not duplicate_check.allowed:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=duplicate_check.reason,
                )
        expected_notional = expected_risk_usd(req)
        open_risk = risk.estimate_open_risk_usd(positions)
        if req.kind == "option_spread_close":
            if current_state.halted:
                reason = f"halted: {current_state.halt_reason or 'no reason'}"
                notify.send(
                    notify.Alert(
                        level="risk",
                        title="Queued close blocked",
                        message=reason,
                        fields={"request_id": req.id, "symbol": req.symbol},
                    )
                )
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=reason,
                )
        else:
            rc = risk.check_pretrade(
                s=current_state,
                is_live=cfg.is_live,
                expected_notional_usd=expected_notional,
                open_position_count=risk.estimate_position_slots(positions),
                open_risk_usd=open_risk,
            )
            if not rc.allowed:
                notify.send(
                    notify.Alert(
                        level="risk",
                        title="Queued trade blocked",
                        message=rc.reason or "risk gate blocked",
                        fields={"request_id": req.id, "symbol": req.symbol, "qty": req.qty},
                    )
                )
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=rc.reason,
                )

        client_order_id = _client_order_id(req)
        if req.dry_run:
            notify.send(
                notify.Alert(
                    level="info",
                    title="Queued dry-run trade",
                    message=f"Would submit market buy {req.qty:g} {req.symbol}.",
                    fields={"request_id": req.id, "client_order_id": client_order_id},
                )
            )
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_DRY_RUN,
                client_order_id=client_order_id,
                reason="dry-run",
            )

        if req.kind == "stock_market_buy":
            try:
                result = b.submit_market_order(
                    symbol=req.symbol,
                    qty=req.qty,
                    side=req.side,
                    client_order_id=client_order_id,
                )
            except Exception as exc:
                if not _is_duplicate_client_order_error(exc):
                    raise
                result = _order_result_from_broker_order(
                    b.get_order_by_client_order_id(client_order_id)
                )
            message = f"Submitted market buy {req.qty:g} {req.symbol}."
        elif req.kind == "option_spread_close":
            strategy_name = req.payload.get("strategy", "SPREAD")
            try:
                result = b.submit_mleg_limit_order(
                    qty=int(req.qty),
                    limit_price=mleg_net_limit_price(req),
                    legs=req.payload["legs"],
                    client_order_id=client_order_id,
                )
            except Exception as exc:
                if not _is_duplicate_client_order_error(exc):
                    raise
                result = _order_result_from_broker_order(
                    b.get_order_by_client_order_id(client_order_id)
                )
            message = (
                f"Submitted {strategy_name} close "
                f"{req.payload.get('short_symbol')}/{req.payload.get('long_symbol')} "
                f"for ${float(req.payload['limit_debit']):.2f} debit."
            )
        else:
            underlying_price = latest_underlying_price_for_request(cfg, req)
            if underlying_price is None:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason="missing underlying price for moneyness check",
                )
            moneyness_check = option_spread_moneyness_check(req, underlying_price)
            if not moneyness_check.allowed:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=moneyness_check.reason,
                )
            requote_credit = requote_credit_for_request(cfg, req)
            if requote_credit is None:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason="missing option quote for pre-submit credit check",
                )
            band_check = credit_band_check(req, requote_credit)
            if not band_check.allowed:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=band_check.reason,
                )
            strategy_name = req.payload.get("strategy", "SPREAD")
            try:
                result = b.submit_mleg_limit_order(
                    qty=int(req.qty),
                    limit_price=mleg_net_limit_price(req),
                    legs=req.payload["legs"],
                    client_order_id=client_order_id,
                )
            except Exception as exc:
                if not _is_duplicate_client_order_error(exc):
                    raise
                result = _order_result_from_broker_order(
                    b.get_order_by_client_order_id(client_order_id)
                )
            message = (
                f"Submitted {strategy_name} {req.payload['direction']} "
                f"{req.payload['short_symbol']}/{req.payload['long_symbol']} "
                f"for ${float(req.payload['limit_credit']):.2f} credit."
            )
        with state.transaction() as st:
            st.processed_keys.append(client_order_id)
            st.orders.append(
                state.OrderRecord(
                    idempotency_key=client_order_id,
                    broker_order_id=result.broker_order_id,
                    symbol=req.symbol,
                    side=req.side,
                    qty=req.qty,
                    submitted_at=datetime.now(timezone.utc).isoformat(),
                    status=result.status,
                )
            )

        notify.send(
            notify.Alert(
                level="trade",
                title="Queued order submitted",
                message=message,
                fields={
                    "request_id": req.id,
                    "status": result.status,
                    "broker_order_id": result.broker_order_id,
                    "client_order_id": client_order_id,
                },
            )
        )
        return db.update_status(
            conn,
            req.id,
            status=db.STATUS_SUBMITTED,
            broker_order_id=result.broker_order_id,
            client_order_id=client_order_id,
            reason=result.status,
        )
    except Exception as exc:
        log.exception("trade request %s failed", req.id)
        notify.send(
            notify.Alert(
                level="error",
                title="Queued trade failed",
                message=str(exc),
                fields={"request_id": req.id, "symbol": req.symbol},
            )
        )
        return db.update_status(
            conn,
            req.id,
            status=db.STATUS_ERROR,
            reason=str(exc),
        )
    finally:
        b.close()


def expected_risk_usd(req: db.TradeRequest) -> float:
    if req.kind == "option_spread_open":
        return float(req.payload.get("max_risk", config.MAX_RISK_PER_TRADE_USD))
    if req.kind == "option_spread_close":
        return 0.0
    return req.qty * config.MAX_RISK_PER_TRADE_USD


def _update_state_order_status(broker_order_id: str, status: str) -> None:
    with state.transaction() as st:
        for order in st.orders:
            if order.broker_order_id == broker_order_id:
                order.status = status


def reconcile_submitted_orders(conn: sqlite3.Connection) -> list[db.TradeRequest]:
    cfg = config.load_alpaca_config()
    b = broker.AlpacaBroker(cfg)
    changed: list[db.TradeRequest] = []
    try:
        for req in db.list_requests_by_status(conn, status=db.STATUS_SUBMITTED, limit=100):
            if not req.broker_order_id:
                continue
            order = b.get_order(req.broker_order_id)
            broker_status = str(order.get("status") or "")
            if not broker_status or broker_status == req.reason:
                continue
            if broker_status == "filled":
                updated = db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_FILLED,
                    reason="filled",
                )
                _update_state_order_status(req.broker_order_id, "filled")
            elif broker_status in _BROKER_ERROR_STATUSES:
                updated = db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_ERROR,
                    reason=broker_status,
                )
                _update_state_order_status(req.broker_order_id, broker_status)
            elif broker_status in _BROKER_WORKING_STATUSES:
                updated = db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_SUBMITTED,
                    reason=broker_status,
                )
                _update_state_order_status(req.broker_order_id, broker_status)
            else:
                updated = db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_SUBMITTED,
                    reason=broker_status,
                )
                _update_state_order_status(req.broker_order_id, broker_status)
            changed.append(updated)
    finally:
        b.close()
    return changed


def run_once(conn: sqlite3.Connection, *, force_closed: bool = False) -> list[db.TradeRequest]:
    db.init(conn)
    results = []
    for req in db.due_requests(conn):
        log.info("executing queued request id=%s kind=%s symbol=%s", req.id, req.kind, req.symbol)
        results.append(execute_request(conn, req, force_closed=force_closed))
    return results


def run_forever(
    *,
    poll_seconds: int = config.SERVICE_POLL_SECONDS,
    force_closed: bool = False,
    stop_event=None,
) -> None:
    conn = db.connect()
    db.init(conn)
    log.info("worker loop started poll_seconds=%s db=%s", poll_seconds, config.DB_FILE)
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                strategy_runner.run_icl_scheduler_once(conn)
            except Exception:
                log.exception("ICL scheduler failed")
            try:
                strategy_runner.run_icl_exit_scheduler_once(conn)
            except Exception:
                log.exception("ICL exit scheduler failed")
            try:
                strategy_runner.run_dca_scheduler_once(conn)
            except Exception:
                log.exception("DCA scheduler failed")
            try:
                strategy_runner.run_orb_observer_once(conn)
            except Exception:
                log.exception("ORB observer failed")
            run_once(conn, force_closed=force_closed)
            try:
                reconcile_submitted_orders(conn)
            except Exception:
                log.exception("order status reconciliation failed")
            try:
                journal.sync_from_db(conn)
            except Exception:
                log.exception("trade journal sync failed")
            time.sleep(poll_seconds)
    finally:
        conn.close()
