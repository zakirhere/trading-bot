#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.tradebot.market-open"

echo "Repo:"
git -C "${PROJECT_ROOT}" status --short --branch

echo
echo "Bot state:"
"${PROJECT_ROOT}/.venv/bin/python" -m tradebot.daemon --status

echo
echo "launchd:"
launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null || echo "not loaded"
