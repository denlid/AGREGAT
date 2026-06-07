"""
AGREGAT Request Signing Module

E2E-01: HMAC request signing with nonce-based replay prevention.

Every client-to-server request is signed with HMAC-SHA256 using a shared
secret derived from the session token. The server verifies the signature
and rejects requests with:
- Invalid signature (tampered payload)
- Expired timestamp (>30s clock skew)
- Reused nonce (replay attack)

This prevents:
- MITM attacks (even with TLS, defense-in-depth)
- Replay attacks (captured requests can't be replayed)
- Request tampering (any modification invalidates signature)

Architecture:
  Client: HMAC-SHA256(session_secret, method + path + timestamp + nonce + body_hash)
  Server: Verifies signature, checks timestamp freshness, validates nonce uniqueness
"""

import hashlib
import hmac
import os
import time
import secrets
from typing import Optional


# ─── Nonce Management ──────────────────────────────────────────────────

class NonceManager:
    """
    Manages nonces for replay attack prevention.
    
    Each request gets a unique 16-byte random nonce.
    Used nonces are tracked locally to prevent accidental reuse.
    Server-side, nonces are stored in Redis with 60s TTL.
    """
    
    def __init__(self, max_cached: int = 1000):
        self._used_nonces: list[str] = []
        self._max_cached = max_cached
    
    def generate(self) -> str:
        """Generate a unique nonce (16 bytes hex)."""
        nonce = secrets.token_hex(16)
        
        # Track locally to prevent accidental reuse
        self._used_nonces.append(nonce)
        if len(self._used_nonces) > self._max_cached:
            # Keep only the most recent nonces
            self._used_nonces = self._used_nonces[-self._max_cached // 2:]
        
        return nonce
    
    def is_used(self, nonce: str) -> bool:
        """Check if nonce was already used (client-side check)."""
        return nonce in self._used_nonces


# Global nonce manager
_nonce_manager = NonceManager()


def generate_nonce() -> str:
    """Generate a unique nonce for a request."""
    return _nonce_manager.generate()


# ─── Request Signing ──────────────────────────────────────────────────

def derive_signing_key(session_token: str, device_fingerprint: str) -> bytes:
    """
    Derive HMAC signing key from session token + device fingerprint.
    
    The signing key is unique per session AND device, so even if a token
    is stolen, it can't be used from a different device.
    
    Uses HKDF-like derivation:
      signing_key = HMAC-SHA256(device_fingerprint, session_token)
    """
    return hmac.new(
        device_fingerprint.encode(),
        session_token.encode(),
        hashlib.sha256,
    ).digest()


def sign_request(
    method: str,
    path: str,
    body: Optional[str],
    signing_key: bytes,
    timestamp: Optional[int] = None,
    nonce: Optional[str] = None,
) -> dict:
    """
    Sign an HTTP request with HMAC-SHA256.
    
    Returns dict with headers to add to the request:
      X-Agreqat-Signature: base64(HMAC)
      X-Agreqat-Timestamp: unix timestamp
      X-Agreqat-Nonce: hex nonce
    
    Signature covers:
      METHOD + PATH + TIMESTAMP + NONCE + BODY_HASH
    
    This ensures the entire request is tamper-proof.
    """
    if timestamp is None:
        timestamp = int(time.time())
    if nonce is None:
        nonce = generate_nonce()
    
    # Hash the body (empty string for GET requests)
    body_hash = hashlib.sha256((body or "").encode()).hexdigest()
    
    # Build the signing string
    signing_string = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
    
    # Compute HMAC-SHA256
    signature = hmac.new(signing_key, signing_string.encode(), hashlib.sha256).hexdigest()
    
    return {
        "X-Agreqat-Signature": signature,
        "X-Agreqat-Timestamp": str(timestamp),
        "X-Agreqat-Nonce": nonce,
    }


def verify_request_signature(
    method: str,
    path: str,
    body: Optional[str],
    signing_key: bytes,
    signature: str,
    timestamp: int,
    nonce: str,
    max_clock_skew: int = 30,
) -> tuple[bool, str]:
    """
    Verify an incoming request signature.
    
    Returns (is_valid, reason).
    
    Checks:
    1. Timestamp freshness (within max_clock_skew seconds)
    2. Nonce uniqueness (not seen before — caller must check Redis)
    3. HMAC signature validity
    
    SECURITY: Uses constant-time comparison for HMAC.
    """
    # Check timestamp freshness
    now = int(time.time())
    if abs(now - timestamp) > max_clock_skew:
        return False, f"timestamp_expired (skew={abs(now - timestamp)}s, max={max_clock_skew}s)"
    
    # Rebuild signing string
    body_hash = hashlib.sha256((body or "").encode()).hexdigest()
    signing_string = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
    
    # Compute expected signature
    expected = hmac.new(signing_key, signing_string.encode(), hashlib.sha256).hexdigest()
    
    # Constant-time comparison
    if not hmac.compare_digest(signature, expected):
        return False, "signature_mismatch"
    
    return True, "ok"


# ─── Server-Side Nonce Tracking (Redis) ───────────────────────────────

async def check_and_store_nonce(redis_client, nonce: str, ttl: int = 60) -> bool:
    """
    Check if nonce was already used and store it if not.
    
    Uses Redis SET NX (set if not exists) for atomic check-and-set.
    Returns True if nonce is fresh (not seen before).
    Returns False if nonce was already used (replay attack).
    
    Call this BEFORE processing the request.
    """
    key = f"nonce:{nonce}"
    # SET NX returns True if key was set (nonce is fresh)
    result = await redis_client.set(key, "1", ex=ttl, nx=True)
    return bool(result)


# ─── Convenience: Sign a full request dict ─────────────────────────────

def sign_api_request(
    method: str,
    endpoint: str,
    payload: Optional[dict],
    session_token: str,
    device_fingerprint: str,
) -> dict:
    """
    Convenience function to sign an API request.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: API path (e.g., "/api/v1/heartbeat")
        payload: Request body dict (or None for GET)
        session_token: Current access token
        device_fingerprint: Device fingerprint
    
    Returns:
        dict with X-Agreqat-* headers to add to the request
    """
    signing_key = derive_signing_key(session_token, device_fingerprint)
    body = __import__("json").dumps(payload, sort_keys=True) if payload else None
    return sign_request(method, endpoint, body, signing_key)