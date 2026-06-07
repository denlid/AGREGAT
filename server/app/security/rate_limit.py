"""
AGREGAT Server — Rate Limiting

Sliding window rate limiter with per-endpoint and per-subject limits.
Supports Redis-backed (production) and in-memory (development/fallback) modes.

H-08: Redis-backed rate limiting for multi-worker deployments.
Falls back to in-memory if Redis is unavailable.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: float
    limit: int


@dataclass
class _WindowEntry:
    timestamps: list[float] = field(default_factory=list)


class InMemoryRateLimiter:
    """
    In-memory sliding window rate limiter.
    Only suitable for single-worker deployments.
    """

    def __init__(self):
        self._windows: dict[str, _WindowEntry] = defaultdict(_WindowEntry)

    def _cleanup(self, key: str, window_seconds: float) -> None:
        """Remove expired timestamps from window."""
        entry = self._windows[key]
        cutoff = time.time() - window_seconds
        entry.timestamps = [t for t in entry.timestamps if t > cutoff]

    def check(
        self,
        subject: str,
        endpoint: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Check if request is allowed (does NOT consume)."""
        key = f"{subject}:{endpoint}"
        self._cleanup(key, window_seconds)
        entry = self._windows[key]
        count = len(entry.timestamps)
        remaining = max(0, max_requests - count)
        reset_at = (entry.timestamps[0] + window_seconds) if entry.timestamps else (time.time() + window_seconds)

        return RateLimitResult(
            allowed=remaining > 0,
            remaining=remaining,
            reset_at=reset_at,
            limit=max_requests,
        )

    def consume(
        self,
        subject: str,
        endpoint: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Check AND consume a rate limit token."""
        result = self.check(subject, endpoint, max_requests, window_seconds)
        if result.allowed:
            key = f"{subject}:{endpoint}"
            self._windows[key].timestamps.append(time.time())
            result.remaining = max(0, result.remaining - 1)
        return result

    def reset(self, subject: str, endpoint: str) -> None:
        """Reset rate limit for a subject+endpoint."""
        key = f"{subject}:{endpoint}"
        self._windows.pop(key, None)


class RedisRateLimiter:
    """
    H-08: Redis-backed sliding window rate limiter.
    
    Uses Redis sorted sets for atomic sliding window operations.
    Works across multiple workers/instances.
    
    Falls back to in-memory if Redis connection fails.
    """

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis = None
        self._fallback = InMemoryRateLimiter()
        self._use_fallback = False
        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Redis. Falls back to in-memory on failure."""
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry_on_timeout=True,
                max_connections=10,
            )
            self._use_fallback = False
        except ImportError:
            # redis package not installed — use in-memory
            self._use_fallback = True
            self._redis = None
        except Exception:
            # Connection failed — use in-memory
            self._use_fallback = True
            self._redis = None

    async def _ensure_connected(self) -> None:
        """Verify Redis connection, fall back if broken."""
        if self._use_fallback or self._redis is None:
            return
        try:
            await self._redis.ping()
        except Exception:
            self._use_fallback = True

    def _key(self, subject: str, endpoint: str) -> str:
        return f"rl:{subject}:{endpoint}"

    async def check(
        self,
        subject: str,
        endpoint: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Check if request is allowed (does NOT consume)."""
        await self._ensure_connected()

        if self._use_fallback or self._redis is None:
            return self._fallback.check(subject, endpoint, max_requests, window_seconds)

        key = self._key(subject, endpoint)
        now = time.time()
        cutoff = now - window_seconds

        try:
            # Remove expired entries
            await self._redis.zremrangebyscore(key, "-inf", cutoff)
            # Count remaining
            count = await self._redis.zcard(key)
            remaining = max(0, max_requests - count)

            # Get oldest entry for reset_at calculation
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                reset_at = oldest[0][1] + window_seconds
            else:
                reset_at = now + window_seconds

            return RateLimitResult(
                allowed=remaining > 0,
                remaining=remaining,
                reset_at=reset_at,
                limit=max_requests,
            )
        except Exception:
            self._use_fallback = True
            return self._fallback.check(subject, endpoint, max_requests, window_seconds)

    async def consume(
        self,
        subject: str,
        endpoint: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Check AND consume a rate limit token (atomic)."""
        await self._ensure_connected()

        if self._use_fallback or self._redis is None:
            return self._fallback.consume(subject, endpoint, max_requests, window_seconds)

        key = self._key(subject, endpoint)
        now = time.time()
        cutoff = now - window_seconds

        try:
            # Use Redis pipeline for atomicity
            pipe = self._redis.pipeline(transaction=True)
            pipe.zremrangebyscore(key, "-inf", cutoff)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})  # score=timestamp, member=unique string
            pipe.expire(key, int(window_seconds) + 60)  # auto-cleanup
            results = await pipe.execute()

            count = results[1]  # zcard result (before adding new entry)
            remaining = max(0, max_requests - count - 1)  # -1 for the entry we just added
            allowed = count < max_requests

            if not allowed:
                # Remove the entry we just added (request was rejected)
                await self._redis.zrem(key, str(now))
                remaining = 0

            # Calculate reset_at
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                reset_at = oldest[0][1] + window_seconds
            else:
                reset_at = now + window_seconds

            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                reset_at=reset_at,
                limit=max_requests,
            )
        except Exception:
            self._use_fallback = True
            return self._fallback.consume(subject, endpoint, max_requests, window_seconds)

    async def reset(self, subject: str, endpoint: str) -> None:
        """Reset rate limit for a subject+endpoint."""
        if self._use_fallback or self._redis is None:
            self._fallback.reset(subject, endpoint)
            return

        key = self._key(subject, endpoint)
        try:
            await self._redis.delete(key)
        except Exception:
            pass


# ─── Factory: create appropriate rate limiter based on config ──────────

def _create_rate_limiter():
    """
    H-08: Create Redis-backed rate limiter if REDIS_URL is configured,
    otherwise fall back to in-memory.
    """
    redis_url = settings.redis_url
    if redis_url:
        return RedisRateLimiter(redis_url)
    return InMemoryRateLimiter()


# Global rate limiter instance (auto-selects Redis or in-memory)
rate_limiter = _create_rate_limiter()


def get_rate_limit_settings(endpoint: str) -> tuple[int, float]:
    """Get rate limit settings for an endpoint from config."""
    limits = settings.rate_limits
    if endpoint in limits:
        return limits[endpoint]["max_requests"], limits[endpoint]["window_seconds"]
    # Default limits
    return 60, 60.0  # 60 requests per minute