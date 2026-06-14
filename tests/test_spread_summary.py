from tradebot import api, db, spread_summary


def test_spread_summary_matches_broker_pair_to_local_request(tmp_path):
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
                "direction": "call_credit",
                "expiration_date": "2026-07-02",
                "short_symbol": "SPY260702C00757000",
                "long_symbol": "SPY260702C00758000",
                "legs": [],
            },
        )
        db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")
        summary = spread_summary.build(
            conn,
            mode="paper",
            positions=[
                {
                    "symbol": "SPY260702C00757000",
                    "asset_class": "us_option",
                    "qty": "-1",
                    "market_value": "-32",
                },
                {
                    "symbol": "SPY260702C00758000",
                    "asset_class": "us_option",
                    "qty": "1",
                    "market_value": "0",
                },
            ],
        )
    finally:
        conn.close()

    assert len(summary.open_spreads) == 1
    assert summary.open_spreads[0].request_id == req.id
    assert summary.open_spreads[0].estimated_pnl == "-12.0"
    assert summary.open_spreads[0].current_debit == "0.32"
    assert summary.open_spreads[0].max_loss == "80.0"
    assert summary.estimated_total_pnl == "-12.0"
    assert summary.spread_max_loss_usd == "80.0"


def test_dashboard_renders_open_spread_summary(tmp_path, monkeypatch):
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
                "direction": "call_credit",
                "expiration_date": "2026-07-02",
                "short_symbol": "SPY260702C00757000",
                "long_symbol": "SPY260702C00758000",
                "legs": [],
            },
        )
        db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")
        summary = spread_summary.build(
            conn,
            mode="paper",
            positions=[
                {
                    "symbol": "SPY260702C00757000",
                    "asset_class": "us_option",
                    "qty": "-1",
                    "market_value": "-32",
                },
                {
                    "symbol": "SPY260702C00758000",
                    "asset_class": "us_option",
                    "qty": "1",
                    "market_value": "0",
                },
            ],
        )
        monkeypatch.setattr(api.spread_summary, "run", lambda _conn: summary)
        html = api.render_dashboard(conn)
    finally:
        conn.close()

    assert "Open Spreads" in html
    assert "Open spreads: <strong>1</strong>" in html
    assert "757/758 C" in html
    assert "-$12" in html


def test_spread_summary_marks_pair_broker_only_when_local_open_retired(tmp_path):
    # Reverse drift: local close has filled (open is retired) but broker still
    # has both legs. Summary must NOT match the retired open — it's accounted-for
    # exposure that nothing locally tracks. Surfaces as request_id=None / status=broker_only.
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
                "direction": "call_credit",
                "expiration_date": "2026-07-02",
                "short_symbol": "SPY260702C00757000",
                "long_symbol": "SPY260702C00758000",
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
                "short_symbol": "SPY260702C00757000",
                "long_symbol": "SPY260702C00758000",
                "legs": [],
            },
        )
        db.update_status(conn, close_req.id, status=db.STATUS_FILLED, reason="filled")

        summary = spread_summary.build(
            conn,
            mode="paper",
            positions=[
                {"symbol": "SPY260702C00757000", "asset_class": "us_option", "qty": "-1", "market_value": "-32"},
                {"symbol": "SPY260702C00758000", "asset_class": "us_option", "qty": "1", "market_value": "0"},
            ],
        )
    finally:
        conn.close()

    assert len(summary.open_spreads) == 1
    assert summary.open_spreads[0].request_id is None
    assert summary.open_spreads[0].status == "broker_only"
    assert summary.unmatched_broker_spread_count == 1


def test_spread_summary_marks_pair_broker_only_when_broker_qty_exceeds_local(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    try:
        db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.60,
            payload={
                "strategy": "ICL",
                "direction": "call_credit",
                "expiration_date": "2026-07-02",
                "short_symbol": "SPY260702C00757000",
                "long_symbol": "SPY260702C00758000",
                "legs": [],
            },
        )

        summary = spread_summary.build(
            conn,
            mode="paper",
            positions=[
                {"symbol": "SPY260702C00757000", "asset_class": "us_option", "qty": "-2", "market_value": "-64"},
                {"symbol": "SPY260702C00758000", "asset_class": "us_option", "qty": "2", "market_value": "0"},
            ],
        )
    finally:
        conn.close()

    assert len(summary.open_spreads) == 1
    assert summary.open_spreads[0].request_id is None
    assert summary.open_spreads[0].status == "broker_only"
    assert summary.unmatched_broker_spread_count == 1
