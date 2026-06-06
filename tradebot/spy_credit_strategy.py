from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import httpx

from . import broker, config

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    expiration_date: date
    strike_price: Decimal
    option_type: str


@dataclass(frozen=True)
class SpreadCandidate:
    direction: str
    expiration_date: date
    short_symbol: str
    long_symbol: str
    short_strike: Decimal
    long_strike: Decimal
    credit: Decimal
    close_credit: Decimal | None = None

    @property
    def width(self) -> Decimal:
        return abs(self.short_strike - self.long_strike)

    @property
    def mark_to_close_pnl(self) -> Decimal | None:
        if self.close_credit is None:
            return None
        return (self.credit - self.close_credit) * Decimal("100")

    @property
    def key(self) -> tuple[str, date, Decimal, Decimal]:
        return spread_key(
            direction=self.direction,
            expiration_date=self.expiration_date,
            short_strike=self.short_strike,
            long_strike=self.long_strike,
        )


@dataclass(frozen=True)
class BacktestEntry:
    entry_time: datetime
    spy_price: Decimal
    previous_close: Decimal
    direction: str
    expiration_date: date
    candidate: SpreadCandidate | None
    reason: str


@dataclass(frozen=True)
class BacktestResult:
    trading_date: date
    previous_close: Decimal
    entries: list[BacktestEntry]

    @property
    def trades_found(self) -> int:
        return sum(1 for e in self.entries if e.candidate is not None)

    @property
    def total_pnl(self) -> Decimal:
        total = Decimal("0")
        for entry in self.entries:
            if entry.candidate and entry.candidate.mark_to_close_pnl is not None:
                total += entry.candidate.mark_to_close_pnl
        return money(total)


