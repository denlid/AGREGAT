"""
AGREGAT Server — Replay Attack Protection

H-05 FIX: Nonce + timestamp based replay protection for API v1 endpoints.

Security model:
- Each request must include a unique nonce (UUID) and timestamp
- Server rejects requests older than 5 minutes (configurable window)
- Server rejects reused nonces (tracked in Redis for multi-worker support)
- Falls back to in-memory store if Redis unavailable
- Automatic cleanup of expired nonces

Usage in API:
    @router.post("/heartbeat")
    async def heartbeat(req: HeartbeatRequest, request: Request):
        # Validate replay protection
        is_valid, reason = await replay_protector.validate(
            nonce=req.nonce,
            timestamp=req.timestamp,
        )
        if not is_valid:
            raise HTTPException(400, detail=reason)
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger("agregat.server.security.replay")


# ===================================================================
# In-Memory Nonce Store (single-worker fallback)
# ===================================================================

class MemoryNonceStore:
    """In-memory nonce store for single-worker deployments."""
    
    def __init__(self):
        self._nonces: dict[str, float] = {}
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # seconds
    
    def is_duplicate(self, nonce: str, timestamp: float) -> bool:
        """Check if nonce was already used."""
        if nonce in self._nonces:
            return True
        return False
    
    def store(self, nonce: str, timestamp: float) -> None:
        """Store a nonce with its timestamp."""
        self._nonces[nonce] = timestamp
        self._maybe_cleanup()
    
    def _maybe_cleanup(self) -> None:
        """Periodically clean up expired nonces."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        # Remove nonces older than 10 minutes
        cutoff = now - 600
        expired = [n for n, t in self._nonces.items() if t < cutoff]
        for n in expired:
            del self._nonces[n]
        
        self._last_cleanup = now
        logger.debug("Nonce cleanup: removed %d expired nonces", len(expired))
    
    @property
    def size(self) -> int:
        return len(self._nonces)


# ===================================================================
# Redis Nonce Store (multi-worker)
# ===================================================================

class RedisNonceStore:
    """Redis-backed nonce store for multi-worker deployments."""
    
    def __init__(self, redis_client, key_prefix: str = "nonce:", ttl: int = 300):
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl = ttl  # seconds — match the replay window
    
    def is_duplicate(self, nonce: str, timestamp: float) -> bool:
        """Check if nonce exists in Redis."""
        key = f"{self._prefix}{nonce}"
        return self._redis.exists(key)
    
    def store(self, nonce: str, timestamp: float) -> None:
        """Store nonce in Redis with TTL."""
        key = f"{self._prefix}{nonce}"
        self._redis.setex(key, self._ttl, str(timestamp))


# ===================================================================
# Replay Protector
# ===================================================================

class ReplayProtector:
    """
    Protects against replay attacks using nonce + timestamp validation.
    
    Security guarantees:
    1. Requests older than `window_seconds` are rejected (expired)
    2. Reused nonces are rejected (replay detected)
    3. Future timestamps (> 60s ahead) are rejected (clock skew protection)
    
    Args:
        window_seconds: Max age of a valid request (default: 300 = 5 min)
        max_clock_skew: Max allowed future timestamp (default: 60s)
        store: Nonce store (MemoryNonceStore or RedisNonceStore)
    """
    
    def __init__(
        self,
        window_seconds: int = 300,
        max_clock_skew: int = 60,
        store: Optional[MemoryNonceStore | RedisNonceStore] = None,
    ):
        self._window = window_seconds
        self._max_skew = max_clock_skew
        self._store = store or MemoryNonceStore()
        self._stats = {
            "total_checked": 0,
            "rejected_expired": 0,
            "rejected_future": 0,
            "rejected_replay": 0,
            "accepted": 0,
        }
    
    async def validate(
        self,
        nonce: str,
        timestamp: float,
    ) -> Tuple[bool, str]:
        """
        Validate a request's nonce and timestamp.
        
        Returns:
            (is_valid, reason)
        
        Reasons for rejection:
        - "request_expired": Timestamp too old
        - "request_future": Timestamp too far in future
        - "nonce_reused": Nonce already seen (replay attack)
        - "nonce_invalid": Nonce format invalid
        """
        self._stats["total_checked"] += 1
        
        # === Check 1: Nonce format ===
        if not nonce or len(nonce) < 16 or len(nonce) > 128:
            return False, "nonce_invalid"
        
        # === Check 2: Timestamp validity ===
        now = time.time()
        
        # Too old?
        if now - timestamp > self._window:
            self._stats["rejected_expired"] += 1
            logger.warning(
                "Replay: request expired (age: %.0fs, window: %ds)",
                now - timestamp, self._window,
            )
            return False, "request_expired"
        
        # Too far in future? (clock skew attack)
        if timestamp - now > self._max_skew:
            self._stats["rejected_future"] += 1
            logger.warning(
                "Replay: future timestamp (%.0fs ahead, max skew: %ds)",
                timestamp - now, self._max_skew,
            )
            return False, "request_future"
        
        # === Check 3: Nonce uniqueness ===
        if self._store.is_duplicate(nonce, timestamp):
            self._stats["rejected_replay"] += 1
            logger.warning("Replay: duplicate nonce detected — REPLAY ATTACK?")
            return False, "nonce_reused"
        
        # === All checks passed — store nonce ===
        self._store.store(nonce, timestamp)
        self._stats["accepted"] += 1
        
        return True, "ok"
    
    @property
    def stats(self) -> dict:
        """Get validation statistics."""
        return {
            **self._stats,
            "store_size": getattr(self._store, "size", "N/A"),
            "rejection_rate": (
                f"{(self._stats['total_checked'] - self._stats['accepted']) / max(self._stats['total_checked'], 1):.1%}"
            ),
        }


# ===================================================================
# Global Instance
# ===================================================================

_replay_protector: Optional[ReplayProtector] = None


def get_replay_protector(redis_client=None) -> ReplayProtector:
    """Get or create the global replay protector."""
    global _replay_protector
    
    if _replay_protector is None:
        if redis_client:
            store = RedisNonceStore(redis_client)
            logger.info("Replay protection: using Redis store")
        else:
            store = MemoryNonceStore()
            logger.info("Replay protection: using in-memory store")
        
        _replay_protector = ReplayProtector(store=store)
    
    return _replay_protector


__all__ = [
    'ReplayProtector',
    'MemoryNonceStore',
    'RedisNonceStore',
    'get_replay_protector',
]