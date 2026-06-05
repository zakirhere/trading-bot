from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LOCAL_TZ = datetime.now().astimezone().tzinfo


@dataclass(frozen=True)
class ParsedTrade:
    symbol: str
    qty: float
    run_at: str | None
    dry_run: bool


class ParseError(ValueError):
    pass


def parse_stock_buy(text: str, *, now: datetime | None = None) -> ParsedTrade:
    now = now or datetime.now(tz=LOCAL_TZ)
    raw = " ".join(text.strip().split())
    lowered = raw.lower()
    dry_run = "dry run" in lowered or "dry-run" in lowered
    lowered = lowered.replace("dry-run", "dry run")

    unsupported = {"option", "options", "contract", "contracts", "call", "put", "spread", "credit", "debit", "sell", "short"}
    found = sorted(word for word in unsupported if re.search(rf"\b{word}\b", lowered))
    if found:
        raise ParseError(
            "Natural language currently supports stock market buys only; "
            f"unsupported term: {found[0]}"
        )

    match = re.search(
        r"\bbuy\s+(?:(?P<qty>\d+(?:\.\d+)?)\s+(?:shares?\s+(?:of\s+)?)?)?(?P<symbol>[A-Za-z]{1,5})\b",
        lowered,
    )
    if not match:
        raise ParseError("Use a stock buy like: buy 1 AAPL at noon today")

    qty = float(match.group("qty") or 1)
    if qty <= 0:
        raise ParseError("Quantity must be greater than zero")

    symbol = match.group("symbol").upper()
    run_at = _parse_time(lowered, now=now)
    return ParsedTrade(symbol=symbol, qty=qty, run_at=run_at, dry_run=dry_run)


def _parse_time(text: str, *, now: datetime) -> str | None:
    if any(token in text for token in (" now", " immediately", " asap")):
        return None

    day = now.date()
    if "tomorrow" in text:
        day = day + timedelta(days=1)

    parsed_time: time | None = None
    if "market open" in text:
        parsed_time = time(9, 30)
        tz = ET
    elif "noon" in text:
        parsed_time = time(12, 0)
        tz = LOCAL_TZ
    else:
        m = re.search(r"\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b", text)
        if not m:
            return None
        hour = int(m.group("hour"))
        minute = int(m.group("minute") or 0)
        ampm = m.group("ampm")
        if ampm is None and 1 <= hour <= 12:
            raise ParseError("Use AM or PM for scheduled times, for example: at 12:30 PM")
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            raise ParseError("Invalid time")
        parsed_time = time(hour, minute)
        tz = LOCAL_TZ

    dt = datetime.combine(day, parsed_time).replace(tzinfo=tz)
    if "today" not in text and "tomorrow" not in text and dt < now:
        dt = dt + timedelta(days=1)
    return dt.astimezone(timezone.utc).isoformat()
