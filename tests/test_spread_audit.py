from tradebot import db, spread_audit


def test_pair_broker_positions_reports_paired_and_unpaired_short():
    positions = [
        {
            "symbol": "SPY260702C00755000",
            "asset_class": "us_option",
            "qty": "-1",
            "market_value": "-412",
        },
        {
            "symbol": "SPY260702C00756000",
            "asset_class": "us_option",
            "qty": "1",
            "market_value": "375",
        },
        {
            "symbol": "SPY260702C00765000",
            "asset_class": "us_option",
            "qty": "-1",
            "market_value": "-152",
        },
    ]

    pairs, unpaired, unparsable = spread_audit.pair_broker_positions(positions)

    assert len(pairs) == 1
    assert pairs[0].short_symbol == "SPY260702C00755000"
    assert pairs[0].long_symbol == "SPY260702C00756000"
    assert [item.symbol for item in unpaired] == ["SPY260702C00765000"]
    assert unpaired[0].side == "short"
    assert unparsable == []


def test_find_local_drift_flags_missing_broker_leg(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={
                "strategy": "DCA",
                "short_symbol": "SPY260702C00755000",
                "long_symbol": "SPY260702C00756000",
                "legs": [],
            },
        )
        db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")

        drift = spread_audit.find_local_drift(
            conn,
            broker_symbols={"SPY260702C00755000"},
        )
    finally:
        conn.close()

    assert len(drift) == 1
    assert drift[0].request_id == req.id
    assert drift[0].missing_broker_symbols == ["SPY260702C00756000"]


def test_find_local_drift_skips_open_with_filled_close(tmp_path):
    # Once a paired close has filled, the open is properly retired —
    # the absent broker legs are expected, not drift.
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    try:
        open_req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "short_symbol": "SPY260702C00755000",
                "long_symbol": "SPY260702C00756000",
                "legs": [],
            },
        )
        db.update_status(conn, open_req.id, status=db.STATUS_FILLED, reason="filled")

        close_req = db.create_option_spread_close(
            conn,
            symbol="SPY",
            qty=1,
            limit_debit=0.30,
            payload={
                "strategy": "ICL",
                "open_request_id": open_req.id,
                "short_symbol": "SPY260702C00755000",
                "long_symbol": "SPY260702C00756000",
                "legs": [],
            },
        )
        db.update_status(conn, close_req.id, status=db.STATUS_FILLED, reason="filled")

        drift = spread_audit.find_local_drift(conn, broker_symbols=set())
    finally:
        conn.close()

    assert drift == []


def _make_pair(short: str, long: str, *, qty: int = 1) -> spread_audit.Pair:
    return spread_audit.Pair(
        short_symbol=short,
        long_symbol=long,
        underlying="SPY",
        expiration="260619",
        option_type="C",
        short_strike="755",
        long_strike="756",
        width="1",
        qty=qty,
    )


def test_find_unmatched_broker_pairs_when_no_local_open(tmp_path):
    # Broker has a paired vertical, but the local db doesn't track it at all
    # (e.g. opened by hand outside the bot). Reverse drift.
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    pair = _make_pair("SPY260619C00755000", "SPY260619C00756000")
    try:
        unmatched = spread_audit.find_unmatched_broker_pairs(conn, [pair])
    finally:
        conn.close()
    assert unmatched == [pair]


def test_find_unmatched_broker_pairs_when_local_open_is_retired(tmp_path):
    # Local says we closed this spread, but broker still has both legs (qty
    # mismatch, fill bug, or manual re-open). Audit must surface this so the
    # opener doesn't refill "the freed slot".
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    pair = _make_pair("SPY260619C00755000", "SPY260619C00756000")
    try:
        open_req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "short_symbol": pair.short_symbol,
                "long_symbol": pair.long_symbol,
                "legs": [],
            },
        )
        db.update_status(conn, open_req.id, status=db.STATUS_FILLED, reason="filled")
        close_req = db.create_option_spread_close(
            conn,
            symbol="SPY",
            qty=1,
            limit_debit=0.30,
            payload={
                "strategy": "ICL",
                "open_request_id": open_req.id,
                "short_symbol": pair.short_symbol,
                "long_symbol": pair.long_symbol,
                "legs": [],
            },
        )
        db.update_status(conn, close_req.id, status=db.STATUS_FILLED, reason="filled")

        unmatched = spread_audit.find_unmatched_broker_pairs(conn, [pair])
    finally:
        conn.close()

    assert unmatched == [pair]


