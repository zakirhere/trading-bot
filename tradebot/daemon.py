from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from datetime import date

from . import api, broker, config, db, journal, killswitch, notify, signals, spy_credit_strategy, spread_audit, state, worker


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_halt(reason: str) -> int:
    with state.transaction() as s:
        s.halted = True
        s.halt_reason = reason
    notify.send(
        notify.Alert(
            level="halt",
            title="Bot halted",
            message=reason,
        )
    )
    print(f"HALTED ({reason})")
    return 0


def cmd_resume() -> int:
    with state.transaction() as s:
        s.halted = False
        s.halt_reason = None
    notify.send(
        notify.Alert(
            level="info",
            title="Bot resumed",
            message="New order submission is allowed again.",
        )
    )
    print("RESUMED")
    return 0


def cmd_status() -> int:
    s = state.load()
    print(f"halted={s.halted} reason={s.halt_reason!r}")
    print(f"processed_keys={len(s.processed_keys)} orders={len(s.orders)}")
    for o in s.orders[-5:]:
        print(f"  {o.submitted_at} {o.side} {o.qty} {o.symbol} status={o.status} id={o.broker_order_id}")
    return 0


def cmd_account_check() -> int:
    cfg = config.load_alpaca_config()
    b = broker.AlpacaBroker(cfg)
    try:
        acct = b.get_account()
        clock = b.get_clock()
    finally:
        b.close()

    print(f"mode={cfg.mode.upper()} base_url={cfg.base_url}")
    print(
        "account="
        f"{acct.get('account_number')} "
        f"status={acct.get('status')} "
        f"trading_blocked={acct.get('trading_blocked')} "
        f"account_blocked={acct.get('account_blocked')}"
    )
    print(
        "balances="
        f"buying_power={acct.get('buying_power')} "
        f"cash={acct.get('cash')} "
        f"portfolio_value={acct.get('portfolio_value')}"
    )
    print(
        "clock="
        f"is_open={clock.get('is_open')} "
        f"next_open={clock.get('next_open')} "
        f"next_close={clock.get('next_close')}"
    )
    return 0


def cmd_run_worker_once(*, force_closed: bool) -> int:
    conn = db.connect()
    try:
        results = worker.run_once(conn, force_closed=force_closed)
    finally:
        conn.close()
    print(f"processed={len(results)}")
    for req in results:
        print(f"  id={req.id} status={req.status} symbol={req.symbol} reason={req.reason!r}")
    return 0


def cmd_sync_journal() -> int:
    conn = db.connect()
    db.init(conn)
    try:
        path = journal.sync_from_db(conn)
    finally:
        conn.close()
    print(f"journal={path}")
    return 0


def cmd_audit_spreads(*, send_notify: bool, json_output: bool) -> int:
    audit = spread_audit.run()
    if json_output:
        print(json.dumps(spread_audit.to_dict(audit), indent=2, sort_keys=True))
    else:
        print(
            "spread_audit="
            f"ok={audit.ok} "
            f"paired={len(audit.paired_spreads)} "
            f"unpaired={len(audit.unpaired_legs)} "
            f"local_drift={len(audit.local_drift)} "
            f"option_open_risk=${audit.option_open_risk_usd:.2f} "
            f"position_slots={audit.position_slots}"
        )
        if audit.unpaired_legs:
            print("unpaired:")
            for item in audit.unpaired_legs:
                print(f"  {item.side} {item.symbol} qty={item.qty} market_value={item.market_value}")
        if audit.local_drift:
            print("local_drift:")
            for item in audit.local_drift:
                print(
                    f"  request={item.request_id} strategy={item.strategy} "
                    f"status={item.status} missing={','.join(item.missing_broker_symbols)}"
                )
        if audit.unparsable_option_symbols:
            print("unparsable:")
            for symbol in audit.unparsable_option_symbols:
                print(f"  {symbol}")

    if send_notify:
        sent = spread_audit.notify_if_changed(audit)
        print(f"notified={sent}")
    return 0


