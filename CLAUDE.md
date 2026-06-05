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
  - Max total open risk: $2,000
  - Max concurrent positions: 5
  - Daily loss limit: -2% of account → hard halt
- **Every order needs an idempotency key:** `{strategy}_{symbol}_{date}_{intent_hash}`.
  Network retries must not double-fill.
- **Killswitch is a one-liner.** `POST /halt` disables new order submission instantly.
- **Daily reconciliation:** bot's tracked positions must match broker's positions,
  else halt + alert. Drift = bug.
- **Pre-trade liquidity check:** min open interest, max bid-ask spread.
  Don't get stuck in illiquid contracts.
- **No new trades < 30 min before close.** End-of-day liquidity traps.
- **Earnings exclusion:** don't open positions with earnings before expiry.

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
- Specific first rule (initial candidate: "sell 30-day, 25-delta SPY put credit
  spread when VIX > 20 and account is < 50% deployed")
- Backtesting data source / library
- Hosting: laptop + launchd, or cloud (laptop = simpler, cloud = always-on)

## Working style with me (Claude)
- Bias toward caution over cleverness in every architectural decision.
- Push back on shortcuts that compromise risk controls.
- For broker/API-specific questions, verify current state before recommending —
  the trading ecosystem changes fast and my knowledge may be stale.
- I value substantive disagreement with reasoning over yes-anding.
- Don't auto-commit to live trading code without explicit confirmation per change.
- Default branch is whatever I set up in the repo — ask if unsure.