class AlpacaMarketData:
    def __init__(self, cfg: config.AlpacaConfig):
        self._trading = broker.AlpacaBroker(cfg)
        self._data = httpx.Client(
            base_url="https://data.alpaca.markets",
            headers={
                "APCA-API-KEY-ID": cfg.key_id,
                "APCA-API-SECRET-KEY": cfg.secret_key,
            },
            timeout=20.0,
        )

    def close(self) -> None:
        self._trading.close()
        self._data.close()

    def option_contracts(
        self,
        *,
        underlying: str,
        expiration_gte: date,
        expiration_lte: date,
        option_type: str,
    ) -> list[OptionContract]:
        contracts: list[OptionContract] = []
        page_token: str | None = None
        while True:
            params = {
                "underlying_symbols": underlying,
                "expiration_date_gte": expiration_gte.isoformat(),
                "expiration_date_lte": expiration_lte.isoformat(),
                "type": option_type,
                "status": "active",
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token
            r = self._trading._client.get("/v2/options/contracts", params=params)
            r.raise_for_status()
            payload = r.json()
            for item in payload.get("option_contracts", []):
                contracts.append(
                    OptionContract(
                        symbol=item["symbol"],
                        expiration_date=date.fromisoformat(item["expiration_date"]),
                        strike_price=Decimal(str(item["strike_price"])),
                        option_type=item["type"],
                    )
                )
            page_token = payload.get("next_page_token")
            if not page_token:
                return contracts

    def stock_bars(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> list[dict]:
        r = self._data.get(
            f"/v2/stocks/{symbol}/bars",
            params={
                "start": to_utc_iso(start),
                "end": to_utc_iso(end),
                "timeframe": timeframe,
                "adjustment": "raw",
            },
        )
        r.raise_for_status()
        return r.json().get("bars", [])

    def option_bars(
        self,
        *,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict]]:
        if not symbols:
            return {}
        out: dict[str, list[dict]] = {}
        for chunk in chunks(symbols, 100):
            r = self._data.get(
                "/v1beta1/options/bars",
                params={
                    "symbols": ",".join(chunk),
                    "start": to_utc_iso(start),
                    "end": to_utc_iso(end),
                    "timeframe": "1Min",
                },
            )
            r.raise_for_status()
            out.update(r.json().get("bars", {}))
        return out


def run_backtest_for_date(
    *,
    trading_date: date,
    data: AlpacaMarketData,
    seed: int,
    entries_per_day: int = 5,
    target_min: Decimal = Decimal("0.20"),
    target_max: Decimal = Decimal("0.22"),
    reject_at_or_above: Decimal = Decimal("0.25"),
    min_dte: int = 8,
    max_dte: int = 42,
    spread_width: Decimal = Decimal("1"),
) -> BacktestResult:
    day_start = datetime.combine(trading_date, MARKET_OPEN, ET)
    day_end = datetime.combine(trading_date, MARKET_CLOSE, ET)
    spy_bars = data.stock_bars(symbol="SPY", start=day_start, end=day_end, timeframe="1Min")
    if not spy_bars:
        raise RuntimeError(f"no SPY intraday bars for {trading_date}")

    prev_close = previous_close(data, trading_date)
    rng = random.Random(seed)
    entry_times = random_entry_times(trading_date, entries_per_day, rng)
    expiry_pool = available_expiries(
        data,
        trading_date=trading_date,
        min_dte=min_dte,
        max_dte=max_dte,
    )
    if not expiry_pool:
        raise RuntimeError("no eligible SPY option expiries returned by Alpaca")

    entries: list[BacktestEntry] = []
    blocked_spreads: set[tuple[str, date, Decimal, Decimal]] = set()
    for entry_time in entry_times:
        spy_price = bar_close_at_or_before(spy_bars, entry_time)
        direction = "call_credit" if spy_price > prev_close else "put_credit"
        expiries = ranked_expiries(expiry_pool, entry_time, rng)
        candidate: SpreadCandidate | None = None
        reason = "no qualifying spread"
        for expiry in expiries:
            candidate = find_best_candidate(
                data=data,
                direction=direction,
                expiration_date=expiry,
                entry_time=entry_time,
                close_time=day_end,
                target_min=target_min,
                target_max=target_max,
                reject_at_or_above=reject_at_or_above,
                spread_width=spread_width,
                blocked_spreads=blocked_spreads,
            )
            if candidate is not None:
                reason = "selected"
                blocked_spreads.add(candidate.key)
                break
        entries.append(
            BacktestEntry(
                entry_time=entry_time,
                spy_price=spy_price,
                previous_close=prev_close,
                direction=direction,
                expiration_date=candidate.expiration_date if candidate else expiries[0],
                candidate=candidate,
                reason=reason,
            )
        )

    return BacktestResult(trading_date=trading_date, previous_close=prev_close, entries=entries)


def find_best_candidate(
    *,
    data: AlpacaMarketData,
    direction: str,
    expiration_date: date,
    entry_time: datetime,
    close_time: datetime,
    target_min: Decimal,
    target_max: Decimal,
    reject_at_or_above: Decimal,
    spread_width: Decimal,
    blocked_spreads: set[tuple[str, date, Decimal, Decimal]] | None = None,
) -> SpreadCandidate | None:
    option_type = "call" if direction == "call_credit" else "put"
    contracts = data.option_contracts(
        underlying="SPY",
        expiration_gte=expiration_date,
        expiration_lte=expiration_date,
        option_type=option_type,
    )
    by_strike = {c.strike_price: c for c in contracts}
    pairs: list[tuple[OptionContract, OptionContract]] = []
    for short in contracts:
        long_strike = short.strike_price + spread_width if direction == "call_credit" else short.strike_price - spread_width
        long = by_strike.get(long_strike)
        if long:
            pairs.append((short, long))
    if not pairs:
        return None

    symbols = sorted({contract.symbol for pair in pairs for contract in pair})
    bars = data.option_bars(symbols=symbols, start=entry_time - timedelta(minutes=2), end=close_time)
    candidates: list[SpreadCandidate] = []
    for short, long in pairs:
        credit = latest_spread_credit(
            short_bars=bars.get(short.symbol, []),
            long_bars=bars.get(long.symbol, []),
            moment=entry_time,
        )
        if credit is None:
            continue
        candidate_key = spread_key(
            direction=direction,
            expiration_date=expiration_date,
            short_strike=short.strike_price,
            long_strike=long.strike_price,
        )
        if blocked_spreads and candidate_key in blocked_spreads:
            continue
        if credit < target_min or credit > target_max or credit >= reject_at_or_above:
            continue
        close_credit = latest_spread_credit(
            short_bars=bars.get(short.symbol, []),
            long_bars=bars.get(long.symbol, []),
            moment=close_time,
        )
        candidates.append(
            SpreadCandidate(
                direction=direction,
                expiration_date=expiration_date,
                short_symbol=short.symbol,
                long_symbol=long.symbol,
                short_strike=short.strike_price,
                long_strike=long.strike_price,
                credit=credit,
                close_credit=close_credit,
            )
        )
    if not candidates:
        return None
    target = target_min
    return min(
        candidates,
        key=lambda c: (
            abs(c.credit - target),
            abs(c.short_strike - c.long_strike),
            c.expiration_date,
            c.short_strike,
        ),
    )


def spread_key(
    *,
    direction: str,
    expiration_date: date,
    short_strike: Decimal,
    long_strike: Decimal,
) -> tuple[str, date, Decimal, Decimal]:
    return (direction, expiration_date, short_strike, long_strike)


def previous_close(data: AlpacaMarketData, trading_date: date) -> Decimal:
    start = datetime.combine(trading_date - timedelta(days=10), time(0, 0), ET)
    end = datetime.combine(trading_date, time(0, 0), ET)
    bars = data.stock_bars(symbol="SPY", start=start, end=end, timeframe="1Day")
    eligible = [
        bar for bar in bars
        if datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET).date() < trading_date
    ]
    if not eligible:
        raise RuntimeError(f"no previous SPY daily bars before {trading_date}")
    return money(Decimal(str(eligible[-1]["c"])))


