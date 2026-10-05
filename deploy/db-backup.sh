#!/usr/bin/env bash
# Daily backup of paper_session.sqlite to GitHub, invoked by
# db-backup.timer after the market close (P-052).
#
# The engine KEEPS --no-db-push. This job is the only thing that pushes
# the database, once a day, so a regression to the old ~780 commits a
# trading day is structurally impossible rather than a matter of care.

set -euo pipefail

export PYTHONUNBUFFERED=1

REPO_DIR="${REPO_DIR:-/home/opc/Treading-bot-Claude}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

cd "$REPO_DIR"
mkdir -p logs

if [[ ! -f .env ]]; then
    echo "[db-backup] FATAL: $REPO_DIR/.env not found" >&2
    exit 78
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

exec "$PYTHON_BIN" scripts/backup_db.py
