from tradebot import api, db


def test_nl_api_creates_request(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    try:
        req = db.create_stock_market_buy(conn, symbol="TSLA", qty=1)
        html = api.render_dashboard(conn, message=f"Queued request #{req.id}")
    finally:
        conn.close()

    assert "Queued request #1" in html
    assert "TSLA" in html


def test_dashboard_shows_strategy_spreads(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    try:
        db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.20,
            payload={
                "strategy": "DCA",
                "direction": "call_credit",
                "expiration_date": "2026-07-02",
                "short_symbol": "SPY260702C00765000",
                "long_symbol": "SPY260702C00766000",
                "legs": [],
            },
        )
        html = api.render_dashboard(conn)
    finally:
        conn.close()

    assert "Strategy Spreads" in html
    assert "DCA" in html
    assert "SPY260702C00765000/SPY260702C00766000" in html
