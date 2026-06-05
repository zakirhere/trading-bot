from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from . import config, state

ET = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


@dataclass
class RiskCheck:
    allowed: bool
    reason: str | None = None


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
