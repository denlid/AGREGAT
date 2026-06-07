"""
AGREGAT Server — Database Connection

Async SQLAlchemy setup for PostgreSQL.
Compatible with Supabase, Neon, Hetzner VPS — any PostgreSQL.

P2-4: SSL enforcement for production database connections.
"""

import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# ─── P2-4: Database SSL Configuration ─────────────────────────────────
# In production, enforce SSL to prevent MITM attacks on DB traffic.
# Supabase/Neon provide SSL by default; self-hosted should configure certs.

def _get_engine_kwargs():
    """Build engine kwargs with SSL enforcement for production.
    
    CRIT-03: SSL mode 'require' with CERT_NONE is a MITM vulnerability.
    In production, default to 'verify-full' with CERT_REQUIRED.
    The 'require' mode (no cert validation) is blocked in production.
    """
    import ssl as _ssl
    
    kwargs = {
        "echo": settings.debug,
        "pool_size": 20,          # MED-10: explicit pool size
        "max_overflow": 10,       # MED-10: explicit max overflow
        "pool_pre_ping": True,    # Verify connections before use
        "pool_recycle": 3600,     # Recycle connections every hour
    }
    
    is_production = not settings.debug
    ssl_mode = os.environ.get("DB_SSL_MODE", "verify-full" if is_production else "disable")
    
    # CRIT-03: Block 'require' mode in production (MITM vulnerability)
    if is_production and ssl_mode == "require":
        import sys
        print("FATAL: DB_SSL_MODE='require' is not allowed in production (MITM risk).", file=sys.stderr)
        print("Use 'verify-full' (default) or 'verify-ca' with DB_SSL_CA_CERT.", file=sys.stderr)
        sys.exit(1)
    
    if ssl_mode == "disable":
        return kwargs
    
    # Build SSL context
    ssl_context = _ssl.create_default_context()
    
    if ssl_mode == "verify-full":
        # Full verification: cert + hostname (most secure)
        ca_cert = os.environ.get("DB_SSL_CA_CERT")
        if ca_cert and os.path.exists(ca_cert):
            ssl_context.load_verify_locations(ca_cert)
        # else: use system CA certs (Supabase, Neon, AWS RDS, etc.)
        ssl_context.check_hostname = True
        ssl_context.verify_mode = _ssl.CERT_REQUIRED
    
    elif ssl_mode == "verify-ca":
        # Verify cert but not hostname (for internal networks)
        ca_cert = os.environ.get("DB_SSL_CA_CERT")
        if ca_cert and os.path.exists(ca_cert):
            ssl_context.load_verify_locations(ca_cert)
        elif is_production:
            import sys
            print("FATAL: DB_SSL_MODE='verify-ca' requires DB_SSL_CA_CERT in production.", file=sys.stderr)
            sys.exit(1)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = _ssl.CERT_REQUIRED
    
    else:
        # Unknown ssl_mode — fail closed
        import sys
        print(f"FATAL: Unknown DB_SSL_MODE='{ssl_mode}'. Use: disable, verify-ca, verify-full", file=sys.stderr)
        sys.exit(1)
    
    kwargs["connect_args"] = {"ssl": ssl_context}
    return kwargs


# Create async engine with SSL (P2-4)
engine = create_async_engine(
    settings.database_url,
    **_get_engine_kwargs(),
)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


async def get_db() -> AsyncSession:
    """
    Dependency for FastAPI routes.
    Provides a database session that auto-closes after request.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database (create tables).
    Only used for local development.
    In production, use migrations/init.sql or Alembic.
    """
    async with engine.begin() as conn:
        # Import all models so they're registered with Base
        from app.models import user, device, license, session, entitlement, strategy, audit  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close database connection pool."""
    await engine.dispose()