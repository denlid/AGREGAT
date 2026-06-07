#!/usr/bin/env python3
"""
AGREGAT Server — Create Owner Account

Creates the initial owner (super-admin) account in the database.
Run this ONCE after setting up the database.

Usage:
    python scripts/create_owner.py

Requires:
    - .env file with DATABASE_URL and OWNER_EMAIL configured
    - Database schema already created (migrations/init.sql)
"""

import asyncio
import hashlib
import os
import secrets
import sys
from pathlib import Path

import bcrypt
import pyotp
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Load .env from server root
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

try:
    import asyncpg
except ImportError:
    print("❌ asyncpg not installed. Run: pip install asyncpg")
    sys.exit(1)


def get_env(key: str, default: str = None) -> str:
    value = os.environ.get(key, default)
    if value is None or value.startswith("CHANGE_ME") or value.startswith("SET_VIA"):
        print(f"❌ {key} not configured in .env")
        sys.exit(1)
    return value


async def create_owner():
    database_url = get_env("DATABASE_URL")
    owner_email = get_env("OWNER_EMAIL")
    encryption_key = get_env("ENCRYPTION_KEY")

    # Parse DATABASE_URL (remove asyncpg:// prefix for asyncpg)
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    print(f"🔧 Connecting to database...")
    print(f"📧 Owner email: {owner_email}")

    # Prompt for password
    print()
    while True:
        password = input("🔑 Enter owner password (min 12 chars): ").strip()
        if len(password) >= 12:
            break
        print("   ⚠️  Password must be at least 12 characters")

    confirm = input("🔑 Confirm password: ").strip()
    if password != confirm:
        print("❌ Passwords don't match!")
        sys.exit(1)

    # Hash password with bcrypt
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=14)).decode()

    # Generate TOTP secret
    totp_secret = pyotp.random_base32()
    totp_uri = pyotp.totp.TOTP(totp_secret).provisioning_uri(
        name=owner_email, issuer_name="AGREGAT Admin"
    )

    # Encrypt TOTP secret for storage
    fernet = Fernet(encryption_key.encode())
    encrypted_totp = fernet.encrypt(totp_secret.encode()).decode()

    # Generate backup codes (8 codes, 8 chars each)
    backup_codes = [secrets.token_hex(4).upper() for _ in range(8)]
    encrypted_backup = [
        fernet.encrypt(code.encode()).decode() for code in backup_codes
    ]

    # Connect and insert
    conn = await asyncpg.connect(database_url)
    try:
        # Check if owner already exists
        existing = await conn.fetchrow(
            "SELECT id, email, role FROM users WHERE email = $1", owner_email
        )
        if existing:
            print(f"\n⚠️  Account already exists: {existing['email']} (role: {existing['role']})")
            update = input("   Update password and MFA? (yes/no): ").strip().lower()
            if update != "yes":
                print("   Aborted.")
                return

            await conn.execute(
                """
                UPDATE users SET
                    password_hash = $1,
                    totp_secret = $2,
                    totp_enabled = TRUE,
                    backup_codes = $3,
                    role = 'owner',
                    status = 'active'
                WHERE email = $4
                """,
                password_hash,
                encrypted_totp,
                encrypted_backup,
                owner_email,
            )
            print("✅ Owner account updated!")
        else:
            await conn.execute(
                """
                INSERT INTO users (email, password_hash, role, status, totp_secret, totp_enabled, backup_codes)
                VALUES ($1, $2, 'owner', 'active', $3, TRUE, $4)
                """,
                owner_email,
                password_hash,
                encrypted_totp,
                encrypted_backup,
            )
            print("✅ Owner account created!")

        # Log audit event
        await conn.execute(
            """
            INSERT INTO audit_events (actor_user_id, actor_role, action, target_type, target_id, payload_json, ip_hash, user_agent_hash)
            VALUES (
                (SELECT id FROM users WHERE email = $1),
                'owner',
                'owner.created',
                'user',
                (SELECT id::text FROM users WHERE email = $1),
                '{"email": "' || $1 || '"}',
                'local',
                'create_owner_script'
            )
            """,
            owner_email,
        )

    finally:
        await conn.close()

    # Print TOTP setup info
    print("\n" + "=" * 60)
    print("🔐 MFA SETUP REQUIRED")
    print("=" * 60)
    print(f"\n📱 Scan this QR code with Google Authenticator:")
    print(f"\n   {totp_uri}\n")
    print(f"   Or enter this secret manually:")
    print(f"   {totp_secret}\n")
    print("=" * 60)
    print("📋 BACKUP CODES (save these somewhere safe!):")
    print("=" * 60)
    for i, code in enumerate(backup_codes, 1):
        print(f"   {i}. {code}")
    print("=" * 60)
    print("\n⚠️  These codes will NOT be shown again!")
    print("   Store them offline (password manager, printed, etc.)")
    print("\n🎉 Setup complete! Login at: http://localhost:8000/admin")


if __name__ == "__main__":
    asyncio.run(create_owner())