def cmd_backtest_spy_credit(*, trading_date: date, seed: int) -> int:
    cfg = config.load_alpaca_config()
    data = spy_credit_strategy.AlpacaMarketData(cfg)
    try:
        result = spy_credit_strategy.run_backtest_for_date(
            trading_date=trading_date,
            data=data,
            seed=seed,
        )
    finally:
        data.close()

    print(f"date={result.trading_date} previous_close={result.previous_close}")
    print(f"trades_found={result.trades_found} total_pnl=${result.total_pnl}")
    print("time_et,spy,direction,expiry,short,long,entry_credit,close_credit,pnl,reason")
    for entry in result.entries:
        candidate = entry.candidate
        if candidate is None:
            print(
                ",".join(
                    [
                        entry.entry_time.strftime("%H:%M"),
                        str(entry.spy_price),
                        entry.direction,
                        entry.expiration_date.isoformat(),
                        "",
                        "",
                        "",
                        "",
                        "",
                        entry.reason,
                    ]
                )
            )
            continue
        pnl = candidate.mark_to_close_pnl
        print(
            ",".join(
                [
                    entry.entry_time.strftime("%H:%M"),
                    str(entry.spy_price),
                    entry.direction,
                    candidate.expiration_date.isoformat(),
                    f"{candidate.short_symbol}@{candidate.short_strike}",
                    f"{candidate.long_symbol}@{candidate.long_strike}",
                    str(candidate.credit),
                    "" if candidate.close_credit is None else str(candidate.close_credit),
                    "" if pnl is None else str(pnl),
                    entry.reason,
                ]
            )
        )
    return 0


def cmd_backtest_spy_income_credit(*, trading_date: date, seed: int) -> int:
    cfg = config.load_alpaca_config()
    data = spy_credit_strategy.AlpacaMarketData(cfg)
    try:
        result = spy_credit_strategy.run_income_backtest_for_date(
            trading_date=trading_date,
            data=data,
            seed=seed,
        )
    finally:
        data.close()

    print(f"date={result.trading_date} previous_close={result.previous_close}")
    print(
        "trades_found="
        f"{result.trades_found} "
        f"realized_pnl=${result.realized_pnl} "
        f"unrealized_pnl=${result.unrealized_pnl} "
        f"total_pnl=${result.total_pnl} "
        f"open_risk_at_close=${result.open_risk_at_close}"
    )
    print("time_et,spy,direction,expiry,short,long,entry_credit,target_close,exit_or_close_credit,pnl,status")
    for entry in result.entries:
        candidate = entry.candidate
        if candidate is None:
            print(
                ",".join(
                    [
                        entry.entry_time.strftime("%H:%M"),
                        str(entry.spy_price),
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        entry.status,
                    ]
                )
            )
            continue
        print(
            ",".join(
                [
                    entry.entry_time.strftime("%H:%M"),
                    str(entry.spy_price),
                    candidate.direction,
                    candidate.expiration_date.isoformat(),
                    f"{candidate.short_symbol}@{candidate.short_strike}",
                    f"{candidate.long_symbol}@{candidate.long_strike}",
                    str(candidate.credit),
                    "" if entry.target_close_credit is None else str(entry.target_close_credit),
                    "" if entry.exit_credit is None else str(entry.exit_credit),
                    "" if entry.pnl is None else str(entry.pnl),
                    entry.status,
                ]
            )
        )
    return 0


