#!/usr/bin/env bash
# Install the market-open launchd job for one-shot dry-run today (6:30 AM PDT = 9:30 ET).
# Calendar interval is pinned to Month=6 Day=5 so it only matches June 5 of any year —
# effectively one-shot for our purposes.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="${PROJECT_ROOT}/launchd/com.tradebot.market-open.plist"
PLIST_DEST="${HOME}/Library/LaunchAgents/com.tradebot.market-open.plist"
LABEL="com.tradebot.market-open"

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${HOME}/.tradebot"

# Bootout existing if loaded (idempotent).
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true

cp "${PLIST_SRC}" "${PLIST_DEST}"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"

echo "Installed: ${PLIST_DEST}"
echo
echo "Next scheduled fire (per launchd):"
launchctl print "gui/$(id -u)/${LABEL}" | grep -E "next run time|state|last exit code|stdout|stderr" || true
echo
echo "Logs:"
echo "  stdout: ${HOME}/.tradebot/launchd-stdout.log"
echo "  stderr: ${HOME}/.tradebot/launchd-stderr.log"
echo
echo "This scheduled job is DRY-RUN only; it will not submit an order."
echo "Account check:       ${PROJECT_ROOT}/.venv/bin/python -m tradebot.daemon --account-check"
echo "Halt before fire:    ${PROJECT_ROOT}/.venv/bin/python -m tradebot.daemon --halt 'reason'"
echo "Manual paper order:  ${PROJECT_ROOT}/.venv/bin/python -m tradebot.daemon --no-server"
echo "Uninstall:           ${PROJECT_ROOT}/scripts/uninstall-launchd.sh"
