from __future__ import annotations

import argparse
import logging
import sys

from . import broker, config, killswitch, signals, state


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
    print(f"HALTED ({reason})")
    return 0


def cmd_resume() -> int:
    with state.transaction() as s:
        s.halted = False
        s.halt_reason = None
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

    mode = "LIVE" if cfg.is_live else "PAPER"
    print(f"mode={mode} base_url={cfg.base_url}")
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


def cmd_run(*, dry_run: bool, no_server: bool, force_closed: bool) -> int:
    log = logging.getLogger("daemon")

    cfg = config.load_alpaca_config()
    log.info(
        "config loaded: base_url=%s live=%s dry_run=%s",
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
            return 3

        if not no_server:
            killswitch.start_in_thread()

        result = signals.nio_buy(b, dry_run=dry_run)
        log.info("result fired=%s reason=%s", result.fired, result.reason)
        if result.order:
            log.info("order id=%s status=%s", result.order.broker_order_id, result.order.status)
        return 0
    except Exception:
        log.exception("daemon failed")
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
    return cmd_run(
        dry_run=args.dry_run, no_server=args.no_server, force_closed=args.force_closed
    )


if __name__ == "__main__":
    raise SystemExit(main())
