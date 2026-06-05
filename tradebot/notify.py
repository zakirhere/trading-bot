from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    level: str
    title: str
    message: str
    fields: dict[str, Any] | None = None


def _format_alert(alert: Alert) -> str:
    parts = [f"*{alert.level.upper()}* {alert.title}", alert.message]
    if alert.fields:
        field_lines = [f"*{k}:* {v}" for k, v in alert.fields.items()]
        parts.append("\n".join(field_lines))
    return "\n".join(part for part in parts if part)


def send(alert: Alert, cfg: config.NotifyConfig | None = None) -> bool:
    cfg = cfg or config.load_notify_config()
    if not cfg.enabled:
        log.debug("notification skipped: provider not configured")
        return False

    assert cfg.slack_webhook_url is not None
    payload = {"text": _format_alert(alert)}
    try:
        r = httpx.post(cfg.slack_webhook_url, json=payload, timeout=10.0)
        r.raise_for_status()
        return True
    except Exception:
        log.exception("slack notification failed")
        return False
