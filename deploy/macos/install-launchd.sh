#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${PROJECT_ROOT}/deploy/macos/com.tradebot.market-open.plist.template"
PLIST_DEST="${HOME}/Library/LaunchAgents/com.tradebot.market-open.plist"
LABEL="com.tradebot.market-open"

if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  echo "Missing ${PROJECT_ROOT}/.venv/bin/python"
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "Missing ${PROJECT_ROOT}/.env"
  echo "Run: cp .env.example .env, then add Alpaca paper credentials"
  exit 1
fi

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${HOME}/.tradebot"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true

python3 - "${TEMPLATE}" "${PLIST_DEST}" "${PROJECT_ROOT}" "${HOME}" <<'PY'
from pathlib import Path
import sys

template, dest, project_root, home = sys.argv[1:]
text = Path(template).read_text()
text = text.replace("__PROJECT_ROOT__", project_root)
text = text.replace("__HOME__", home)
Path(dest).write_text(text)
PY

plutil -lint "${PLIST_DEST}"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"

echo "Installed dry-run launchd job: ${PLIST_DEST}"
echo "Verify: launchctl print gui/$(id -u)/${LABEL}"
echo "Logs: ${HOME}/.tradebot/launchd-stdout.log and launchd-stderr.log"
