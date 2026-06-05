from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from . import broker, idempotency, notify, risk, state

log = logging.getLogger(__name__)

STRATEGY = "plumbing_test"
NIO_QTY = 10
# Conservative upper bound on NIO price for risk-cap check (true price typically $4-10).
NIO_PRICE_UPPER_BOUND = 30.0
DEFAULT_PRICE_UPPER_BOUND = 500.0


@dataclass
class SignalResult:
    fired: bool
    reason: str
    order: broker.OrderResult | None = None


def stock_buy(
    b: broker.AlpacaBroker,
    *,
    symbol: str,
    qty: float,
    dry_run: bool = False,
    price_upper_bound: float = DEFAULT_PRICE_UPPER_BOUND,
) -> SignalResult:
    symbol = symbol.upper()
    intent = {"action": "buy", "symbol": symbol, "qty": qty, "type": "market"}
    key = idempotency.make_key(STRATEGY, symbol, intent)

    s = state.load()
    if idempotency.is_processed(key, s.processed_keys):
        notify.send(
            notify.Alert(
                level="info",
                title="Signal skipped",
                message=f"{symbol} plumbing signal already processed for this date.",
                fields={"symbol": symbol, "idempotency_key": key},
            )
        )
        return SignalResult(False, f"idempotency: {key} already processed")

    positions = b.get_positions()
    expected_notional = qty * price_upper_bound
    open_risk = sum(abs(float(p.get("market_value", 0))) for p in positions)

    rc = risk.check_pretrade(
        s=s,
        is_live=b.cfg.is_live,
        expected_notional_usd=expected_notional,
        open_position_count=len(positions),
        open_risk_usd=open_risk,
    )
    if not rc.allowed:
        log.warning("risk gate blocked: %s", rc.reason)
        notify.send(
            notify.Alert(
                level="risk",
                title="Trade blocked",
                message=rc.reason or "risk gate blocked",
                fields={"symbol": symbol, "qty": qty, "dry_run": dry_run},
            )
        )
        return SignalResult(False, f"risk gate: {rc.reason}")

    if dry_run:
        log.info("DRY RUN — would submit market buy %s %s key=%s", qty, symbol, key)
        notify.send(
            notify.Alert(
                level="info",
                title="Dry-run signal",
                message=f"Would submit market buy {qty:g} {symbol}.",
                fields={"symbol": symbol, "qty": qty, "idempotency_key": key},
            )
        )
        return SignalResult(False, "dry-run")

    log.info("submitting market buy %s %s key=%s", qty, symbol, key)
    result = b.submit_market_order(
        symbol=symbol, qty=qty, side="buy", client_order_id=key
    )

    with state.transaction() as st:
        st.processed_keys.append(key)
        st.orders.append(
            state.OrderRecord(
                idempotency_key=key,
                broker_order_id=result.broker_order_id,
                symbol=symbol,
                side="buy",
                qty=qty,
                submitted_at=datetime.now(timezone.utc).isoformat(),
                status=result.status,
            )
        )

    log.info("submitted: id=%s status=%s", result.broker_order_id, result.status)
    notify.send(
            notify.Alert(
                level="trade",
                title="Order submitted",
                message=f"Submitted market buy {qty:g} {symbol}.",
                fields={
                    "symbol": symbol,
                    "qty": qty,
                    "status": result.status,
                    "broker_order_id": result.broker_order_id,
                    "idempotency_key": key,
            },
        )
    )
    return SignalResult(True, "submitted", order=result)


def nio_buy(b: broker.AlpacaBroker, *, dry_run: bool = False) -> SignalResult:
    return stock_buy(
        b,
        symbol="NIO",
        qty=NIO_QTY,
        dry_run=dry_run,
        price_upper_bound=NIO_PRICE_UPPER_BOUND,
    )
