#!/usr/bin/env python3
"""
AGREGAT Server — Generate Ed25519 Signing Keypair

Run this ONCE during initial server setup:
    python scripts/generate_signing_key.py

This generates:
  - server/keys/license_signing_key.pem  (private key — KEEP SECRET)
  - server/keys/license_signing_key.pub  (public key — embed in desktop client)

The public key base64 is printed for embedding in the desktop client binary.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.security.signing import generate_signing_keypair, get_public_key_base64


def main():
    print("=" * 60)
    print("AGREGAT — Ed25519 License Signing Key Generator")
    print("=" * 60)
    
    private_pem, public_pem = generate_signing_keypair()
    
    print("\n✅ Keypair generated successfully!")
    print(f"\n📁 Private key: server/keys/license_signing_key.pem")
    print(f"📁 Public key:  server/keys/license_signing_key.pub")
    
    print("\n⚠️  IMPORTANT:")
    print("   - NEVER commit the private key to version control")
    print("   - Add server/keys/*.pem to .gitignore")
    print("   - Back up the private key securely")
    
    # Print public key base64 for embedding in desktop client
    public_b64 = get_public_key_base64()
    print(f"\n📋 Public key (base64) — embed this in desktop client:")
    print("-" * 60)
    print(public_b64)
    print("-" * 60)
    print("\nAdd this to engine/security/license_client.py as:")
    print(f'  SERVER_PUBLIC_KEY_B64 = "{public_b64}"')


if __name__ == "__main__":
    main()