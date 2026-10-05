#!/usr/bin/env bash
# Daily D-0026 universe selection run, invoked by universe-refresh.timer.
#
# This is the job that turns "the engine is running" into "the system is
# running". The engine only ever READS snapshots -- nothing in src/engine
# writes one -- and SnapshotUniverseSource looks up TODAY's US-Eastern
# trading date only, never reusing yesterday's. So a day without this
# run is a day with no universe, and since D-0054 removed the fallback
# watchlist that means a day with no new trades at all.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/opc/Treading-bot-Claude}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

cd "$REPO_DIR"

if [[ ! -f .env ]]; then
    echo "[universe-refresh] FATAL: $REPO_DIR/.env not found" >&2
    exit 78
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# --whitelist is deliberately NOT passed: every stage of the D-0026
# pipeline is percentile-based, so it needs the whole tradable universe
# as its candidate pool. Running it against a handful of symbols
# produces zero or near-zero survivors -- that is expected behavior, not
# a bug, and it is why this job must stay un-whitelisted.
#
# Leveraged and inverse products are excluded inside the provider
# (D-0056), so DXD and its family never reach the pipeline from here.
exec "$PYTHON_BIN" scripts/run_universe_selection.py --send-telegram
