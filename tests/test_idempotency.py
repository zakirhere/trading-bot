from datetime import date

from tradebot import idempotency


def test_key_shape():
    k = idempotency.make_key(
        "plumbing_test", "NIO", {"action": "buy", "qty": 10}, date(2026, 6, 5)
    )
    assert k.startswith("plumbing_test_NIO_2026-06-05_")
    assert len(k.rsplit("_", 1)[-1]) == 8


def test_key_deterministic():
    intent = {"action": "buy", "qty": 10}
    a = idempotency.make_key("s", "X", intent, date(2026, 6, 5))
    b = idempotency.make_key("s", "X", intent, date(2026, 6, 5))
    assert a == b


def test_key_different_intent():
    a = idempotency.make_key("s", "X", {"qty": 10}, date(2026, 6, 5))
    b = idempotency.make_key("s", "X", {"qty": 11}, date(2026, 6, 5))
    assert a != b


def test_key_different_date():
    intent = {"qty": 10}
    a = idempotency.make_key("s", "X", intent, date(2026, 6, 5))
    b = idempotency.make_key("s", "X", intent, date(2026, 6, 6))
    assert a != b


def test_is_processed():
    assert idempotency.is_processed("a", ["a", "b"])
    assert not idempotency.is_processed("c", ["a", "b"])
    assert not idempotency.is_processed("a", [])
