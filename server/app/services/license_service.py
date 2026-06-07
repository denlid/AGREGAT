"""
AGREGAT Server — License Service

Business logic for license management: creation, activation, revocation,
entitlement management, and device binding.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.license import License
from app.models.device import Device
from app.models.session import Session
from app.models.entitlement import Entitlement
from app.security.jwt import (
    generate_access_token,
    generate_refresh_token,
    hash_refresh_token,
    get_token_expiry,
    get_refresh_token_expiry,
)
from app.security.signing import create_license_receipt
from app.security.fingerprint import hash_fingerprint
from app.services.audit import log_event


def generate_activation_key() -> str:
    """Generate a secure activation key (AGR-XXXX-XXXX-XXXX-XXXX format)."""
    prefix = "AGR"
    parts = [secrets.token_hex(4).upper() for _ in range(4)]
    return f"{prefix}-{'-'.join(parts)}"


def hash_activation_key(key: str, pepper: str = "") -> str:
    """
    MH-08: Hash activation key with server-side pepper (HMAC-SHA256).
    
    SECURITY: Activation keys are stored as HMAC(pepper, key) hashes.
    If the database is compromised, attackers cannot brute-force keys
    without also knowing the pepper (stored in .env, not in DB).
    
    The plaintext key is only shown once to the admin at creation time.
    
    When pepper is empty (dev/legacy), falls back to plain SHA-256.
    """
    normalized = key.strip().upper()
    if pepper:
        # HMAC-SHA256 with pepper as key
        import hmac as _hmac
        return _hmac.new(
            pepper.encode(),
            normalized.encode(),
            hashlib.sha256,
        ).hexdigest()
    # Legacy/dev: plain SHA-256 (no pepper)
    return hashlib.sha256(normalized.encode()).hexdigest()


# Default entitlements per tier
TIER_ENTITLEMENTS: dict[str, list[dict]] = {
    "beta": [
        {"capability": "backtest", "limit_value": None},
        {"capability": "chart_render", "limit_value": None},
        {"capability": "strategy_count", "limit_value": 5},
    ],
    "pro": [
        {"capability": "backtest", "limit_value": None},
        {"capability": "chart_render", "limit_value": None},
        {"capability": "strategy_count", "limit_value": 50},
        {"capability": "optimizer", "limit_value": None},
        {"capability": "monte_carlo", "limit_value": None},
        {"capability": "report_export", "limit_value": None},
    ],
    "enterprise": [
        {"capability": "backtest", "limit_value": None},
        {"capability": "chart_render", "limit_value": None},
        {"capability": "strategy_count", "limit_value": None},
        {"capability": "optimizer", "limit_value": None},
        {"capability": "monte_carlo", "limit_value": None},
        {"capability": "report_export", "limit_value": None},
        {"capability": "api_access", "limit_value": None},
        {"capability": "multi_device", "limit_value": 5},
    ],
}


async def create_license(
    db: AsyncSession,
    email: str,
    tier: str,
    days: int,
    max_devices: int,
    notes: Optional[str],
    created_by: Optional[uuid.UUID],
) -> License:
    """Create a new license for a user (creates user if doesn't exist)."""
    # Find or create user
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        user = User(email=email, role="user", status="active")
        db.add(user)
        await db.flush()

    # MH-08: Generate plaintext key (shown once to admin) and store only HMAC hash
    from app.config import settings as _settings
    plaintext_key = generate_activation_key()
    activation_key_hash = hash_activation_key(plaintext_key, pepper=_settings.activation_key_pepper)
    starts_at = datetime.now(timezone.utc)
    expires_at = starts_at + timedelta(days=days)

    license_obj = License(
        user_id=user.id,
        tier=tier,
        status="active",
        activation_key=activation_key_hash,  # Store HASH, not plaintext
        starts_at=starts_at,
        expires_at=expires_at,
        max_devices=max_devices,
        notes=notes,
        created_by=created_by,
    )
    db.add(license_obj)
    await db.flush()

    # Create default entitlements for tier
    for ent in TIER_ENTITLEMENTS.get(tier, []):
        entitlement = Entitlement(
            license_id=license_obj.id,
            capability=ent["capability"],
            limit_value=ent.get("limit_value"),
        )
        db.add(entitlement)

    await db.flush()
    await log_event(
        db,
        action="license.created",
        actor_user_id=created_by,
        actor_role="admin",
        target_type="license",
        target_id=str(license_obj.id),
        payload={"email": email, "tier": tier, "days": days, "key_prefix": plaintext_key[:8]},
    )
    await db.flush()
    await db.refresh(license_obj)

    # Attach plaintext key to the object for the response (NOT stored in DB)
    license_obj._plaintext_key = plaintext_key  # type: ignore[attr-defined]

    return license_obj


async def activate_license(
    db: AsyncSession,
    email: str,
    activation_key: str,
    device_fingerprint: str,
    device_name: Optional[str],
    os_info: Optional[str],
    app_version: Optional[str],
    ip_hash: Optional[str],
) -> dict:
    """
    Activate a license key on a device.
    Returns tokens + entitlements on success, raises on failure.
    """
    # MH-08: Hash the provided key with pepper and look up by hash
    from app.config import settings as _settings
    key_hash = hash_activation_key(activation_key, pepper=_settings.activation_key_pepper)

    stmt = select(License).where(
        and_(
            License.activation_key == key_hash,
            License.status == "active",
            License.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    license_obj = result.scalar_one_or_none()

    if not license_obj:
        raise ValueError("invalid_key")

    # Check expiration
    if datetime.now(timezone.utc) > license_obj.expires_at:
        raise ValueError("license_expired")

    # Verify email matches
    user_stmt = select(User).where(User.id == license_obj.user_id)
    user_result = await db.execute(user_stmt)
    user = user_result.scalar_one()

    if user.email.lower() != email.lower():
        raise ValueError("email_mismatch")

    if not user.is_active:
        raise ValueError("user_suspended")

    # Check device limit
    device_count_stmt = select(func.count(Device.id)).where(
        and_(
            Device.user_id == user.id,
            Device.revoked_at.is_(None),
        )
    )
    device_count_result = await db.execute(device_count_stmt)
    device_count = device_count_result.scalar() or 0

    # MH-09: Hash fingerprint with pepper before storage/comparison
    fingerprint_hash = hash_fingerprint(device_fingerprint, pepper=_settings.fingerprint_pepper)

    # Find or register device (compare hashed fingerprints)
    device_stmt = select(Device).where(
        and_(
            Device.user_id == user.id,
            Device.device_fingerprint == fingerprint_hash,
        )
    )
    device_result = await db.execute(device_stmt)
    device = device_result.scalar_one_or_none()

    if not device:
        if device_count >= license_obj.max_devices:
            raise ValueError("device_limit_reached")
        device = Device(
            user_id=user.id,
            device_fingerprint=fingerprint_hash,  # Store HMAC, not raw
            device_name=device_name,
            os_info=os_info,
            app_version=app_version,
            last_ip_hash=ip_hash,
        )
        db.add(device)
        await db.flush()
    else:
        if device.is_revoked:
            raise ValueError("device_revoked")
        device.last_seen_at = datetime.now(timezone.utc)
        device.last_ip_hash = ip_hash
        if app_version:
            device.app_version = app_version

    # Revoke existing sessions for this device
    revoke_stmt = select(Session).where(
        and_(
            Session.device_id == device.id,
            Session.revoked_at.is_(None),
        )
    )
    revoke_result = await db.execute(revoke_stmt)
    old_sessions = revoke_result.scalars().all()
    for old_sess in old_sessions:
        old_sess.revoked_at = datetime.now(timezone.utc)
        old_sess.revoke_reason = "new_activation"

    # Create new session
    refresh_token = generate_refresh_token()
    refresh_token_hash = hash_refresh_token(refresh_token)

    session = Session(
        user_id=user.id,
        device_id=device.id,
        license_id=license_obj.id,
        refresh_token_hash=refresh_token_hash,
        expires_at=get_refresh_token_expiry(),
        client_metadata={
            "app_version": app_version,
            "os_info": os_info,
            "device_name": device_name,
        },
    )
    db.add(session)
    await db.flush()

    access_token = generate_access_token(session.id, user.id)

    # Load entitlements
    ent_stmt = select(Entitlement).where(Entitlement.license_id == license_obj.id)
    ent_result = await db.execute(ent_stmt)
    entitlements = ent_result.scalars().all()

    await log_event(
        db,
        action="license.activated",
        actor_user_id=user.id,
        actor_role="user",
        target_type="license",
        target_id=str(license_obj.id),
        ip_hash=ip_hash,
        payload={"device": device_name, "os": os_info},
    )

    # C-01: Create Ed25519 signed receipt for cryptographic verification
    # The client MUST verify this signature — unsigned receipts are rejected
    entitlements_list = [
        {"capability": e.capability, "limit_value": e.limit_value}
        for e in entitlements
    ]
    receipt = create_license_receipt(
        user_id=str(user.id),
        license_tier=license_obj.tier,
        device_fingerprint=device_fingerprint,
        entitlements=entitlements_list,
        expires_at=license_obj.expires_at.isoformat(),
        session_id=str(session.id),
    )

    return {
        "user_id": str(user.id),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "entitlements": entitlements_list,
        "expires_at": license_obj.expires_at,
        "license_tier": license_obj.tier,
        "receipt": receipt,  # C-01: Signed receipt
    }


async def revoke_license(
    db: AsyncSession,
    license_id: uuid.UUID,
    reason: Optional[str],
    actor_user_id: uuid.UUID,
) -> License:
    """Revoke a license and all associated sessions."""
    stmt = select(License).where(License.id == license_id)
    result = await db.execute(stmt)
    license_obj = result.scalar_one_or_none()

    if not license_obj:
        raise ValueError("license_not_found")

    license_obj.status = "revoked"
    license_obj.revoked_at = datetime.now(timezone.utc)
    license_obj.revoke_reason = reason

    # Revoke all sessions
    sess_stmt = select(Session).where(
        and_(
            Session.license_id == license_id,
            Session.revoked_at.is_(None),
        )
    )
    sess_result = await db.execute(sess_stmt)
    sessions = sess_result.scalars().all()
    for sess in sessions:
        sess.revoked_at = datetime.now(timezone.utc)
        sess.revoke_reason = f"license_revoked: {reason}"

    await log_event(
        db,
        action="license.revoked",
        actor_user_id=actor_user_id,
        actor_role="admin",
        target_type="license",
        target_id=str(license_id),
        payload={"reason": reason},
    )
    await db.flush()
    await db.refresh(license_obj)
    return license_obj


async def get_license_entitlements(
    db: AsyncSession,
    license_id: uuid.UUID,
) -> list[dict]:
    """Get entitlements for a license."""
    stmt = select(Entitlement).where(Entitlement.license_id == license_id)
    result = await db.execute(stmt)
    entitlements = result.scalars().all()
    return [
        {"capability": e.capability, "limit_value": e.limit_value}
        for e in entitlements
    ]