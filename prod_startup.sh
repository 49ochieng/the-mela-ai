#!/bin/bash
# Mela AI Backend Startup Script
#
# Installs requirements into user site-packages (~/.local) on hash change.
# User site-packages are always in sys.path; no PYTHONPATH manipulation needed.

set -e

WWWROOT=/home/site/wwwroot
REQUIREMENTS="$WWWROOT/requirements-runtime.txt"

if [ ! -f "$REQUIREMENTS" ]; then
    REQUIREMENTS="$WWWROOT/requirements.txt"
fi

REQ_HASH=$(md5sum "$REQUIREMENTS" 2>/dev/null | cut -d' ' -f1 || echo "none")
HASH_FILE="$HOME/.local/.mela_req_hash"

STORED_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "")

if [ "$STORED_HASH" != "$REQ_HASH" ]; then
    echo "[startup] Requirements changed (${STORED_HASH:-none} -> $REQ_HASH). Installing..."
    pip install -r "$REQUIREMENTS" --user --quiet 2>&1
    echo "$REQ_HASH" > "$HASH_FILE"
    echo "[startup] Package installation complete."
else
    echo "[startup] Using cached packages (hash: $REQ_HASH)."
fi

# Run database migrations.
#
# Production prior to alembic adoption created its schema via
# Base.metadata.create_all(), so existing tables block the early CREATE TABLE
# revisions. We detect that state (schema exists but alembic_version is empty)
# and stamp the prior baseline so only NEW revisions actually run.
echo "[startup] Running alembic bootstrap + upgrade..."
cd "$WWWROOT"

python3 - <<'PY' 2>&1 || echo "[startup] WARN: alembic bootstrap step failed — continuing."
import asyncio, os, sys
from sqlalchemy import text
from app.core.database import engine

BASELINE_REV = "002_agent_memory"  # last revision whose tables exist via create_all()

async def main():
    async with engine.begin() as conn:
        # Does alembic_version already track a revision?
        try:
            row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
        except Exception:
            row = None
        if row and row[0]:
            print(f"[startup] alembic_version present: {row[0]} (no stamp needed)")
            return
        # Does the legacy schema exist? Probe a known table.
        try:
            await conn.execute(text("SELECT 1 FROM users"))
            schema_exists = True
        except Exception:
            schema_exists = False
        if not schema_exists:
            print("[startup] No legacy schema — alembic upgrade will create from scratch")
            return
        print(f"[startup] Legacy schema detected — stamping {BASELINE_REV}")
        # Use alembic API for cross-dialect safety
        from alembic.config import Config
        from alembic import command
        cfg = Config("alembic.ini")
        command.stamp(cfg, BASELINE_REV)

asyncio.run(main())
PY

python3 -m alembic upgrade head 2>&1 || {
    echo "[startup] WARN: alembic upgrade failed — continuing anyway. Check column drift."
}

echo "[startup] Starting gunicorn on port ${PORT:-8000}..."
exec python3 -m gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers 1 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -

