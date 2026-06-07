"""
AGREGAT Server — Ed25519 License Receipt Signing (C-06)

Replaces weak XOR encryption with cryptographic signatures.

How it works:
1. Server generates an Ed25519 keypair (private key stays on server)
2. After successful activation, server signs the license data
3. Desktop client verifies the signature using the embedded public key
4. Tampered receipts are immediately detected

This prevents:
- License spoofing (cannot forge a valid receipt without private key)
- Replay attacks (receipt includes timestamp + device fingerprint)
- MITM tampering (signature covers all receipt fields)
"""

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# ─── Key Management ───────────────────────────────────────────────────

_KEY_DIR = Path(__file__).parent.parent.parent / "keys"
_PRIVATE_KEY_PATH = _KEY_DIR / "license_signing_key.pem"
_PUBLIC_KEY_PATH = _KEY_DIR / "license_signing_key.pub"


def _ensure_key_dir() -> None:
    """Create keys directory if it doesn't exist."""
    _KEY_DIR.mkdir(parents=True, exist_ok=True)


def generate_signing_keypair() -> tuple[str, str]:
    """
    Generate a new Ed25519 keypair for license receipt signing.
    Returns (private_key_pem, public_key_pem).
    
    Run this ONCE during server setup:
        python -c "from app.security.signing import generate_signing_keypair; generate_signing_keypair()"
    """
    _ensure_key_dir()
    
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.PKCS8,
        encryption_algorithm=__import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption(),
    )
    
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.SubjectPublicKeyInfo,
    )
    
    _PRIVATE_KEY_PATH.write_bytes(private_pem)
    os.chmod(_PRIVATE_KEY_PATH, 0o600)  # Owner read/write only
    _PUBLIC_KEY_PATH.write_bytes(public_pem)
    
    return private_pem.decode(), public_pem.decode()


def load_private_key() -> Ed25519PrivateKey:
    """Load the Ed25519 private key for signing."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    
    if not _PRIVATE_KEY_PATH.exists():
        raise FileNotFoundError(
            f"Signing key not found at {_PRIVATE_KEY_PATH}. "
            "Run: python scripts/generate_signing_key.py"
        )
    
    pem = _PRIVATE_KEY_PATH.read_bytes()
    return load_pem_private_key(pem, password=None)


def load_public_key() -> Ed25519PublicKey:
    """Load the Ed25519 public key for verification."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    
    if not _PUBLIC_KEY_PATH.exists():
        raise FileNotFoundError(f"Public key not found at {_PUBLIC_KEY_PATH}")
    
    pem = _PUBLIC_KEY_PATH.read_bytes()
    return load_pem_public_key(pem)


def get_public_key_base64() -> str:
    """
    Get the public key as base64 string (for embedding in desktop client).
    This should be embedded in the compiled binary at build time.
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )
    
    public_key = load_public_key()
    raw_bytes = public_key.public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return base64.b64encode(raw_bytes).decode()


# ─── Signing ──────────────────────────────────────────────────────────

def create_license_receipt(
    user_id: str,
    license_tier: str,
    device_fingerprint: str,
    entitlements: list[dict],
    expires_at: str,
    session_id: str,
) -> dict:
    """
    Create a signed license receipt.
    
    The receipt contains all license data + a cryptographic signature.
    The desktop client verifies this signature to detect tampering.
    
    Returns:
        {
            "user_id": "...",
            "license_tier": "...",
            "device_fingerprint": "...",
            "entitlements": [...],
            "expires_at": "...",
            "session_id": "...",
            "issued_at": "...",
            "signature": "base64-encoded Ed25519 signature"
        }
    """
    issued_at = datetime.now(timezone.utc).isoformat()
    
    # Build the payload (deterministic JSON for consistent hashing)
    payload = {
        "user_id": user_id,
        "license_tier": license_tier,
        "device_fingerprint": device_fingerprint,
        "entitlements": entitlements,
        "expires_at": expires_at,
        "session_id": session_id,
        "issued_at": issued_at,
    }
    
    # Canonical JSON (sorted keys, no whitespace)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    
    # Sign the canonical JSON
    private_key = load_private_key()
    signature = private_key.sign(canonical.encode())
    
    # Add signature to receipt
    payload["signature"] = base64.b64encode(signature).decode()
    
    return payload


def verify_license_receipt(receipt: dict, public_key_b64: str) -> bool:
    """
    Verify a license receipt signature.
    Used by desktop client to detect tampered receipts.
    
    Args:
        receipt: The receipt dict (must contain "signature" key)
        public_key_b64: Base64-encoded Ed25519 public key (embedded in client)
    
    Returns:
        True if signature is valid, False otherwise
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_der_public_key,
    )
    
    # Extract signature
    signature_b64 = receipt.get("signature")
    if not signature_b64:
        return False
    
    try:
        signature = base64.b64decode(signature_b64)
        
        # Load public key from base64
        raw_key = base64.b64decode(public_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(raw_key)
        
        # Rebuild canonical payload (without signature)
        payload = {k: v for k, v in receipt.items() if k != "signature"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        
        # Verify signature
        public_key.verify(signature, canonical.encode())
        return True
        
    except Exception:
        return False


def verify_receipt_on_server(receipt: dict) -> bool:
    """Verify a license receipt using the server's public key."""
    public_key = load_public_key()
    
    signature_b64 = receipt.get("signature")
    if not signature_b64:
        return False
    
    try:
        signature = base64.b64decode(signature_b64)
        payload = {k: v for k, v in receipt.items() if k != "signature"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        public_key.verify(signature, canonical.encode())
        return True
    except Exception:
        return False