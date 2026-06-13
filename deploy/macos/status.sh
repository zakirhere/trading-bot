#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TIMER_LABEL="com.tradebot.market-open"
SERVICE_LABEL="com.tradebot.service"
AUDIT_LABEL="com.tradebot.spread-audit"

echo "Repo:"
git -C "${PROJECT_ROOT}" status --short --branch

echo
echo "Bot state:"
"${PROJECT_ROOT}/.venv/bin/python" -m tradebot.daemon --status

echo
echo "launchd timer:"
launchctl print "gui/$(id -u)/${TIMER_LABEL}" 2>/dev/null || echo "not loaded"

echo
echo "launchd service:"
launchctl print "gui/$(id -u)/${SERVICE_LABEL}" 2>/dev/null || echo "not loaded"

echo
echo "spread audit service:"
launchctl print "gui/$(id -u)/${AUDIT_LABEL}" 2>/dev/null || echo "not loaded"
