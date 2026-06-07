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
    """Build engine kwargs with SSL enforcement for production."""
    kwargs = {
        "echo": settings.debug,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,   # Verify connections before use
        "pool_recycle": 3600,    # Recycle connections every hour
    }
    
    # P2-4: Enforce SSL in production (unless explicitly disabled)
    is_production = not settings.debug
    ssl_mode = os.environ.get("DB_SSL_MODE", "require" if is_production else "disable")
    
    if ssl_mode != "disable":
        # For asyncpg: connect_args with ssl parameter
        import ssl as _ssl
        ssl_context = _ssl.create_default_context()
        
        # If custom CA cert is provided, use it
        ca_cert = os.environ.get("DB_SSL_CA_CERT")
        if ca_cert and os.path.exists(ca_cert):
            ssl_context.load_verify_locations(ca_cert)
        else:
            # Use system CA certs (works with Supabase, Neon, etc.)
            ssl_context.check_hostname = True
            ssl_context.verify_mode = _ssl.CERT_REQUIRED
        
        # ssl_mode: require | verify-ca | verify-full
        if ssl_mode == "require":
            ssl_context.check_hostname = False
            ssl_context.verify_mode = _ssl.CERT_NONE
        
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