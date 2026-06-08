from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradebot import spy_credit_strategy as strategy

ET = ZoneInfo("America/New_York")


class FakeData:
    def __init__(self):
        self.expiries_seen: list[date] = []

    def stock_bars(self, *, symbol, start, end, timeframe):
        assert symbol == "SPY"
        if timeframe == "1Day":
            return [{"t": "2026-06-04T00:00:00Z", "c": 757.09}]
        return [
            {"t": "2026-06-05T13:30:00Z", "c": 750.00},
            {"t": "2026-06-05T14:30:00Z", "c": 749.00},
            {"t": "2026-06-05T15:30:00Z", "c": 748.00},
            {"t": "2026-06-05T19:59:00Z", "c": 740.00},
        ]

    def option_contracts(self, *, underlying, expiration_gte, expiration_lte, option_type):
        assert underlying == "SPY"
        if expiration_gte != expiration_lte:
            return [
                strategy.OptionContract("SPY260619P00700000", date(2026, 6, 19), Decimal("700"), "put"),
                strategy.OptionContract("SPY260626P00700000", date(2026, 6, 26), Decimal("700"), "put"),
            ]
        self.expiries_seen.append(expiration_gte)
        if expiration_gte == date(2026, 6, 19):
            return [
                strategy.OptionContract("SHORT_BAD", expiration_gte, Decimal("700"), option_type),
                strategy.OptionContract("LONG_BAD", expiration_gte, Decimal("699"), option_type),
            ]
        return [
            strategy.OptionContract("SHORT_OK", expiration_gte, Decimal("700"), option_type),
            strategy.OptionContract("LONG_OK", expiration_gte, Decimal("699"), option_type),
        ]

    def option_bars(self, *, symbols, start, end):
        if "SHORT_BAD" in symbols:
            return {
                "SHORT_BAD": [
                    {"t": "2026-06-05T14:30:00Z", "c": 1.00},
                    {"t": "2026-06-05T19:59:00Z", "c": 1.00},
                ],
                "LONG_BAD": [
                    {"t": "2026-06-05T14:30:00Z", "c": 0.75},
                    {"t": "2026-06-05T19:59:00Z", "c": 0.75},
                ],
            }
        return {
            "SHORT_OK": [
                {"t": "2026-06-05T14:30:00Z", "c": 1.00},
                {"t": "2026-06-05T19:59:00Z", "c": 0.80},
            ],
            "LONG_OK": [
                {"t": "2026-06-05T14:30:00Z", "c": 0.79},
                {"t": "2026-06-05T19:59:00Z", "c": 0.72},
            ],
        }


class DuplicateSpreadData:
    def stock_bars(self, *, symbol, start, end, timeframe):
        assert symbol == "SPY"
        if timeframe == "1Day":
            return [{"t": "2026-06-04T04:00:00Z", "c": 757.09}]
        return [
            {"t": "2026-06-05T13:30:00Z", "c": 750.00},
            {"t": "2026-06-05T14:30:00Z", "c": 749.00},
            {"t": "2026-06-05T15:30:00Z", "c": 748.00},
            {"t": "2026-06-05T19:59:00Z", "c": 740.00},
        ]

    def option_contracts(self, *, underlying, expiration_gte, expiration_lte, option_type):
        assert underlying == "SPY"
        if expiration_gte != expiration_lte:
            return [
                strategy.OptionContract("PREFERRED_SHORT", date(2026, 6, 26), Decimal("700"), "put"),
                strategy.OptionContract("FALLBACK_SHORT", date(2026, 6, 30), Decimal("690"), "put"),
            ]
        if expiration_gte == date(2026, 6, 26):
            return [
                strategy.OptionContract("PREFERRED_SHORT", expiration_gte, Decimal("700"), option_type),
                strategy.OptionContract("PREFERRED_LONG", expiration_gte, Decimal("699"), option_type),
            ]
        return [
            strategy.OptionContract("FALLBACK_SHORT", expiration_gte, Decimal("690"), option_type),
            strategy.OptionContract("FALLBACK_LONG", expiration_gte, Decimal("689"), option_type),
        ]

    def option_bars(self, *, symbols, start, end):
        if "PREFERRED_SHORT" in symbols:
            return {
                "PREFERRED_SHORT": [
                    {"t": "2026-06-05T14:30:00Z", "c": 1.00},
                    {"t": "2026-06-05T15:30:00Z", "c": 1.00},
                    {"t": "2026-06-05T19:59:00Z", "c": 0.80},
                ],
                "PREFERRED_LONG": [
                    {"t": "2026-06-05T14:30:00Z", "c": 0.79},
                    {"t": "2026-06-05T15:30:00Z", "c": 0.79},
                    {"t": "2026-06-05T19:59:00Z", "c": 0.72},
                ],
            }
        return {
            "FALLBACK_SHORT": [
                {"t": "2026-06-05T14:30:00Z", "c": 1.00},
                {"t": "2026-06-05T15:30:00Z", "c": 1.00},
                {"t": "2026-06-05T19:59:00Z", "c": 0.80},
            ],
            "FALLBACK_LONG": [
                {"t": "2026-06-05T14:30:00Z", "c": 0.79},
                {"t": "2026-06-05T15:30:00Z", "c": 0.79},
                {"t": "2026-06-05T19:59:00Z", "c": 0.72},
            ],
        }


