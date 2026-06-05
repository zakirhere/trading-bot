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
