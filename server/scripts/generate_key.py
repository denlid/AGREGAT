#!/usr/bin/env python3
"""
AGREGAT Server — License Key Generator

Generates activation keys for users. Keys are stored in the database
and can be issued via CLI (before admin panel is ready) or via admin panel.

Usage:
    python scripts/generate_key.py --email user@example.com --days 7 --tier beta
    python scripts/generate_key.py --email user@example.com --days 30 --tier pro --notes "Premium user"
    python scripts/generate_key.py --batch users.csv  # CSV with email,days,tier columns

Key Format: AGR-{TIER}-{RANDOM_4}-{RANDOM_4}-{RANDOM_4}
Example:    AGR-BETA-X7K2-M9PQ-R4TW
"""

import asyncio
import argparse
import csv
import os
import secrets
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def generate_activation_key(tier: str = "beta") -> str:
    """Generate a unique activation key."""
    tier_prefix = tier.upper()[:4]
    alphabet = string.ascii_uppercase + string.digits
    # Remove confusing characters
    alphabet = alphabet.replace('O', '').replace('0', '').replace('I', '').replace('1')
    
    parts = [''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return f"AGR-{tier_prefix}-{'-'.join(parts)}"


async def create_license_key(
    conn,
    email: str,
    days: int,
    tier: str = "beta",
    notes: str = "",
    created_by_email: str = None,
    max_devices: int = 1,
) -> str:
    """Create a user (if needed) and generate a license key."""
    
    # Find or create user
    user = await conn.fetchrow("SELECT id, role FROM users WHERE email = $1", email)
    
    if user is None:
        # Create regular user (no password — auth via license key)
        user_id = await conn.fetchval(
            """
            INSERT INTO users (email, role, status)
            VALUES ($1, 'user', 'active')
            RETURNING id
            """,
            email,
        )
        print(f"   📝 Created new user: {email}")
    else:
        user_id = user["id"]
    
    # Get creator ID (owner/admin who is generating this key)
    created_by = None
    if created_by_email:
        creator = await conn.fetchrow(
            "SELECT id FROM users WHERE email = $1", created_by_email
        )
        if creator:
            created_by = creator["id"]
    
    # Generate unique key
    activation_key = generate_activation_key(tier)
    
    # Check for collisions (extremely unlikely but safe)
    while await conn.fetchrow(
        "SELECT id FROM licenses WHERE activation_key = $1", activation_key
    ):
        activation_key = generate_activation_key(tier)
    
    # Calculate expiration
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=days)
    
    # Insert license
    await conn.execute(
        """
        INSERT INTO licenses (user_id, tier, status, activation_key, starts_at, expires_at, max_devices, notes, created_by)
        VALUES ($1, $2, 'active', $3, $4, $5, $6, $7, $8)
        """,
        user_id,
        tier,
        activation_key,
        now,
        expires_at,
        max_devices,
        notes,
        created_by,
    )
    
    # Grant default entitlements based on tier
    entitlements = get_tier_entitlements(tier)
    license_id = await conn.fetchval(
        "SELECT id FROM licenses WHERE activation_key = $1", activation_key
    )
    
    for cap, limit in entitlements:
        await conn.execute(
            """
            INSERT INTO entitlements (license_id, capability, limit_value)
            VALUES ($1, $2, $3)
            ON CONFLICT (license_id, capability) DO NOTHING
            """,
            license_id,
            cap,
            limit,
        )
    
    # Audit log
    await conn.execute(
        """
        INSERT INTO audit_events (actor_user_id, actor_role, action, target_type, target_id, payload_json, ip_hash, user_agent_hash)
        VALUES ($1, 'owner', 'license.issued', 'license', $2, $3, 'cli', 'generate_key_script')
        """,
        created_by,
        str(license_id),
        f'{{"email": "{email}", "tier": "{tier}", "days": {days}, "key": "{activation_key}"}}',
    )
    
    return activation_key


def get_tier_entitlements(tier: str) -> list[tuple[str, int | None]]:
    """Return default entitlements for a tier."""
    common = [
        ("app.launch", None),
        ("engine.visual_backtest", None),
        ("engine.batch_backtest", None),
        ("engine.optimizer", None),
        ("chart.render", None),
        ("report.export", None),
        ("strategy.load", None),
        ("strategy.save", None),
    ]
    
    tier_caps = {
        "beta": common + [
            ("ai.assistant", None),
            ("optimizer.grid_search", 100),
        ],
        "pro": common + [
            ("ai.assistant", None),
            ("ai.code_generation", None),
            ("optimizer.grid_search", None),
            ("optimizer.genetic", None),
            ("analytics.advanced", None),
        ],
        "enterprise": common + [
            ("ai.assistant", None),
            ("ai.code_generation", None),
            ("ai.strategy_analysis", None),
            ("optimizer.grid_search", None),
            ("optimizer.genetic", None),
            ("optimizer.bayesian", None),
            ("analytics.advanced", None),
            ("analytics.custom_metrics", None),
            ("api.sdk", None),
            ("api.cli", None),
        ],
    }
    
    return tier_caps.get(tier.lower(), common)


async def main():
    parser = argparse.ArgumentParser(description="Generate AGREGAT license keys")
    parser.add_argument("--email", help="User email")
    parser.add_argument("--days", type=int, default=7, help="License duration in days")
    parser.add_argument("--tier", default="beta", choices=["beta", "pro", "enterprise"], help="License tier")
    parser.add_argument("--notes", default="", help="Internal notes")
    parser.add_argument("--max-devices", type=int, default=1, help="Max devices per license")
    parser.add_argument("--batch", help="CSV file with columns: email,days,tier")
    parser.add_argument("--created-by", default=None, help="Admin email who created this key")
    
    args = parser.parse_args()
    
    if not args.email and not args.batch:
        parser.error("Specify --email or --batch")
    
    database_url = get_env("DATABASE_URL")
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    
    conn = await asyncpg.connect(database_url)
    
    try:
        if args.batch:
            # Batch mode
            print(f"📂 Processing batch: {args.batch}")
            with open(args.batch, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    email = row["email"].strip()
                    days = int(row.get("days", args.days))
                    tier = row.get("tier", args.tier).strip()
                    key = await create_license_key(
                        conn, email, days, tier,
                        created_by_email=args.created_by,
                    )
                    print(f"   ✅ {email}: {key} ({tier}, {days}d)")
        else:
            # Single key
            print(f"🔑 Generating license key...")
            print(f"   Email: {args.email}")
            print(f"   Tier:  {args.tier}")
            print(f"   Days:  {args.days}")
            print()
            
            key = await create_license_key(
                conn,
                args.email,
                args.days,
                args.tier,
                args.notes,
                created_by_email=args.created_by,
                max_devices=args.max_devices,
            )
            
            print("=" * 50)
            print(f"✅ LICENSE KEY GENERATED")
            print("=" * 50)
            print(f"\n   🔑 {key}\n")
            print(f"   Email:     {args.email}")
            print(f"   Tier:      {args.tier}")
            print(f"   Duration:  {args.days} days")
            print(f"   Devices:   {args.max_devices}")
            print(f"   Expires:   {(datetime.now(timezone.utc) + timedelta(days=args.days)).strftime('%Y-%m-%d')}")
            print("=" * 50)
            print(f"\n📋 Send this key to the user along with activation instructions.")
    
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())