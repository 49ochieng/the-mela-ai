#!/bin/bash
# Mela AI Backend Startup Script
#
# Installs requirements into user site-packages (~/.local) on hash change.
# User site-packages are always in sys.path, so system python3 + gunicorn
# resolve the app's dependencies without any PYTHONPATH manipulation.
#
# NOTE: Do NOT rely on an Oryx-built antenv — SCM_DO_BUILD_DURING_DEPLOYMENT
# is disabled, so on a fresh App Service Plan there is no antenv and the
# only dependencies present are whatever this script installs. A prior
# refactor that depended on antenv left the container running bare system
# python (no uvicorn/alembic) → exit 1. The --user install below self-heals.

set -e

WWWROOT=/home/site/wwwroot
REQUIREMENTS="$WWWROOT/requirements-runtime.txt"

if [ ! -f "$REQUIREMENTS" ]; then
    REQUIREMENTS="$WWWROOT/requirements.txt"
fi

echo "[startup] Python runtime: $(python3 --version 2>/dev/null || echo unknown)"

# ---------------------------------------------------------------------------
# Dependency install (hash-cached). User site-packages survive across
# restarts on the same plan; a requirements change re-installs.
# ---------------------------------------------------------------------------
REQ_HASH=$(md5sum "$REQUIREMENTS" 2>/dev/null | cut -d' ' -f1 || echo "none")
HASH_FILE="$HOME/.local/.mela_req_hash"
STORED_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "")

if [ "$STORED_HASH" != "$REQ_HASH" ]; then
    echo "[startup] Requirements changed (${STORED_HASH:-none} -> $REQ_HASH). Installing..."
    python3 -m pip install -r "$REQUIREMENTS" --user --quiet 2>&1
    echo "$REQ_HASH" > "$HASH_FILE"
    echo "[startup] Package installation complete."
else
    echo "[startup] Using cached packages (hash: $REQ_HASH)."
fi

# ---------------------------------------------------------------------------
# Database migrations.
#
# Production prior to alembic adoption created its schema via
# Base.metadata.create_all(), so existing tables block the early CREATE TABLE
# revisions. We detect that state (schema exists but alembic_version empty)
# and stamp the prior baseline so only NEW revisions actually run.
# All alembic steps are non-fatal — a migration hiccup must not block boot.
# ---------------------------------------------------------------------------
cd "$WWWROOT"
echo "[startup] Running alembic bootstrap + upgrade..."

NEEDS_STAMP=$(python3 - <<'PY'
import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.begin() as conn:
        try:
            row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
            if row and row[0]:
                print("no")
                return
        except Exception:
            pass
        try:
            await conn.execute(text("SELECT 1 FROM users"))
            print("yes")
        except Exception:
            print("no")

asyncio.run(main())
PY
) || {
    echo "[startup] WARN: alembic pre-check failed; skipping legacy stamp."
    NEEDS_STAMP="no"
}

if [ "$NEEDS_STAMP" = "yes" ]; then
    echo "[startup] Legacy schema detected — stamping 002_agent_memory"
    python3 -m alembic stamp 002_agent_memory 2>&1 || \
        echo "[startup] WARN: alembic stamp failed — continuing."
else
    echo "[startup] alembic_version state OK (NEEDS_STAMP='$NEEDS_STAMP')"
fi

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