class IncomeStrategyData:
    def stock_bars(self, *, symbol, start, end, timeframe):
        assert symbol == "SPY"
        if timeframe == "1Day":
            return [{"t": "2026-06-04T04:00:00Z", "c": 757.09}]
        return [
            {"t": "2026-06-05T13:30:00Z", "c": 750.00},
            {"t": "2026-06-05T14:30:00Z", "c": 749.00},
            {"t": "2026-06-05T15:30:00Z", "c": 748.00},
            {"t": "2026-06-05T19:59:00Z", "c": 740.00},
        ]

    def option_contracts(self, *, underlying, expiration_gte, expiration_lte, option_type):
        assert underlying == "SPY"
        if expiration_gte != expiration_lte:
            return [
                strategy.OptionContract("EXPIRY_MARKER", date(2026, 6, 26), Decimal("700"), "put"),
            ]
        if option_type == "call":
            return [
                strategy.OptionContract("CALL_SHORT", expiration_gte, Decimal("700"), option_type),
                strategy.OptionContract("CALL_LONG", expiration_gte, Decimal("701"), option_type),
            ]
        return [
            strategy.OptionContract("PUT_SHORT", expiration_gte, Decimal("700"), option_type),
            strategy.OptionContract("PUT_LONG", expiration_gte, Decimal("699"), option_type),
        ]

    def option_bars(self, *, symbols, start, end):
        if "CALL_SHORT" in symbols:
            return {
                "CALL_SHORT": [
                    {"t": "2026-06-05T14:30:00Z", "c": 1.00},
                    {"t": "2026-06-05T15:30:00Z", "c": 0.70},
                    {"t": "2026-06-05T19:59:00Z", "c": 0.72},
                ],
                "CALL_LONG": [
                    {"t": "2026-06-05T14:30:00Z", "c": 0.40},
                    {"t": "2026-06-05T15:30:00Z", "c": 0.40},
                    {"t": "2026-06-05T19:59:00Z", "c": 0.43},
                ],
            }
        return {
            "PUT_SHORT": [
                {"t": "2026-06-05T14:30:00Z", "c": 1.00},
                {"t": "2026-06-05T19:59:00Z", "c": 1.00},
            ],
            "PUT_LONG": [
                {"t": "2026-06-05T14:30:00Z", "c": 0.10},
                {"t": "2026-06-05T19:59:00Z", "c": 0.10},
            ],
        }


class LatestQuoteData:
    def option_contracts(self, *, underlying, expiration_gte, expiration_lte, option_type):
        assert underlying == "SPY"
        assert expiration_gte == expiration_lte
        return [
            strategy.OptionContract("BLOCKED_SHORT", expiration_gte, Decimal("700"), option_type),
            strategy.OptionContract("BLOCKED_LONG", expiration_gte, Decimal("699"), option_type),
            strategy.OptionContract("OPEN_SHORT", expiration_gte, Decimal("690"), option_type),
            strategy.OptionContract("OPEN_LONG", expiration_gte, Decimal("689"), option_type),
        ]

    def option_latest_quotes(self, *, symbols):
        return {
            "BLOCKED_SHORT": {"bp": 1.00},
            "BLOCKED_LONG": {"ap": 0.80},
            "OPEN_SHORT": {"bp": 1.00},
            "OPEN_LONG": {"ap": 0.80},
        }


