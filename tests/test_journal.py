from tradebot import db, journal


def _conn(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    return conn


def test_journal_renders_option_spread_details(tmp_path):
    conn = _conn(tmp_path)
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
                "expiration_date": "2026-06-30",
                "short_symbol": "SPY260630C00559000",
                "long_symbol": "SPY260630C00560000",
                "legs": [],
                "max_risk": "80.00",
            },
        )
        db.update_status(
            conn,
            req.id,
            status=db.STATUS_SUBMITTED,
            broker_order_id="broker-1",
            reason="new",
        )

        path = journal.sync_from_db(conn, path=tmp_path / "trade-journal.md")

        text = path.read_text()
        assert "# Tradebot Trade Journal" in text
        assert "DCA" in text
        assert "call_credit 2026-06-30 SPY260630C00559000/SPY260630C00560000 credit=0.2" in text
        assert "broker-1" in text
    finally:
        conn.close()
