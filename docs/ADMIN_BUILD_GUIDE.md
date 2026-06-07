# AGREGAT — Admin & Build Guide

**Version:** 7.7.8  
**Last Updated:** 2026-06-07  
**Audience:** Build engineers, system administrators, security operators

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Key Generation & Management](#key-generation--management)
4. [Environment Variables](#environment-variables)
5. [Build Process (Windows)](#build-process-windows)
6. [Server Deployment](#server-deployment)
7. [License Administration](#license-administration)
8. [Security Verification](#security-verification)
9. [Incident Response](#incident-response)
10. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

AGREGAT uses a **defense-in-depth** security architecture with 6 layers:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: License Verification                           │
│  • Ed25519 signed receipts (server-authoritative)       │
│  • Offline cache with HMAC + 24h grace period           │
│  • Device fingerprint (CPU+disk+MAC+BIOS)               │
│  • Adaptive rate limiting for signature failures        │
├─────────────────────────────────────────────────────────┤
│ Layer 2: Anti-Tamper & Anti-Debug                       │
│  • Binary integrity (SHA-256 + Ed25519 manifest)        │
│  • Debugger detection (IsDebuggerPresent, ptrace)       │
│  • VM detection (CPUID, DMI, MAC OUI, registry)         │
│  • Disinformation mode for VM analysis                  │
│  • DLL injection & IAT hook detection                   │
├─────────────────────────────────────────────────────────┤
│ Layer 3: Request Security                               │
│  • HMAC request signing with nonce                      │
│  • Replay attack prevention                             │
│  • TLS certificate pinning                              │
├─────────────────────────────────────────────────────────┤
│ Layer 4: Data Protection                                │
│  • Encrypted DB fields (Fernet/AES-128)                 │
│  • Secure secret storage (DPAPI/Keychain/libsecret)     │
│  • Secure memory (CryptProtectMemory + VirtualLock)     │
│  • AI privacy with code sanitization                    │
├─────────────────────────────────────────────────────────┤
│ Layer 5: Strategy Security                              │
│  • Sandboxed execution (restricted builtins)            │
│  • DSL injection protection                             │
│  • File upload validation (magic bytes)                 │
├─────────────────────────────────────────────────────────┤
│ Layer 6: Server Security                                │
│  • Security headers middleware                          │
│  • Honeypot for admin login                             │
│  • Memory monitor for DoS protection                    │
└─────────────────────────────────────────────────────────┘
```

---

## Prerequisites

### Build Machine Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Windows 10 x64 | Windows 11 x64 |
| Python | 3.10 | 3.12 |
| RAM | 8 GB | 16 GB |
| Disk | 5 GB free | 10 GB free |
| CPU | 4 cores | 8+ cores |

### Required Python Packages

```bash
pip install -r requirements.txt
pip install nuitka orderedset zstandard python-minifier
```

### Required Tools

| Tool | Purpose | Install |
|------|---------|---------|
| Nuitka | Python → C compilation | `pip install nuitka` |
| UPX (optional) | Binary compression | https://upx.github.io/ |
| Git | Version control | https://git-scm.com/ |
| OpenSSL (optional) | Key generation | https://slproweb.com/products/Win32OpenSSL.html |

---

## Key Generation & Management

### 1. Ed25519 Signing Key Pair (License System)

**CRITICAL:** The private key MUST NEVER leave the admin's secure machine.  
The public key is embedded in the client binary.

```bash
# Generate on the ADMIN machine (never on build machine)
python server/scripts/generate_signing_key.py

# Output:
#   signing_key.pem          ← PRIVATE key (keep secret, encrypt at rest)
#   signing_public_key.pem   ← PUBLIC key (embed in license_client.py)
```

**Embedding the public key:**

1. Open `engine/security/license_client.py`
2. Find `_PRODUCTION_PUBLIC_KEY_B64`
3. Replace with the base64-encoded public key:

```python
_PRODUCTION_PUBLIC_KEY_B64 = "MCowBQYDK2VwAyEA..."  # Your public key here
```

**Encrypting the private key at rest:**

```bash
# Set a strong password (20+ characters)
set AGREGAT_SIGNING_KEY_PASSWORD=your-strong-password-here

# The server/scripts/generate_signing_key.py script encrypts the key
# using AES-256-GCM with a key derived from this password.
```

### 2. TLS Certificate Pin

```bash
# Get the SHA-256 fingerprint of your server's TLS certificate
openssl s_client -connect your-server.com:443 < /dev/null 2>/dev/null | \
  openssl x509 -fingerprint -sha256 -noout

# Output: SHA256 Fingerprint=AB:CD:EF:12:34:...

# Embed in license_client.py:
_BUNDLED_CERT_SHA256 = "ABCDEF1234..."  # Lowercase, no colons
```

### 3. Database Encryption Key (Fernet)

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Output: abcdefghijklmnopqrstuvwxyz1234567890_ABCDE=
# Set as environment variable on SERVER:
set AGREGAT_DB_ENCRYPTION_KEY=abcdefghijklmnopqrstuvwxyz1234567890_ABCDE=
```

### 4. License Cache Secret

```bash
python -c "import secrets; print(secrets.token_hex(32))"

# Output: a1b2c3d4e5f6...
# Set as environment variable on CLIENT build machine:
set AGREGAT_CACHE_SECRET=a1b2c3d4e5f6...
```

### Key Storage Best Practices

| Key | Storage | Access |
|-----|---------|--------|
| Ed25519 Private Key | Admin's encrypted USB / password manager | Admin only |
| Ed25519 Public Key | Embedded in client binary | Public (in binary) |
| DB Encryption Key | Server env var / secrets manager | Server process only |
| Cache Secret | Build machine env var | Build engineer |
| TLS Cert Pin | Embedded in client binary | Public (in binary) |
| JWT Secret | Server env var | Server process only |

**NEVER commit any of these to Git.** The `.gitignore` file excludes:
- `*.key`, `*.pem`, `*.p12`, `*.pfx`, `*.crt`, `*.cer`
- `.env`, `.env.*`, `server/.env`
- `signing_key.pem`, `ed25519_private_key.*`
- `secrets.json`, `credentials.json`

---

## Environment Variables

### Build Machine (Client)

| Variable | Required | Purpose |
|----------|----------|---------|
| `AGREGAT_CACHE_SECRET` | ✅ | Encrypts offline license cache |
| `AGREGAT_SECURITY_HASHES` | Auto | SHA-256 hashes of security files (set by build script) |
| `AGREGAT_DEV_MODE` | ❌ | Must NOT be set for production builds |
| `APP_ENV` | ❌ | Must NOT be `development` for production builds |

### Server

| Variable | Required | Purpose | Generate |
|----------|----------|---------|----------|
| `AGREGAT_DB_ENCRYPTION_KEY` | ✅ | Fernet key for encrypted DB fields | `Fernet.generate_key()` |
| `AGREGAT_SIGNING_KEY_PASSWORD` | ✅ | Password for encrypted signing key | Strong password (20+ chars) |
| `JWT_SECRET` | ✅ | JWT token signing secret | `secrets.token_hex(32)` |
| `FINGERPRINT_SALT` | ✅ | Salt for device fingerprint | `secrets.token_hex(16)` |
| `ALLOWED_ORIGINS` | ✅ | CORS allowed origins | Comma-separated domains |
| `DATABASE_URL` | ✅ | PostgreSQL connection string | `postgresql://user:pass@host/db` |
| `DEBUG` | ❌ | Must be `false` in production | — |
| `AGREGAT_SERVER_URL` | ✅ | Server URL (HTTPS required) | `https://your-server.com` |

### Development Mode (MUST be disabled for production)

Development mode requires **BOTH** environment variables:
```bash
set AGREGAT_DEV_MODE=1
set APP_ENV=development
```

A `.dev_mode` file alone is **NOT sufficient** — this prevents accidental shipping of dev bypass.

---

## Build Process (Windows)

### Step 1: Pre-Build Checklist

```bash
# 1. Ensure NO .dev_mode file exists
if exist .dev_mode del .dev_mode

# 2. Ensure NO private keys in project directory
dir *.pem /s
dir *.key /s
# If any private keys found, MOVE them outside the project

# 3. Verify Ed25519 public key is embedded
findstr "AGREGAT_PUBLIC_KEY" engine\security\license_client.py

# 4. Set cache secret
set AGREGAT_CACHE_SECRET=<your-64-char-hex>

# 5. Pull latest code
git pull origin main
git status
# Ensure no uncommitted changes to security files
```

### Step 2: Run Build Script

```bash
build_final_windows.cmd
```

The build script performs these phases automatically:

| Phase | Action | Time |
|-------|--------|------|
| 0 | Security pre-build checks | < 1s |
| 1 | Clean previous builds | < 5s |
| 2 | Minify security modules (obfuscation) | ~30s |
| 2.5 | Compute SHA-256 hashes for integrity | ~5s |
| 3 | Nuitka compilation (C → binary) | 10-30 min |
| 4 | UPX packing (optional) | ~1 min |
| 5 | Generate build integrity hash | < 1s |
| 6 | Verification | < 1s |

### Step 3: Post-Build Verification

```bash
# 1. Verify binary exists
dir dist\AGREGAT.exe

# 2. Verify SHA-256 hash
type dist\AGREGAT.exe.sha256

# 3. Test binary runs (on clean machine without Python)
dist\AGREGAT.exe

# 4. Verify security modules loaded
# The app should show license prompt (not crash)

# 5. Verify NO sensitive files in dist/
dir dist\*.env /s
dir dist\*.key /s
dir dist\*.pem /s
# Should return nothing
```

### Step 4: Code Signing (Post-Beta)

```bash
# Sign with your code signing certificate
signtool sign /fd SHA256 /tr http://timestamp.digicert.com ^
  /td SHA256 /sha1 <your-cert-thumbprint> dist\AGREGAT.exe

# Verify signature
signtool verify /pa dist\AGREGAT.exe
```

### Step 5: Publish

1. Upload `dist\AGREGAT.exe` to your distribution server
2. Publish `dist\AGREGAT.exe.sha256` alongside it
3. Users verify: `certutil -hashfile AGREGAT.exe SHA256`

---

## Server Deployment

### Docker Deployment

```bash
cd server

# 1. Create .env file (NEVER commit to Git)
cat > .env << 'EOF'
AGREGAT_DB_ENCRYPTION_KEY=<fernet-key>
AGREGAT_SIGNING_KEY_PASSWORD=<password>
JWT_SECRET=<64-char-hex>
FINGERPRINT_SALT=<32-char-hex>
ALLOWED_ORIGINS=https://your-domain.com
DATABASE_URL=postgresql://agregat:password@db:5432/agregat
DEBUG=false
AGREGAT_SERVER_URL=https://your-domain.com
EOF

# 2. Copy signing key (encrypted) to server
cp /secure/location/signing_key_encrypted.pem server/app/security/

# 3. Build and start
docker-compose -f docker-compose.prod.yml up -d --build

# 4. Verify
curl -s https://your-domain.com/api/health | python -m json.tool
```

### Manual Deployment

```bash
cd server

# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
export AGREGAT_DB_ENCRYPTION_KEY=<fernet-key>
export AGREGAT_SIGNING_KEY_PASSWORD=<password>
export JWT_SECRET=<64-char-hex>
export FINGERPRINT_SALT=<32-char-hex>
export DEBUG=false

# 3. Run migrations
alembic upgrade head

# 4. Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Production Checklist

- [ ] `DEBUG=false`
- [ ] `AGREGAT_DB_ENCRYPTION_KEY` set
- [ ] `JWT_SECRET` is random 64-char hex
- [ ] `FINGERPRINT_SALT` is random string
- [ ] `ALLOWED_ORIGINS` set to specific domains
- [ ] HTTPS enabled (Let's Encrypt / Cloudflare)
- [ ] Default owner password changed
- [ ] PostgreSQL backups configured
- [ ] Firewall configured (only 80/443 open)
- [ ] Fail2ban or similar for SSH
- [ ] Log rotation configured
- [ ] Monitoring/alerting configured

---

## License Administration

### Issuing a License

```bash
# On the ADMIN machine (with private key)
python server/scripts/issue_license.py \
  --customer "John Doe" \
  --email "john@example.com" \
  --tier "pro" \
  --expires "2027-06-07" \
  --hwid "<customer-hwid>"

# Output: license_receipt.json
# Send this file to the customer
```

### License Tiers

| Tier | Features | Max Strategies | Max Assets |
|------|----------|---------------|------------|
| trial | Basic backtest | 1 | 5 |
| basic | Backtest + optimization | 5 | 20 |
| pro | All features | Unlimited | Unlimited |
| enterprise | Pro + priority support | Unlimited | Unlimited |

### Revoking a License

```bash
# Add HWID to revocation list on server
python server/scripts/revoke_license.py --hwid "<hwid>"

# The client will be blocked on next online verification
```

### Offline License Cache

The client caches the license receipt locally with:
- HMAC-SHA256 integrity check
- 24-hour grace period for offline use
- Encrypted with `AGREGAT_CACHE_SECRET`

Cache location: `%USERPROFILE%\.agregat\license_cache.enc`

---

## Security Verification

### Smoke Tests

```bash
# Test all security modules import correctly
python -c "from engine.security.strategy_sandbox import StrategySandbox; print('C-01 OK')"
python -c "from engine.security.secret_store import SecretStore; print('C-02 OK')"
python -c "from engine.security.integrity import IntegrityChecker; print('C-03 OK')"
python -c "from engine.security.update_checker import UpdateChecker; print('M-03 OK')"
python -c "from engine.security.secure_memory import SecureToken; print('MED-07 OK')"
python -c "from engine.security.anti_debug import detect_vm, DisinformationMode; print('P0-10 OK')"
python -c "from gui.security.ai_privacy import AIPrivacyManager; print('H-03 OK')"
python -c "from server.app.models.encrypted_fields import EncryptedString; print('H-02 OK')"
python -c "from server.app.security.middleware import SecurityHeadersMiddleware; print('L-01 OK')"
python -c "from server.app.security.honeypot import HoneypotGuard; print('L-02 OK')"
python -c "from server.app.security.memory_monitor import MemoryMonitor; print('L-03 OK')"
```

### VM Detection Test

```python
from engine.security.anti_debug import get_vm_detection_summary
import json
print(json.dumps(get_vm_detection_summary(), indent=2))
```

Expected output on bare metal:
```json
{
  "vm_detected": false,
  "vm_type": null,
  "suspicious_processes": [],
  "disinformation_active": false,
  "debugger_attached": false,
  "timing_anomaly": false
}
```

### Integrity Check Test

```python
from engine.security.integrity import IntegrityChecker
checker = IntegrityChecker()
result = checker.verify_all()
print(f"Valid: {result.is_valid}")
print(f"Files checked: {result.files_checked}")
```

---

## Incident Response

### Suspected Key Compromise

1. **Immediately** rotate the compromised key
2. Re-issue all affected licenses with new key
3. Update `_PRODUCTION_PUBLIC_KEY_B64` in `license_client.py`
4. Rebuild and redistribute client binary
5. Revoke all licenses signed with old key

### Suspected Binary Tampering

1. Compare SHA-256 hash with published hash
2. If mismatch: alert users, pull distribution
3. Investigate build machine for compromise
4. Rebuild from clean source on clean machine
5. Publish new hash

### Suspected Server Breach

1. Rotate ALL secrets (DB encryption, JWT, signing key password)
2. Force password reset for all admin accounts
3. Review access logs for unauthorized access
4. Check honeypot logs for attack indicators
5. Restore from clean backup if needed
6. Notify affected users if PII was exposed

### Memory Dump Analysis Detected

If `detect_suspicious_processes()` finds Cheat Engine, Process Hacker, etc.:

1. The app logs the detection (if audit logging enabled)
2. Periodic security watchdog may trigger silent exit
3. Consider enabling disinformation mode for VM analysis

---

## Troubleshooting

### Build Fails: "Ed25519 public key not found"

```bash
# Embed the public key in license_client.py
# Find: _PRODUCTION_PUBLIC_KEY_B64 = ""
# Replace with your base64-encoded public key
```

### Build Fails: ".dev_mode file detected"

```bash
del .dev_mode
# Also ensure AGREGAT_DEV_MODE and APP_ENV are not set
set AGREGAT_DEV_MODE=
set APP_ENV=
```

### App Crashes: "License verification failed"

```bash
# Check if license cache exists
dir %USERPROFILE%\.agregat\license_cache.enc

# Check if AGREGAT_CACHE_SECRET is set
echo %AGREGAT_CACHE_SECRET%

# Re-import license receipt
# File → Import License → select license_receipt.json
```

### Server: "Database encryption key not configured"

```bash
# Generate and set the key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
set AGREGAT_DB_ENCRYPTION_KEY=<output>
```

### VM Detection False Positive

Some users legitimately run in VMs (corporate environments). To allow VMs:

```python
# In app.py, before starting:
from engine.security.anti_debug import detect_vm, DisinformationMode

vm = detect_vm()
if vm:
    # Option 1: Log but allow
    logger.warning(f"Running in VM: {vm}")
    
    # Option 2: Activate disinformation mode
    # DisinformationMode.activate()
    
    # Option 3: Block (not recommended for beta)
    # sys.exit("Hardware not supported")
```

---

## Quick Reference

### Key Commands

| Action | Command |
|--------|---------|
| Build Windows | `build_final_windows.cmd` |
| Generate signing key | `python server/scripts/generate_signing_key.py` |
| Issue license | `python server/scripts/issue_license.py --customer X --tier Y` |
| Revoke license | `python server/scripts/revoke_license.py --hwid X` |
| Verify build hash | `certutil -hashfile dist\AGREGAT.exe SHA256` |
| Security smoke test | See [Smoke Tests](#smoke-tests) section |
| Start server (Docker) | `cd server && docker-compose -f docker-compose.prod.yml up -d` |
| Start server (manual) | `cd server && uvicorn app.main:app --host 0.0.0.0 --port 8000` |

### File Locations

| File | Purpose | Should be in Git? |
|------|---------|-------------------|
| `engine/security/license_client.py` | Client license verification | ✅ (with public key) |
| `signing_key.pem` | Private signing key | ❌ NEVER |
| `signing_public_key.pem` | Public signing key | ❌ (embedded in code) |
| `.env` | Environment variables | ❌ NEVER |
| `server/.env` | Server environment | ❌ NEVER |
| `build_final_windows.cmd` | Build script | ✅ |
| `.gitignore` | Git ignore rules | ✅ |
| `SECURITY_AUDIT_REPORT.md` | Audit report | ✅ |
| `docs/ADMIN_BUILD_GUIDE.md` | This guide | ✅ |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-06-07 | Initial version — covers all security modules P0-1 through P0-11 |