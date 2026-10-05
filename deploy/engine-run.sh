#!/usr/bin/env bash
# Launch wrapper for the paper-trading engine under systemd.
#
# WHY A WRAPPER INSTEAD OF systemd's EnvironmentFile=
# ---------------------------------------------------
# systemd parses EnvironmentFile with its own rules, which are NOT
# shell rules: it does not run a shell, so quoting, `export`, comments
# after a value, and `$VAR` references inside values are handled
# differently from `source .env`. A token that works when the engine is
# started by hand can silently arrive mangled under systemd. Sourcing
# the same file with the same shell the operator uses removes that
# whole class of difference.
#
# `set -a` exports every variable the file defines, which is what the
# engine expects (it reads os.environ).

set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/opc/Treading-bot-Claude}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

cd "$REPO_DIR"

if [[ ! -f .env ]]; then
    echo "[engine-run] FATAL: $REPO_DIR/.env not found" >&2
    exit 78            # EX_CONFIG -- a config fault, not a crash
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# --max-hours has no "unlimited" value (it is a float with a default of
# 6.0 and the loop always builds a deadline from it). 87600 hours is ten
# years, which is unlimited in practice. The cap is NOT the restart
# mechanism -- systemd is. See trading-engine.service, and note that a
# cap expiry exits with status 0, which is exactly why that unit uses
# Restart=always rather than Restart=on-failure.
exec "$PYTHON_BIN" scripts/run_paper_session.py \
    --universe-mode snapshot \
    --max-hours 87600 \
    --enable-research \
    --no-db-push \
    --skip-confirm
