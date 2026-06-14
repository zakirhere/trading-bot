from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import broker, config, db, notify, risk

ACTIVE_STATUSES = {db.STATUS_QUEUED, db.STATUS_SUBMITTED, db.STATUS_FILLED}
AUDIT_STATE_FILE = config.STATE_DIR / "spread-audit-state.json"


@dataclass(frozen=True)
class Pair:
    short_symbol: str
    long_symbol: str
    underlying: str
    expiration: str
    option_type: str
    short_strike: str
    long_strike: str
    width: str
    qty: int


@dataclass(frozen=True)
class UnpairedLeg:
    symbol: str
    underlying: str
    expiration: str
    option_type: str
    strike: str
    qty: int
    side: str
    market_value: str


@dataclass(frozen=True)
class LocalDrift:
    request_id: int
    strategy: str | None
    status: str
    short_symbol: str | None
    long_symbol: str | None
    missing_broker_symbols: list[str]


@dataclass(frozen=True)
class SpreadAudit:
    checked_at: str
    mode: str
    broker_position_count: int
    broker_option_symbol_count: int
    paired_spreads: list[Pair]
    unpaired_legs: list[UnpairedLeg]
    local_drift: list[LocalDrift]
    unparsable_option_symbols: list[str]
    option_open_risk_usd: float
    position_slots: int

    @property
    def ok(self) -> bool:
        return (
            not self.unpaired_legs
            and not self.local_drift
            and not self.unparsable_option_symbols
        )

    @property
    def unpaired_short_count(self) -> int:
        return sum(1 for item in self.unpaired_legs if item.side == "short")


def run() -> SpreadAudit:
    cfg = config.load_alpaca_config()
    b = broker.AlpacaBroker(cfg)
    try:
        positions = b.get_positions()
    finally:
        b.close()

    paired_spreads, unpaired_legs, unparsable = pair_broker_positions(positions)
    broker_symbols = {
        str(position.get("symbol"))
        for position in positions
        if position.get("asset_class") == "us_option" and position.get("symbol")
    }

    conn = db.connect()
    db.init(conn)
    try:
        local_drift = find_local_drift(conn, broker_symbols)
    finally:
        conn.close()

    return SpreadAudit(
        checked_at=datetime.now(timezone.utc).isoformat(),
        mode=cfg.mode,
        broker_position_count=len(positions),
        broker_option_symbol_count=len(broker_symbols),
        paired_spreads=paired_spreads,
        unpaired_legs=unpaired_legs,
        local_drift=local_drift,
        unparsable_option_symbols=unparsable,
        option_open_risk_usd=risk.estimate_open_risk_usd(positions),
        position_slots=risk.estimate_position_slots(positions),
    )


def pair_broker_positions(
    positions: list[dict[str, Any]],
) -> tuple[list[Pair], list[UnpairedLeg], list[str]]:
    option_positions = []
    unparsable: list[str] = []
    for position in positions:
        if position.get("asset_class") != "us_option":
            continue
        parsed = risk.parse_option_position(position)
        if parsed is None:
            unparsable.append(str(position.get("symbol") or ""))
            continue
        if parsed.qty != 0:
            option_positions.append(parsed)

    paired_symbols: set[str] = set()
    pairs: list[Pair] = []
    unpaired: list[UnpairedLeg] = []

    for short in sorted(
        [p for p in option_positions if p.qty < 0],
        key=lambda p: (p.underlying, p.expiration, p.option_type, p.strike),
    ):
        if short.symbol in paired_symbols:
            continue
        long = risk.find_vertical_long(short, option_positions, paired_symbols)
        if long is None:
            unpaired.append(unpaired_leg(short, "short"))
            paired_symbols.add(short.symbol)
            continue
        width = abs(long.strike - short.strike)
        qty = min(abs(short.qty), abs(long.qty))
        pairs.append(
            Pair(
                short_symbol=short.symbol,
                long_symbol=long.symbol,
                underlying=short.underlying,
                expiration=short.expiration,
                option_type=short.option_type,
                short_strike=str(short.strike),
                long_strike=str(long.strike),
                width=str(width),
                qty=qty,
            )
        )
        paired_symbols.add(short.symbol)
        paired_symbols.add(long.symbol)

    for option in option_positions:
        if option.symbol not in paired_symbols:
            unpaired.append(unpaired_leg(option, "long" if option.qty > 0 else "short"))

    return pairs, unpaired, unparsable


def unpaired_leg(option: risk.OptionPosition, side: str) -> UnpairedLeg:
    return UnpairedLeg(
        symbol=option.symbol,
        underlying=option.underlying,
        expiration=option.expiration,
        option_type=option.option_type,
        strike=str(option.strike),
        qty=option.qty,
        side=side,
        market_value=str(option.market_value),
    )


