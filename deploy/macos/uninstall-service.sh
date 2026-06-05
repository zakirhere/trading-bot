#!/usr/bin/env bash
set -euo pipefail

LABEL="com.tradebot.service"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || echo "(was not loaded)"
rm -f "${PLIST_DEST}"
echo "Uninstalled ${LABEL}"
