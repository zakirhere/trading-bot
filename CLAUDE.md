# Personal Trading Bot

## What this project is
A personal auto-trading bot for defined-risk option spreads (vertical spreads,
iron condors). Real money. Fully automated execution is the goal; cautious
phased ramp from backtest → paper → live micro → scale.

## Non-negotiable defaults — these are code, not config
- **Paper-only by default.** Live trading requires explicit `TRADEBOT_LIVE=1` env var.
  Default mode must never submit live orders.
- **Risk caps are hardcoded guardrails.** Config can only make them tighter, never looser:
  - Max risk per trade: $500
  - Max total open risk: $10,000
  - Max concurrent positions: 20
  - Daily loss limit: -2% of account → hard halt
- **Every order needs an idempotency key:** `{strategy}_{symbol}_{date}_{intent_hash}`.
  Network retries must not double-fill.
- **Killswitch is a one-liner.** `POST /halt` disables new order submission instantly.
- **Daily reconciliation:** bot's tracked positions must match broker's positions,
  else halt + alert. Drift = bug.
- **Pre-trade liquidity check:** min open interest, max bid-ask spread.
  Don't get stuck in illiquid contracts.
- **No new trades < 5 min before close.** End-of-day liquidity traps.
- **Earnings exclusion:** don't open positions with earnings before expiry.
- **Option spreads use limit orders only.** Never submit market orders for option
  spreads. Entries require limit credit at or above the strategy minimum; exits
  require limit debit at or below the profit target.
- **Never trade AAPL.** User works for Apple. AAPL is excluded from manual stock
  orders, stock strategies, and future scanners.
- **Alpaca data feed constraints matter.** Paper/basic data requires
  `feed=iex` for stock bars and `feed=indicative` for latest option quotes.
  Historical option bars do not accept `feed`; current/future option bars can
  return 403 without OPRA entitlement. Live schedulers should select from
  latest indicative quotes.
- **Alpaca MLeg limit prices are signed.** Positive `limit_price` is debit;
  negative `limit_price` is credit. A `$0.58` credit spread must submit
  `limit_price="-0.58"` or it may fill for less credit than intended.

## Architectural style
- Python daemon, launchd service, runs as me (personal Mac).
- State file: `~/.tradebot/state.json` (positions, open orders, killswitch).
- HTTP dashboard on localhost for monitoring.
- Slack/iMessage alerts via personal webhook (NOT Westgate — this isn't Apple-internal).
- Sub-minute polling for live signals (trading needs faster signal than 30-min ticks).

## Phased ramp — don't skip phases
| Phase | What runs | Money at risk |
|---|---|---|
| 0. Backtest | Rules vs historical data | $0 |
| 1. Paper + alerts | Paper mode, Slack alerts only | $0 |
| 2. Paper + auto-execute | Paper orders, verify execution | $0 |
| 3. Live, micro size | 1 contract, $50-100 max risk | $200-500 total |
| 4. Scale gradually | Increase only if live P&L ≈ paper P&L | Up to limits |

If paper P&L diverges from live P&L at phase 3, that's a model bug — fix before scaling.

## What I haven't decided yet
- Broker: Tastytrade > IBKR > Alpaca (need to verify 2026 API status before committing)
- Account size + initial capital
- Backtesting data source / library
- Hosting: laptop + launchd, or cloud (laptop = simpler, cloud = always-on)

## Strategy candidates under simulation
- SPY directional $0.20 credit spread DCA:
  5 random daily entries, direction from SPY green/red vs previous close, expiry
  8-42 DTE, $1-wide spreads, no duplicate expiry/strike combinations.
- SPY $0.60 income credit spread:
  5 total daily entries, scan both call and put credit spreads, expiry 8-42 DTE,
  $1-wide spreads, no duplicate expiry/strike combinations, close winners at
  50% profit using limit debit orders, keep losers open across days, continue
  opening until open risk is near the $10k simulation cap. During a trading day,
  refill closed winners toward 5 active ICL trades, but never open more than 10
  ICL trades in one day.
  Current automation slice is paper-only entry autorun behind
  `TRADEBOT_ICL_PAPER_AUTORUN=1`; 50% close/refill monitoring is not wired yet.
- SPY DCA paper autorun:
  enabled behind `TRADEBOT_DCA_PAPER_AUTORUN=1`, queues paper-only SPY DCA
  limit-credit spread entries. DCA is contrarian/fade: SPY green -> call credit,
  SPY red -> put credit.
- ORB stock observe:
  enabled behind `TRADEBOT_ORB_OBSERVE=1`, monitors SPY, QQQ, NVDA, TSLA, MSFT
  for opening-range breakouts after the first 15 minutes until 11:30 ET. It logs
  and alerts only; it does not place stock orders. AAPL is explicitly excluded.

## Working style with me (Claude)
- Bias toward caution over cleverness in every architectural decision.
- Push back on shortcuts that compromise risk controls.
- For broker/API-specific questions, verify current state before recommending —
  the trading ecosystem changes fast and my knowledge may be stale.
- I value substantive disagreement with reasoning over yes-anding.
- Don't auto-commit to live trading code without explicit confirmation per change.
- Default branch is whatever I set up in the repo — ask if unsure.