def test_find_unmatched_broker_pairs_returns_empty_when_active_open_matches(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    pair = _make_pair("SPY260619C00755000", "SPY260619C00756000")
    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "short_symbol": pair.short_symbol,
                "long_symbol": pair.long_symbol,
                "legs": [],
            },
        )
        db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")

        unmatched = spread_audit.find_unmatched_broker_pairs(conn, [pair])
    finally:
        conn.close()

    assert unmatched == []


def test_find_unmatched_broker_pairs_flags_quantity_shortfall(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    pair = _make_pair("SPY260619C00755000", "SPY260619C00756000", qty=2)
    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "short_symbol": pair.short_symbol,
                "long_symbol": pair.long_symbol,
                "legs": [],
            },
        )
        db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")

        unmatched = spread_audit.find_unmatched_broker_pairs(conn, [pair])
    finally:
        conn.close()

    assert unmatched == [pair]


def test_find_unmatched_broker_pairs_sums_active_local_quantity(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    pair = _make_pair("SPY260619C00755000", "SPY260619C00756000", qty=2)
    try:
        for _ in range(2):
            req = db.create_option_spread_open(
                conn,
                symbol="SPY",
                qty=1,
                side="sell",
                limit_credit=0.60,
                payload={
                    "strategy": "ICL",
                    "short_symbol": pair.short_symbol,
                    "long_symbol": pair.long_symbol,
                    "legs": [],
                },
            )
            db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")

        unmatched = spread_audit.find_unmatched_broker_pairs(conn, [pair])
    finally:
        conn.close()

    assert unmatched == []


def test_notify_if_changed_dedupes(tmp_path, monkeypatch):
    sent = []
    audit = spread_audit.SpreadAudit(
        checked_at="2026-06-12T20:00:00+00:00",
        mode="paper",
        broker_position_count=1,
        broker_option_symbol_count=1,
        paired_spreads=[],
        unpaired_legs=[
            spread_audit.UnpairedLeg(
                symbol="SPY260702C00765000",
                underlying="SPY",
                expiration="260702",
                option_type="C",
                strike="765",
                qty=-1,
                side="short",
                market_value="-152",
            )
        ],
        local_drift=[],
        unmatched_broker_pairs=[],
        unparsable_option_symbols=[],
        option_open_risk_usd=152.0,
        position_slots=1,
    )

    monkeypatch.setattr(spread_audit.notify, "send", lambda alert: sent.append(alert) or True)
    state_file = tmp_path / "spread-audit-state.json"

    assert spread_audit.notify_if_changed(audit, state_file=state_file)
    assert not spread_audit.notify_if_changed(audit, state_file=state_file)
    assert len(sent) == 1
    assert sent[0].level == "critical"


def test_notify_if_changed_sends_recovery(tmp_path, monkeypatch):
    sent = []
    state_file = tmp_path / "spread-audit-state.json"
    state_file.write_text('{"fingerprint":"old"}\n')
    audit = spread_audit.SpreadAudit(
        checked_at="2026-06-12T20:00:00+00:00",
        mode="paper",
        broker_position_count=0,
        broker_option_symbol_count=0,
        paired_spreads=[],
        unpaired_legs=[],
        local_drift=[],
        unmatched_broker_pairs=[],
        unparsable_option_symbols=[],
        option_open_risk_usd=0,
        position_slots=0,
    )

    monkeypatch.setattr(spread_audit.notify, "send", lambda alert: sent.append(alert) or True)

    assert spread_audit.notify_if_changed(audit, state_file=state_file)
    assert len(sent) == 1
    assert sent[0].title == "Option spread audit recovered"


def test_notify_if_changed_does_not_dedupe_failed_send(tmp_path, monkeypatch):
    audit = spread_audit.SpreadAudit(
        checked_at="2026-06-12T20:00:00+00:00",
        mode="paper",
        broker_position_count=0,
        broker_option_symbol_count=0,
        paired_spreads=[],
        unpaired_legs=[],
        local_drift=[],
        unmatched_broker_pairs=[],
        unparsable_option_symbols=["BAD"],
        option_open_risk_usd=0,
        position_slots=0,
    )
    state_file = tmp_path / "spread-audit-state.json"
    monkeypatch.setattr(spread_audit.notify, "send", lambda alert: False)

    assert not spread_audit.notify_if_changed(audit, state_file=state_file)
    assert not state_file.exists()
