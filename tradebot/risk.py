from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import config, state

ET = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
OCC_SYMBOL_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


@dataclass
class RiskCheck:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class OptionPosition:
    symbol: str
    underlying: str
    expiration: str
    option_type: str
    strike: Decimal
    qty: int
    market_value: Decimal


def check_pretrade(
    *,
    s: state.State,
    is_live: bool,
    expected_notional_usd: float,
    open_position_count: int,
    open_risk_usd: float,
    now_et: datetime | None = None,
) -> RiskCheck:
    if s.halted:
        return RiskCheck(False, f"halted: {s.halt_reason or 'no reason'}")

    if expected_notional_usd > config.MAX_RISK_PER_TRADE_USD:
        return RiskCheck(
            False,
            f"trade notional ${expected_notional_usd:.2f} > per-trade cap ${config.MAX_RISK_PER_TRADE_USD}",
        )

    if open_risk_usd + expected_notional_usd > config.MAX_TOTAL_OPEN_RISK_USD:
        return RiskCheck(
            False,
            f"open risk would be ${open_risk_usd + expected_notional_usd:.2f}, cap ${config.MAX_TOTAL_OPEN_RISK_USD}",
        )

    if open_position_count >= config.MAX_CONCURRENT_POSITIONS:
        return RiskCheck(
            False,
            f"position count {open_position_count} >= cap {config.MAX_CONCURRENT_POSITIONS}",
        )

    now_et = now_et or datetime.now(ET)
    close_dt = datetime.combine(now_et.date(), MARKET_CLOSE).replace(tzinfo=ET)
    minutes_to_close = (close_dt - now_et).total_seconds() / 60
    if 0 < minutes_to_close < config.NO_NEW_TRADES_BEFORE_CLOSE_MIN:
        return RiskCheck(
            False,
            f"{minutes_to_close:.1f} min to close, < {config.NO_NEW_TRADES_BEFORE_CLOSE_MIN} min cutoff",
        )

    return RiskCheck(True)


def estimate_open_risk_usd(positions: list[dict]) -> float:
    option_positions: list[OptionPosition] = []
    total = Decimal("0")
    for position in positions:
        asset_class = position.get("asset_class")
        if asset_class == "us_option":
            parsed = parse_option_position(position)
            if parsed is None:
                total += abs(decimal_value(position.get("market_value")))
            else:
                option_positions.append(parsed)
            continue
        total += abs(decimal_value(position.get("market_value")))

    paired_symbols: set[str] = set()
    for short in sorted(
        [p for p in option_positions if p.qty < 0],
        key=lambda p: (p.underlying, p.expiration, p.option_type, p.strike),
    ):
        if short.symbol in paired_symbols:
            continue
        long = find_vertical_long(short, option_positions, paired_symbols)
        if long is None:
            total += abs(short.market_value)
            paired_symbols.add(short.symbol)
            continue
        width = abs(long.strike - short.strike)
        qty = min(abs(short.qty), abs(long.qty))
        total += width * Decimal("100") * Decimal(qty)
        paired_symbols.add(short.symbol)
        paired_symbols.add(long.symbol)

    for option in option_positions:
        if option.symbol not in paired_symbols:
            total += abs(option.market_value)

    return float(total)


def find_vertical_long(
    short: OptionPosition,
    options: list[OptionPosition],
    paired_symbols: set[str],
) -> OptionPosition | None:
    candidates = [
        option
        for option in options
        if option.symbol not in paired_symbols
        and option.qty > 0
        and option.underlying == short.underlying
        and option.expiration == short.expiration
        and option.option_type == short.option_type
    ]
    if short.option_type == "C":
        candidates = [option for option in candidates if option.strike > short.strike]
    else:
        candidates = [option for option in candidates if option.strike < short.strike]
    if not candidates:
        return None
    return min(candidates, key=lambda option: abs(option.strike - short.strike))


def parse_option_position(position: dict) -> OptionPosition | None:
    symbol = str(position.get("symbol") or "")
    match = OCC_SYMBOL_RE.match(symbol)
    if not match:
        return None
    underlying, expiration, option_type, strike_raw = match.groups()
    try:
        qty = int(Decimal(str(position.get("qty", "0"))))
    except Exception:
        return None
    return OptionPosition(
        symbol=symbol,
        underlying=underlying,
        expiration=expiration,
        option_type=option_type,
        strike=Decimal(strike_raw) / Decimal("1000"),
        qty=qty,
        market_value=decimal_value(position.get("market_value")),
    )


def decimal_value(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))
