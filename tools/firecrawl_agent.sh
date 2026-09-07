#!/usr/bin/env bash
# Wrapper around `npx firecrawl-cli agent`, gated by the SAME spend gate + 24h kill switch the web app
# uses (app/crawl.py spend_gate()/kill_switch(), via python -m app.firecrawl_gate). See docs/firecrawl.md.
#
# Usage:
#   tools/firecrawl_agent.sh <clinic_id> "<prompt>" [--urls a,b] [--schema-file f.json] [extra firecrawl-cli agent args...]
#
# The wrapper always adds --max-credits itself (computed by the gate) and --wait --json; do not pass
# --max-credits or --browser yourself. FIRECRAWL_API_KEY must already be in the environment
# (cd repo && set -a && . ./.env && set +a).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

CLINIC_ID="${1:?usage: tools/firecrawl_agent.sh <clinic_id> \"<prompt>\" [extra firecrawl-cli agent args...]}"
PROMPT="${2:?prompt required}"
shift 2

: "${FIRECRAWL_API_KEY:?FIRECRAWL_API_KEY not set -- run: set -a; . ./.env; set +a}"

PY="${PYTHON:-.venv/bin/python}"
GATE_OUT="$("$PY" -m app.firecrawl_gate "$CLINIC_ID" 2>&1)"
GATE_STATUS=$?
if [ "$GATE_STATUS" -ne 0 ]; then
    echo "firecrawl_gate refused clinic $CLINIC_ID:" >&2
    echo "$GATE_OUT" >&2
    exit 1
fi
CAP="$(echo "$GATE_OUT" | tail -n1)"
echo "spend gate: up to $CAP credits allowed for clinic $CLINIC_ID" >&2

exec npx -y firecrawl-cli@latest agent "$PROMPT" --max-credits "$CAP" --wait --json "$@"
