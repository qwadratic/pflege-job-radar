#!/usr/bin/env bash
# tools/wa_bridge.py с окружением сервиса. Без WA_AUTOSEND/WA_TRANSPORT/WA_BRIDGE_URL
# CLI не падает, а тихо пишет черновики и рапортует успех (замерено: AUTOSEND False,
# TRANSPORT meta, BRAIN deterministic). Без set -e: в .env есть незакавыченное
# значение (KNOWN_PHONES_SOURCE_QUERY), хвост которого bash пытается выполнить.
set -uo pipefail
cd /home/claude/repo/pflege-board
set -a
. ./.env 2>/dev/null
. /home/claude/.local/state/pflege-wa-bridge/rail.env 2>/dev/null
set +a
exec .venv/bin/python tools/wa_bridge.py "$@"