def test_find_best_candidate_rejects_25_credit_and_accepts_21():
    data = FakeData()
    entry_time = datetime(2026, 6, 5, 10, 30, tzinfo=ET)
    close_time = datetime.combine(date(2026, 6, 5), time(16, 0), ET)

    rejected = strategy.find_best_candidate(
        data=data,
        direction="put_credit",
        expiration_date=date(2026, 6, 19),
        entry_time=entry_time,
        close_time=close_time,
        target_min=Decimal("0.20"),
        target_max=Decimal("0.22"),
        reject_at_or_above=Decimal("0.25"),
        spread_width=Decimal("1"),
    )
    accepted = strategy.find_best_candidate(
        data=data,
        direction="put_credit",
        expiration_date=date(2026, 6, 26),
        entry_time=entry_time,
        close_time=close_time,
        target_min=Decimal("0.20"),
        target_max=Decimal("0.22"),
        reject_at_or_above=Decimal("0.25"),
        spread_width=Decimal("1"),
    )

    assert rejected is None
    assert accepted is not None
    assert accepted.credit == Decimal("0.21")
    assert accepted.close_credit == Decimal("0.08")
    assert accepted.mark_to_close_pnl == Decimal("13.00")


def test_backtest_scans_next_expiry_when_first_has_no_valid_spread():
    data = FakeData()

    result = strategy.run_backtest_for_date(
        trading_date=date(2026, 6, 5),
        data=data,
        seed=1,
        entries_per_day=1,
    )

    assert result.previous_close == Decimal("757.09")
    assert result.trades_found == 1
    assert result.total_pnl == Decimal("13.00")
    assert result.entries[0].direction == "put_credit"
    assert result.entries[0].candidate is not None
    assert result.entries[0].candidate.expiration_date == date(2026, 6, 26)


def test_backtest_does_not_repeat_same_spread_in_same_day():
    result = strategy.run_backtest_for_date(
        trading_date=date(2026, 6, 5),
        data=DuplicateSpreadData(),
        seed=1,
        entries_per_day=2,
    )

    selected = [entry.candidate for entry in result.entries if entry.candidate is not None]

    assert len(selected) == 2
    assert selected[0].key != selected[1].key
    assert selected[0].expiration_date == date(2026, 6, 26)
    assert selected[1].expiration_date == date(2026, 6, 30)


def test_latest_spread_credit_requires_same_timestamp_for_both_legs():
    short_bars = [
        {"t": "2026-06-05T14:30:00Z", "c": 1.00},
        {"t": "2026-06-05T15:00:00Z", "c": 0.90},
    ]
    long_bars = [
        {"t": "2026-06-05T14:30:00Z", "c": 0.79},
        {"t": "2026-06-05T15:01:00Z", "c": 0.95},
    ]

    credit = strategy.latest_spread_credit(
        short_bars=short_bars,
        long_bars=long_bars,
        moment=datetime(2026, 6, 5, 11, 0, tzinfo=ET),
    )

    assert credit == Decimal("0.21")


def test_income_direction_follows_spy_momentum():
    assert strategy.income_direction(
        spy_price=Decimal("751"),
        previous_close=Decimal("750"),
    ) == "put_credit"
    assert strategy.income_direction(
        spy_price=Decimal("749"),
        previous_close=Decimal("750"),
    ) == "call_credit"


def test_income_backtest_closes_at_50_percent_profit_target():
    result = strategy.run_income_backtest_for_date(
        trading_date=date(2026, 6, 5),
        data=IncomeStrategyData(),
        seed=1,
        entries_per_day=1,
    )

    entry = result.entries[0]

    assert result.trades_found == 1
    assert entry.candidate is not None
    assert entry.candidate.direction == "call_credit"
    assert entry.candidate.credit == Decimal("0.60")
    assert entry.target_close_credit == Decimal("0.30")
    assert entry.exit_credit == Decimal("0.30")
    assert entry.pnl == Decimal("30.00")
    assert entry.exit_time is not None
    assert result.realized_pnl == Decimal("30.00")
    assert result.unrealized_pnl == Decimal("0.00")
    assert result.open_risk_at_close == Decimal("0.00")


def test_latest_quote_selection_skips_blocked_open_symbols():
    candidate = strategy.find_best_candidate_from_latest_quotes(
        data=LatestQuoteData(),
        direction="put_credit",
        expiration_date=date(2026, 6, 26),
        target_min=Decimal("0.20"),
        target_max=Decimal("0.22"),
        reject_at_or_above=Decimal("0.25"),
        spread_width=Decimal("1"),
        blocked_symbols={"BLOCKED_LONG"},
    )

    assert candidate is not None
    assert candidate.short_symbol == "OPEN_SHORT"
    assert candidate.long_symbol == "OPEN_LONG"
