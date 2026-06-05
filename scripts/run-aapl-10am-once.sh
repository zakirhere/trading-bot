#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tradebot.aapl-10am-once"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

cleanup() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "${PLIST_DEST}"
}
trap cleanup EXIT

cd "${PROJECT_ROOT}"
"${PROJECT_ROOT}/.venv/bin/python" -m tradebot.daemon --no-server --symbol AAPL --qty 1