def cmd_serve(*, force_closed: bool) -> int:
    stop_event = threading.Event()

    def stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    server = api.start_in_thread()
    thread = threading.Thread(
        target=worker.run_forever,
        kwargs={"force_closed": force_closed, "stop_event": stop_event},
        daemon=True,
        name="worker",
    )
    thread.start()
    print(f"dashboard=http://{config.SERVICE_HOST}:{config.SERVICE_PORT}")
    try:
        while not stop_event.is_set():
            stop_event.wait(1)
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_run(
    *,
    dry_run: bool,
    no_server: bool,
    force_closed: bool,
    symbol: str | None,
    qty: float | None,
) -> int:
    log = logging.getLogger("daemon")

    cfg = config.load_alpaca_config()
    log.info(
        "config loaded: mode=%s base_url=%s live=%s dry_run=%s",
        cfg.mode,
        cfg.base_url,
        cfg.is_live,
        dry_run,
    )

    b = broker.AlpacaBroker(cfg)
    try:
        acct = b.get_account()
        log.info(
            "alpaca acct=%s status=%s buying_power=$%s",
            acct.get("account_number"),
            acct.get("status"),
            acct.get("buying_power"),
        )
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            log.error("trading_blocked or account_blocked — aborting")
            notify.send(
                notify.Alert(
                    level="halt",
                    title="Alpaca account blocked",
                    message="Daemon aborted before signal execution.",
                    fields={
                        "account_number": acct.get("account_number"),
                        "trading_blocked": acct.get("trading_blocked"),
                        "account_blocked": acct.get("account_blocked"),
                    },
                )
            )
            return 2

        clock = b.get_clock()
        log.info(
            "market is_open=%s next_open=%s next_close=%s",
            clock.get("is_open"),
            clock.get("next_open"),
            clock.get("next_close"),
        )
        if not clock.get("is_open") and not dry_run and not force_closed:
            log.warning("market not open — refusing to fire. Use --force-closed to override.")
            notify.send(
                notify.Alert(
                    level="risk",
                    title="Market closed",
                    message="Daemon refused to submit orders because Alpaca clock is closed.",
                    fields={
                        "next_open": clock.get("next_open"),
                        "next_close": clock.get("next_close"),
                    },
                )
            )
            return 3

        if not no_server:
            killswitch.start_in_thread()

        if symbol is None and qty is None:
            result = signals.nio_buy(b, dry_run=dry_run)
        else:
            if not symbol or qty is None:
                raise RuntimeError("--symbol and --qty must be provided together")
            result = signals.stock_buy(b, symbol=symbol, qty=qty, dry_run=dry_run)
        log.info("result fired=%s reason=%s", result.fired, result.reason)
        if result.order:
            log.info("order id=%s status=%s", result.order.broker_order_id, result.order.status)
        return 0
    except Exception as exc:
        log.exception("daemon failed")
        notify.send(
            notify.Alert(
                level="error",
                title="Daemon failed",
                message=str(exc),
            )
        )
        return 1
    finally:
        b.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tradebot")
    p.add_argument("--halt", metavar="REASON", nargs="?", const="manual",
                   help="set halted=true in state and exit")
    p.add_argument("--resume", action="store_true", help="set halted=false in state and exit")
    p.add_argument("--status", action="store_true", help="print state and exit")
    p.add_argument("--account-check", action="store_true",
                   help="check Alpaca account and market clock without running signals")
    p.add_argument("--dry-run", action="store_true",
                   help="run signal logic without submitting real orders")
    p.add_argument("--no-server", action="store_true",
                   help="do not start the killswitch HTTP server")
    p.add_argument("--force-closed", action="store_true",
                   help="proceed even if Alpaca clock reports market closed")
    p.add_argument("--serve", action="store_true",
                   help="run persistent dashboard/API and queue worker")
    p.add_argument("--run-worker-once", action="store_true",
                   help="process currently due queued trade requests and exit")
    p.add_argument("--sync-journal", action="store_true",
                   help="write the local Markdown trade journal from SQLite and exit")
    p.add_argument("--audit-spreads", action="store_true",
                   help="audit option spread pairing and broker/local drift")
    p.add_argument("--notify", action="store_true",
                   help="send configured notification for commands that support it")
    p.add_argument("--json", action="store_true",
                   help="emit JSON for commands that support it")
    p.add_argument("--backtest-spy-credit", metavar="YYYY-MM-DD",
                   help="backtest the SPY credit-spread DCA scanner for one trading date")
    p.add_argument("--backtest-spy-income-credit", metavar="YYYY-MM-DD",
                   help="backtest the SPY 0.60-credit spread income scanner for one trading date")
    p.add_argument("--backtest-seed", type=int, default=20260605,
                   help="random seed for backtest entry times")
    p.add_argument("--symbol", help="stock symbol for a plumbing market-buy signal")
    p.add_argument("--qty", type=float, help="share quantity for --symbol")
    args = p.parse_args(argv)

    setup_logging()

    if args.halt is not None:
        return cmd_halt(args.halt)
    if args.resume:
        return cmd_resume()
    if args.status:
        return cmd_status()
    if args.account_check:
        return cmd_account_check()
    if args.run_worker_once:
        return cmd_run_worker_once(force_closed=args.force_closed)
    if args.sync_journal:
        return cmd_sync_journal()
    if args.audit_spreads:
        return cmd_audit_spreads(send_notify=args.notify, json_output=args.json)
    if args.backtest_spy_credit:
        return cmd_backtest_spy_credit(
            trading_date=date.fromisoformat(args.backtest_spy_credit),
            seed=args.backtest_seed,
        )
    if args.backtest_spy_income_credit:
        return cmd_backtest_spy_income_credit(
            trading_date=date.fromisoformat(args.backtest_spy_income_credit),
            seed=args.backtest_seed,
        )
    if args.serve:
        return cmd_serve(force_closed=args.force_closed)
    return cmd_run(
        dry_run=args.dry_run,
        no_server=args.no_server,
        force_closed=args.force_closed,
        symbol=args.symbol,
        qty=args.qty,
    )


if __name__ == "__main__":
    raise SystemExit(main())
