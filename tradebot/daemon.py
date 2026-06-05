from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from . import api, broker, config, db, killswitch, notify, signals, state, worker


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )


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