def available_expiries(
    data: AlpacaMarketData,
    *,
    trading_date: date,
    min_dte: int,
    max_dte: int,
) -> list[date]:
    contracts = data.option_contracts(
        underlying="SPY",
        expiration_gte=trading_date + timedelta(days=min_dte),
        expiration_lte=trading_date + timedelta(days=max_dte),
        option_type="put",
    )
    return sorted({c.expiration_date for c in contracts})


def ranked_expiries(expiries: list[date], entry_time: datetime, rng: random.Random) -> list[date]:
    # Keep some DCA randomness, but scan nearby liquid buckets before giving up.
    shuffled = expiries[:]
    rng.shuffle(shuffled)
    preferred = sorted(
        shuffled,
        key=lambda d: (
            abs((d - entry_time.date()).days - 21),
            abs((d - entry_time.date()).days - 14),
            d,
        ),
    )
    return preferred


def random_entry_times(trading_date: date, count: int, rng: random.Random) -> list[datetime]:
    open_dt = datetime.combine(trading_date, MARKET_OPEN, ET)
    close_dt = datetime.combine(trading_date, MARKET_CLOSE, ET)
    latest = close_dt - timedelta(minutes=config.NO_NEW_TRADES_BEFORE_CLOSE_MIN)
    total_minutes = int((latest - open_dt).total_seconds() // 60)
    offsets = sorted(rng.sample(range(total_minutes + 1), count))
    return [open_dt + timedelta(minutes=offset) for offset in offsets]


def bar_close_at_or_before(bars: list[dict], moment: datetime) -> Decimal:
    close = latest_close(bars, moment)
    if close is None:
        raise RuntimeError(f"no bar at or before {moment.isoformat()}")
    return close


def latest_close(bars: list[dict], moment: datetime) -> Decimal | None:
    latest: Decimal | None = None
    for bar in bars:
        ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET)
        if ts <= moment:
            latest = money(Decimal(str(bar["c"])))
        else:
            break
    return latest


def latest_spread_credit(
    *,
    short_bars: list[dict],
    long_bars: list[dict],
    moment: datetime,
) -> Decimal | None:
    short_by_ts = bars_by_timestamp(short_bars, moment)
    long_by_ts = bars_by_timestamp(long_bars, moment)
    common = sorted(set(short_by_ts) & set(long_by_ts))
    if not common:
        return None
    ts = common[-1]
    return money(short_by_ts[ts] - long_by_ts[ts])


def bars_by_timestamp(bars: list[dict], moment: datetime) -> dict[datetime, Decimal]:
    out: dict[datetime, Decimal] = {}
    for bar in bars:
        ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET)
        if ts <= moment:
            out[ts] = money(Decimal(str(bar["c"])))
    return out


def to_utc_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def chunks(items: list[str], size: int):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]
