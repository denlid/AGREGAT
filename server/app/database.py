"""
AGREGAT Server — Database Connection

Async SQLAlchemy setup for PostgreSQL.
Compatible with Supabase, Neon, Hetzner VPS — any PostgreSQL.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Verify connections before use
    pool_recycle=3600,   # Recycle connections every hour
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