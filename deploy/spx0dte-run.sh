#!/usr/bin/env bash
# Launch wrapper for the SPX 0DTE session runner under systemd.
# Mirrors engine-run.sh's own reasoning exactly (same .env sourcing
# approach, same PYTHONUNBUFFERED fix) -- a SEPARATE unit/wrapper from
# the equity-ladder engine's, not a modification of it.

set -euo pipefail

export PYTHONUNBUFFERED=1

REPO_DIR="${REPO_DIR:-/home/opc/Treading-bot-Claude}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
# Controller's own request, 2026-11-19/22: the live dashboard (price
# chart, manual "Sell Now" button) runs inside this same process via
# --dashboard-port, so a systemd-managed session serves it exactly like
# the manual nohup sessions did -- not a reduced, dashboard-less mode.
# PUBLIC_HOST defaults to the VM's own known public IP (confirmed via
# `curl ifconfig.me` on this same VM, 2026-11-19); override via the
# unit's Environment= if it ever changes.
DASHBOARD_PORT="${DASHBOARD_PORT:-8787}"
PUBLIC_HOST="${PUBLIC_HOST:-130.162.190.89}"
STATUS_FILE="${STATUS_FILE:-spx0dte_status.json}"

cd "$REPO_DIR"
mkdir -p logs

if [[ ! -f .env ]]; then
    echo "[spx0dte-run] FATAL: $REPO_DIR/.env not found" >&2
    exit 78            # EX_CONFIG -- a config fault, not a crash
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# --max-hours has no "unlimited" value, same reasoning as
# engine-run.sh: the cap is not the restart mechanism, systemd is
# (Restart=always). The runner's own loop already sleeps through
# closed-market hours rather than exiting, so a very long cap just
# means systemd, not this script, decides when the process restarts.
exec "$PYTHON_BIN" scripts/run_spx0dte_session.py \
    --max-hours 87600 \
    --tick-seconds 60 \
    --status-file "$STATUS_FILE" \
    --dashboard-port "$DASHBOARD_PORT" \
    --public-host "$PUBLIC_HOST"
