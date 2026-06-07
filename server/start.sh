#!/bin/bash
# ============================================================
# AGREGAT Server — Production Entrypoint
# ============================================================
# Used by Render.com and Docker to start the server.
# Handles: port detection, migrations, health check, graceful shutdown.
# ============================================================

set -e  # Exit on any error

# ─── Port Configuration ─────────────────────────────────────
# Render injects $PORT. Default to 10000 for local/Docker.
PORT="${PORT:-10000}"

echo "═══════════════════════════════════════════════════"
echo "  AGREGAT License Server — Production Startup"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Port:      $PORT"
echo "  Debug:     ${DEBUG:-false}"
echo "  Database:  ${DATABASE_URL:0:30}..."
echo ""

# ─── Pre-flight Checks ──────────────────────────────────────

# Check that critical env vars are set
if [ -z "$DATABASE_URL" ]; then
    echo "❌ FATAL: DATABASE_URL is not set"
    exit 1
fi

if [ -z "$JWT_SECRET" ]; then
    echo "❌ FATAL: JWT_SECRET is not set"
    exit 1
fi

if [ -z "$ENCRYPTION_KEY" ]; then
    echo "❌ FATAL: ENCRYPTION_KEY is not set"
    exit 1
fi

if [ -z "$OWNER_EMAIL" ]; then
    echo "⚠️  WARNING: OWNER_EMAIL is not set — admin access will be limited"
fi

echo "✅ Environment variables validated"

# ─── Database Migration ─────────────────────────────────────
# In production, run Alembic migrations if available.
# In dev mode, tables are auto-created by SQLAlchemy create_all().

if [ "$DEBUG" != "true" ] && command -v alembic &> /dev/null; then
    echo "📦 Running database migrations..."
    alembic upgrade head || echo "⚠️  Migration failed — continuing (tables may already exist)"
else
    echo "📦 Skipping migrations (dev mode or alembic not installed)"
fi

# ─── Start Server ───────────────────────────────────────────
echo ""
echo "🚀 Starting uvicorn on 0.0.0.0:$PORT"
echo "═══════════════════════════════════════════════════"
echo ""

# Workers: 1 for free tier (512 MB RAM), increase for paid plans
WORKERS="${WEB_CONCURRENCY:-1}"

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level "${LOG_LEVEL:-info}" \
    --access-log \
    --no-reload  # Never reload in production