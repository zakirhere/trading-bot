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


@dataclass
class SignalResult:
    fired: bool
    reason: str
    order: broker.OrderResult | None = None


def nio_buy(b: broker.AlpacaBroker, *, dry_run: bool = False) -> SignalResult:
    intent = {"action": "buy", "symbol": "NIO", "qty": NIO_QTY, "type": "market"}
    key = idempotency.make_key(STRATEGY, "NIO", intent)

    s = state.load()
    if idempotency.is_processed(key, s.processed_keys):
        notify.send(
            notify.Alert(
                level="info",
                title="Signal skipped",
                message="NIO plumbing signal already processed for this date.",
                fields={"symbol": "NIO", "idempotency_key": key},
            )
        )
        return SignalResult(False, f"idempotency: {key} already processed")

    positions = b.get_positions()
    expected_notional = NIO_QTY * NIO_PRICE_UPPER_BOUND
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
                fields={"symbol": "NIO", "qty": NIO_QTY, "dry_run": dry_run},
            )
        )
        return SignalResult(False, f"risk gate: {rc.reason}")

    if dry_run:
        log.info("DRY RUN — would submit market buy %d NIO key=%s", NIO_QTY, key)
        notify.send(
            notify.Alert(
                level="info",
                title="Dry-run signal",
                message=f"Would submit market buy {NIO_QTY} NIO.",
                fields={"symbol": "NIO", "qty": NIO_QTY, "idempotency_key": key},
            )
        )
        return SignalResult(False, "dry-run")

    log.info("submitting market buy %d NIO key=%s", NIO_QTY, key)
    result = b.submit_market_order(
        symbol="NIO", qty=NIO_QTY, side="buy", client_order_id=key
    )

    with state.transaction() as st:
        st.processed_keys.append(key)
        st.orders.append(
            state.OrderRecord(
                idempotency_key=key,
                broker_order_id=result.broker_order_id,
                symbol="NIO",
                side="buy",
                qty=NIO_QTY,
                submitted_at=datetime.now(timezone.utc).isoformat(),
                status=result.status,
            )
        )

    log.info("submitted: id=%s status=%s", result.broker_order_id, result.status)
    notify.send(
        notify.Alert(
            level="trade",
            title="Order submitted",
            message=f"Submitted market buy {NIO_QTY} NIO.",
            fields={
                "symbol": "NIO",
                "qty": NIO_QTY,
                "status": result.status,
                "broker_order_id": result.broker_order_id,
                "idempotency_key": key,
            },
        )
    )
    return SignalResult(True, "submitted", order=result)
