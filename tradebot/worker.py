from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone

from . import broker, config, db, notify, risk, state, strategy_runner

log = logging.getLogger(__name__)


def _client_order_id(req: db.TradeRequest) -> str:
    return f"queue_{req.id}_{req.kind}_{req.symbol}"


def execute_request(
    conn: sqlite3.Connection,
    req: db.TradeRequest,
    *,
    force_closed: bool = False,
) -> db.TradeRequest:
    if req.kind not in {"stock_market_buy", "option_spread_open"}:
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

        if cfg.is_live and req.kind == "option_spread_open":
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="option spread automation is paper-only",
            )

        current_state = state.load()
        positions = b.get_positions()
        expected_notional = expected_risk_usd(req)
        open_risk = sum(abs(float(p.get("market_value", 0))) for p in positions)
        rc = risk.check_pretrade(
            s=current_state,
            is_live=cfg.is_live,
            expected_notional_usd=expected_notional,
            open_position_count=len(positions),
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
            result = b.submit_market_order(
                symbol=req.symbol,
                qty=req.qty,
                side=req.side,
                client_order_id=client_order_id,
            )
            message = f"Submitted market buy {req.qty:g} {req.symbol}."
        else:
            strategy_name = req.payload.get("strategy", "SPREAD")
            result = b.submit_mleg_limit_order(
                qty=int(req.qty),
                limit_price=float(req.payload["limit_credit"]),
                legs=req.payload["legs"],
                client_order_id=client_order_id,
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
    return req.qty * config.MAX_RISK_PER_TRADE_USD


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
                strategy_runner.run_dca_scheduler_once(conn)
            except Exception:
                log.exception("DCA scheduler failed")
            try:
                strategy_runner.run_orb_observer_once(conn)
            except Exception:
                log.exception("ORB observer failed")
            run_once(conn, force_closed=force_closed)
            time.sleep(poll_seconds)
    finally:
        conn.close()