def find_local_drift(conn, broker_symbols: set[str]) -> list[LocalDrift]:
    drift: list[LocalDrift] = []
    closed_open_ids = _filled_close_open_ids(conn)
    for req in db.list_strategy_spread_requests(conn, limit=1000):
        if req.status not in ACTIVE_STATUSES:
            continue
        if req.id in closed_open_ids:
            continue
        payload = req.payload
        short_symbol = payload.get("short_symbol")
        long_symbol = payload.get("long_symbol")
        missing = [
            symbol
            for symbol in [short_symbol, long_symbol]
            if symbol and symbol not in broker_symbols
        ]
        if missing:
            drift.append(
                LocalDrift(
                    request_id=req.id,
                    strategy=payload.get("strategy"),
                    status=req.status,
                    short_symbol=short_symbol,
                    long_symbol=long_symbol,
                    missing_broker_symbols=missing,
                )
            )
    return drift


def _filled_close_open_ids(conn) -> set[int]:
    closes = db.list_strategy_close_requests(conn, limit=1000)
    return {
        int(req.payload["open_request_id"])
        for req in closes
        if req.status == db.STATUS_FILLED
        and req.payload.get("open_request_id") is not None
    }


def fingerprint(audit: SpreadAudit) -> str:
    payload = {
        "unpaired_legs": sorted(
            (asdict(item) for item in audit.unpaired_legs),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        "local_drift": sorted(
            (asdict(item) for item in audit.local_drift),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        "unparsable_option_symbols": sorted(audit.unparsable_option_symbols),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def notify_if_changed(audit: SpreadAudit, *, state_file: Path = AUDIT_STATE_FILE) -> bool:
    current = fingerprint(audit)
    previous = read_previous_fingerprint(state_file)
    if current == previous:
        return False

    if audit.ok:
        sent = notify.send(
            notify.Alert(
                level="info",
                title="Option spread audit recovered",
                message="All broker option legs are paired and active local spreads are present.",
                fields=summary_fields(audit),
            )
        )
        if sent:
            write_fingerprint(state_file, current, audit.checked_at)
        return sent

    level = "critical" if audit.unpaired_short_count or audit.local_drift else "warning"
    sent = notify.send(
        notify.Alert(
            level=level,
            title="Option spread audit found drift",
            message=human_summary(audit),
            fields=summary_fields(audit),
        )
    )
    if sent:
        write_fingerprint(state_file, current, audit.checked_at)
    return sent


def read_previous_fingerprint(state_file: Path) -> str | None:
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text())
    except Exception:
        return None
    value = data.get("fingerprint")
    return str(value) if value else None


def write_fingerprint(state_file: Path, value: str, checked_at: str) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"fingerprint": value, "checked_at": checked_at}) + "\n")
    state_file.chmod(0o600)


def summary_fields(audit: SpreadAudit) -> dict[str, Any]:
    return {
        "paired_spreads": len(audit.paired_spreads),
        "unpaired_legs": len(audit.unpaired_legs),
        "unpaired_shorts": audit.unpaired_short_count,
        "local_drift": len(audit.local_drift),
        "option_open_risk": f"${audit.option_open_risk_usd:.2f}",
        "position_slots": audit.position_slots,
    }


def human_summary(audit: SpreadAudit) -> str:
    lines: list[str] = []
    for item in audit.unpaired_legs[:5]:
        level = "CRITICAL" if item.side == "short" else "WARNING"
        lines.append(f"{level} unpaired {item.side}: {item.symbol} qty={item.qty}")
    for item in audit.local_drift[:5]:
        missing = ", ".join(item.missing_broker_symbols)
        lines.append(
            f"CRITICAL local drift: request {item.request_id} "
            f"{item.strategy} {item.status} missing {missing}"
        )
    for symbol in audit.unparsable_option_symbols[:5]:
        lines.append(f"WARNING unparsable option symbol: {symbol}")
    if not lines:
        return "No option spread pairing drift found."
    remaining = (
        len(audit.unpaired_legs)
        + len(audit.local_drift)
        + len(audit.unparsable_option_symbols)
        - len(lines)
    )
    if remaining > 0:
        lines.append(f"...and {remaining} more finding(s).")
    return "\n".join(lines)


def to_dict(audit: SpreadAudit) -> dict[str, Any]:
    return {
        "checked_at": audit.checked_at,
        "mode": audit.mode,
        "broker_position_count": audit.broker_position_count,
        "broker_option_symbol_count": audit.broker_option_symbol_count,
        "paired_spreads": [asdict(item) for item in audit.paired_spreads],
        "unpaired_legs": [asdict(item) for item in audit.unpaired_legs],
        "local_drift": [asdict(item) for item in audit.local_drift],
        "unparsable_option_symbols": audit.unparsable_option_symbols,
        "option_open_risk_usd": audit.option_open_risk_usd,
        "position_slots": audit.position_slots,
        "ok": audit.ok,
    }
