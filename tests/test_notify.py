from tradebot import config, notify


def test_send_noops_when_not_configured():
    cfg = config.NotifyConfig(provider="", slack_webhook_url=None)

    sent = notify.send(notify.Alert(level="info", title="Test", message="Hello"), cfg)

    assert not sent


def test_send_posts_slack_payload(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, *, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    cfg = config.NotifyConfig(
        provider="slack",
        slack_webhook_url="https://hooks.slack.com/services/T000/B000/abc",
    )

    sent = notify.send(
        notify.Alert(
            level="trade",
            title="Order submitted",
            message="Submitted market buy 10 NIO.",
            fields={"symbol": "NIO", "qty": 10},
        ),
        cfg,
    )

    assert sent
    assert calls == [
        {
            "url": "https://hooks.slack.com/services/T000/B000/abc",
            "json": {
                "text": (
                    "*TRADE* Order submitted\n"
                    "Submitted market buy 10 NIO.\n"
                    "*symbol:* NIO\n"
                    "*qty:* 10"
                )
            },
            "timeout": 10.0,
        }
    ]
