from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from . import broker, config, db, risk, spread_audit

ACTIVE_STATUSES = {db.STATUS_QUEUED, db.STATUS_SUBMITTED, db.STATUS_FILLED}


@dataclass(frozen=True)
class OpenSpread:
    request_id: int | None
    strategy: str | None
    status: str
    placed_at: str | None
    underlying: str
    expiration: str
    option_type: str
    short_strike: str
    long_strike: str
    qty: int
    entry_credit: str | None
    current_debit: str
    net_market_value: str
    estimated_pnl: str | None
    max_loss: str | None
    short_symbol: str
    long_symbol: str


@dataclass(frozen=True)
class SpreadSummary:
    checked_at: str
    mode: str
    open_spreads: list[OpenSpread]
    unpaired_legs: list[spread_audit.UnpairedLeg]
    local_drift: list[spread_audit.LocalDrift]
    unparsable_option_symbols: list[str]
    estimated_total_pnl: str
    spread_max_loss_usd: str
    option_open_risk_usd: float
    position_slots: int

    @property
    def unmatched_broker_spread_count(self) -> int:
        return sum(1 for item in self.open_spreads if item.request_id is None)


def run(conn) -> SpreadSummary:
    cfg = config.load_alpaca_config()
    b = broker.AlpacaBroker(cfg)
    try:
        positions = b.get_positions()
    finally:
        b.close()
    return build(conn, positions=positions, mode=cfg.mode)


def build(conn, *, positions: list[dict[str, Any]], mode: str) -> SpreadSummary:
    pairs, unpaired, unparsable = spread_audit.pair_broker_positions(positions)
    broker_symbols = {
        str(position.get("symbol"))
        for position in positions
        if position.get("asset_class") == "us_option" and position.get("symbol")
    }
    position_market_values = {
        str(position.get("symbol")): risk.decimal_value(position.get("market_value"))
        for position in positions
        if position.get("asset_class") == "us_option" and position.get("symbol")
    }
    local_spreads = db.list_strategy_spread_requests(conn, limit=2000)
    closed_open_ids = db.filled_close_open_ids(conn)
    open_spreads = [
        _open_spread(pair, local_spreads, position_market_values, closed_open_ids)
        for pair in pairs
    ]
    total_pnl = sum(
        Decimal(item.estimated_pnl)
        for item in open_spreads
        if item.estimated_pnl is not None
    )
    max_loss = sum(
        Decimal(item.max_loss)
        for item in open_spreads
        if item.max_loss is not None
    )
    return SpreadSummary(
        checked_at=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        open_spreads=open_spreads,
        unpaired_legs=unpaired,
        local_drift=spread_audit.find_local_drift(conn, broker_symbols),
        unparsable_option_symbols=unparsable,
        estimated_total_pnl=str(total_pnl),
        spread_max_loss_usd=str(max_loss),
        option_open_risk_usd=risk.estimate_open_risk_usd(positions),
        position_slots=risk.estimate_position_slots(positions),
    )


def _open_spread(
    pair: spread_audit.Pair,
    local_spreads: list[db.TradeRequest],
    position_market_values: dict[str, Decimal],
    closed_open_ids: set[int],
) -> OpenSpread:
    local = _match_local(pair, local_spreads, closed_open_ids)
    entry_credit = _decimal_payload(local, "limit_credit") if local else None
    qty = Decimal(pair.qty)
    net_market_value = (
        position_market_values.get(pair.short_symbol, Decimal("0"))
        + position_market_values.get(pair.long_symbol, Decimal("0"))
    )
    entry_total = entry_credit * Decimal("100") * qty if entry_credit is not None else None
    estimated_pnl = entry_total + net_market_value if entry_total is not None else None
    width = Decimal(pair.width)
    max_loss = width * Decimal("100") * qty - entry_total if entry_total is not None else None
    current_debit = abs(net_market_value) / Decimal("100") / qty if qty else Decimal("0")
    return OpenSpread(
        request_id=local.id if local else None,
        strategy=str(local.payload.get("strategy")) if local and local.payload.get("strategy") else None,
        status=local.status if local else "broker_only",
        placed_at=local.created_at if local else None,
        underlying=pair.underlying,
        expiration=pair.expiration,
        option_type=pair.option_type,
        short_strike=pair.short_strike,
        long_strike=pair.long_strike,
        qty=pair.qty,
        entry_credit=str(entry_credit) if entry_credit is not None else None,
        current_debit=str(current_debit),
        net_market_value=str(net_market_value),
        estimated_pnl=str(estimated_pnl) if estimated_pnl is not None else None,
        max_loss=str(max_loss) if max_loss is not None else None,
        short_symbol=pair.short_symbol,
        long_symbol=pair.long_symbol,
    )


def _match_local(
    pair: spread_audit.Pair,
    requests: list[db.TradeRequest],
    closed_open_ids: set[int],
) -> db.TradeRequest | None:
    pair_symbols = {pair.short_symbol, pair.long_symbol}
    for req in requests:
        if req.status not in ACTIVE_STATUSES:
            continue
        if req.id in closed_open_ids:
            continue
        payload = req.payload
        if (
            pair_symbols == {payload.get("short_symbol"), payload.get("long_symbol")}
            and int(req.qty) >= pair.qty
        ):
            return req
    return None


def _decimal_payload(req: db.TradeRequest, key: str) -> Decimal | None:
    value = req.payload.get(key)
    if value is None:
        return None
    return Decimal(str(value))


def to_dict(summary: SpreadSummary) -> dict[str, Any]:
    return {
        "checked_at": summary.checked_at,
        "mode": summary.mode,
        "open_spreads": [asdict(item) for item in summary.open_spreads],
        "unpaired_legs": [asdict(item) for item in summary.unpaired_legs],
        "local_drift": [asdict(item) for item in summary.local_drift],
        "unparsable_option_symbols": summary.unparsable_option_symbols,
        "estimated_total_pnl": summary.estimated_total_pnl,
        "spread_max_loss_usd": summary.spread_max_loss_usd,
        "option_open_risk_usd": summary.option_open_risk_usd,
        "position_slots": summary.position_slots,
        "unmatched_broker_spread_count": summary.unmatched_broker_spread_count,
    }
