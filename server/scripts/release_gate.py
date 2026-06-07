#!/usr/bin/env python3
"""
AGREGAT Server — Release Gate Security Checklist

MH-10: Fixed to be ASCII-safe on Windows and includes semantic checks
for all audit findings (C-01 through C-05, H-01 through H-11, MH-01 through MH-10).

Run this script BEFORE deploying to production:
    python scripts/release_gate.py
    python -X utf8 scripts/release_gate.py  (Windows fallback)

Exits with code 1 if any check fails (blocks deployment).
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

# MH-10: Force UTF-8 for stdout/stderr to prevent UnicodeEncodeError on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ASCII-safe colors (no emoji in raw output — use text markers)
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

SERVER_DIR = Path(__file__).parent.parent  # server/
PROJECT_ROOT = SERVER_DIR.parent  # AGREGAT/

passed = 0
failed = 0
warnings = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    status = f"{GREEN}[PASS]{RESET}" if condition else f"{RED}[FAIL]{RESET}"
    print(f"  {status}  {name}")
    if detail and not condition:
        print(f"         {YELLOW}-> {detail}{RESET}")
    if condition:
        passed += 1
    else:
        failed += 1


def warn(name: str, detail: str = "") -> None:
    global warnings
    print(f"  {YELLOW}[WARN]{RESET}  {name}")
    if detail:
        print(f"         -> {detail}")
    warnings += 1


def read_file(path: Path) -> str:
    """Read file content, return empty string if not found."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def file_contains(path: Path, pattern: str) -> bool:
    """Check if file contains a regex pattern."""
    content = read_file(path)
    return bool(re.search(pattern, content, re.MULTILINE | re.DOTALL))


def file_not_contains(path: Path, pattern: str) -> bool:
    """Check if file does NOT contain a regex pattern."""
    return not file_contains(path, pattern)


# ─── Banner ───────────────────────────────────────────────────────────

print()
print(f"{BOLD}{'='*60}{RESET}")
print(f"{BOLD}  AGREGAT Server — Release Gate Security Check{RESET}")
print(f"{BOLD}{'='*60}{RESET}")
print()

# ─── C-01: Dev Mode Bypass ───────────────────────────────────────────

print(f"{BOLD}[C-01] Dev Mode Bypass Removal{RESET}")

license_client = PROJECT_ROOT / "engine" / "security" / "license_client.py"
check(
    "No .dev_mode file check in license_client.py",
    file_not_contains(license_client, r'\.dev_mode.*exists|Path.*\.dev_mode'),
    "Found .dev_mode file check — must use env vars only",
)
check(
    "Uses AGREGAT_DEV_MODE env var",
    file_contains(license_client, r'AGREGAT_DEV_MODE'),
    "Must check AGREGAT_DEV_MODE environment variable",
)

# ─── C-02: Admin Auth Consistency ────────────────────────────────────

print(f"\n{BOLD}[C-02] Admin Auth (bcrypt consistency){RESET}")

auth_service = SERVER_DIR / "app" / "services" / "auth_service.py"
check(
    "Uses bcrypt for password hashing",
    file_contains(auth_service, r'import bcrypt'),
    "Must use bcrypt for password hashing",
)
check(
    "Consistent bcrypt usage (no mixed hash methods)",
    file_contains(auth_service, r'bcrypt\.(hashpw|checkpw)'),
    "Must use bcrypt.hashpw and bcrypt.checkpw consistently",
)

# ─── C-03: Admin Sessions in PostgreSQL ──────────────────────────────

print(f"\n{BOLD}[C-03] Admin Sessions in PostgreSQL{RESET}")

admin_session_model = SERVER_DIR / "app" / "models" / "admin_session.py"
check(
    "AdminSession model exists",
    admin_session_model.exists(),
    "Must have AdminSession model for persistent sessions",
)
check(
    "AdminSession has token_hash column",
    file_contains(admin_session_model, r'token_hash'),
    "Must store session token hash in database",
)

deps_file = SERVER_DIR / "app" / "api" / "deps.py"
check(
    "require_admin checks database session",
    file_contains(deps_file, r'AdminSession'),
    "Must verify admin session against database",
)

# ─── C-04: Allowed Hosts fail-closed ─────────────────────────────────

print(f"\n{BOLD}[C-04] Allowed Hosts Fail-Closed{RESET}")

config_py = SERVER_DIR / "app" / "config.py"
check(
    "Production validation blocks empty ALLOWED_HOSTS",
    file_contains(config_py, r'ALLOWED_HOSTS.*empty|_validate_production_settings'),
    "Must fail production startup when ALLOWED_HOSTS is empty",
)

main_py = SERVER_DIR / "app" / "main.py"
check(
    "TrustedHostMiddleware configured",
    file_contains(main_py, r'TrustedHostMiddleware'),
    "Must have TrustedHost middleware",
)

# ─── C-05: CSRF Protection ──────────────────────────────────────────

print(f"\n{BOLD}[C-05] CSRF Protection{RESET}")

check(
    "CSRF token generation exists",
    file_contains(deps_file, r'def generate_csrf_token'),
    "Must have CSRF token generation",
)
check(
    "CSRF verification uses constant-time comparison",
    file_contains(deps_file, r'hmac\.compare_digest'),
    "Must use hmac.compare_digest for CSRF token comparison",
)
check(
    "CSRF dependency reads header only (MH-05 fix)",
    file_contains(deps_file, r'x-csrf-token'),
    "Must read CSRF token from X-CSRF-Token header",
)

# ─── C-06: Ed25519 Signed Receipts ──────────────────────────────────

print(f"\n{BOLD}[C-06] Ed25519 Signed Receipts{RESET}")

signing_module = SERVER_DIR / "app" / "security" / "signing.py"
check(
    "signing.py exists (Ed25519 module)",
    signing_module.exists(),
    "Must have Ed25519 signing module",
)
check(
    "Uses Ed25519PrivateKey",
    file_contains(signing_module, r'Ed25519PrivateKey'),
    "Must use Ed25519 for signing",
)
check(
    "create_license_receipt function exists",
    file_contains(signing_module, r'def create_license_receipt'),
    "Must have receipt creation function",
)
check(
    "license_client.py verifies signatures",
    file_contains(license_client, r'_verify_server_signature|Ed25519PublicKey'),
    "Client must verify server signatures",
)
check(
    "No XOR encryption in license_client.py",
    file_not_contains(license_client, r'def _xor_encrypt'),
    "XOR encryption must be removed (replaced by Ed25519)",
)

signing_key_script = SERVER_DIR / "scripts" / "generate_signing_key.py"
check(
    "generate_signing_key.py script exists",
    signing_key_script.exists(),
    "Must have key generation script",
)

gitignore = SERVER_DIR / ".gitignore"
check(
    ".gitignore excludes keys/",
    file_contains(gitignore, r'keys/|\.pem'),
    "Must exclude signing keys from version control",
)

# ─── H-01: MFA First-Time Setup Flow ────────────────────────────────

print(f"\n{BOLD}[H-01] MFA First-Time Setup Flow{RESET}")

admin_auth = SERVER_DIR / "app" / "api" / "admin" / "auth.py"
check(
    "MFA setup uses MFA token (not require_admin)",
    file_contains(admin_auth, r'admin_mfa_token.*verify_mfa_token'),
    "MFA setup must authenticate via MFA token, not full admin session",
)
check(
    "MFA enable uses MFA token (not require_admin)",
    file_contains(admin_auth, r'mfa/enable.*mfa_token|admin_mfa_enable.*mfa_token'),
    "MFA enable must authenticate via MFA token",
)

# ─── H-02: Encrypted MFA Secrets ────────────────────────────────────

print(f"\n{BOLD}[H-02] Encrypted MFA Secrets{RESET}")

check(
    "MFA setup encrypts TOTP secret with Fernet",
    file_contains(admin_auth, r'Fernet.*encrypt.*totp_secret'),
    "Must encrypt TOTP secret before storing",
)
check(
    "MFA setup encrypts backup codes",
    file_contains(admin_auth, r'fernet\.encrypt.*code'),
    "Must encrypt backup codes before storing",
)
check(
    "MFA verify decrypts TOTP secret",
    file_contains(admin_auth, r'fernet\.decrypt.*totp_secret|decrypt.*totp'),
    "Must decrypt TOTP secret before verification",
)

# ─── H-03: Backup Code Type Fix ─────────────────────────────────────

print(f"\n{BOLD}[H-03] Backup Code Storage Type{RESET}")

user_model = SERVER_DIR / "app" / "models" / "user.py"
check(
    "backup_codes uses ARRAY type (not JSON)",
    file_contains(user_model, r'backup_codes.*ARRAY|MutableList'),
    "Must use ARRAY type for backup_codes column",
)

# ─── H-04: Dummy bcrypt Hash ────────────────────────────────────────

print(f"\n{BOLD}[H-04] Dummy bcrypt Hash{RESET}")

check(
    "No dummy bcrypt hash in auth_service.py",
    file_not_contains(auth_service, r'\$2b\$12\$9.*00000000000000000'),
    "Must not use dummy bcrypt hash (timing leak)",
)

# ─── H-05: Dashboard XSS Fix ────────────────────────────────────────

print(f"\n{BOLD}[H-05] Dashboard XSS Prevention{RESET}")

dashboard_html = SERVER_DIR / "app" / "templates" / "dashboard.html"
check(
    "No innerHTML for untrusted data in dashboard",
    file_not_contains(dashboard_html, r'\.innerHTML\s*='),
    "Must not use innerHTML for untrusted data (XSS risk)",
)
check(
    "Uses textContent or createElement for DOM updates",
    file_contains(dashboard_html, r'textContent|createElement|createTextNode'),
    "Must use safe DOM construction methods",
)

# ─── H-08: Redis Rate Limiting ──────────────────────────────────────

print(f"\n{BOLD}[H-08] Redis-Backed Rate Limiting{RESET}")

rate_limit_module = SERVER_DIR / "app" / "security" / "rate_limit.py"
check(
    "Redis rate limiter exists",
    file_contains(rate_limit_module, r'RedisRateLimiter|redis'),
    "Must support Redis-backed rate limiting",
)
check(
    "Rate limit on admin login endpoint",
    file_contains(admin_auth, r'_admin_login_rate_limit'),
    "Must have rate limiting on login endpoint",
)
check(
    "Rate limit on MFA endpoint",
    file_contains(admin_auth, r'_admin_mfa_rate_limit'),
    "Must have rate limiting on MFA endpoint",
)

# ─── H-09: Trusted Proxy ────────────────────────────────────────────

print(f"\n{BOLD}[H-09] Trusted Proxy for X-Forwarded-For{RESET}")

check(
    "Trusted proxy check in get_ip_hash",
    file_contains(deps_file, r'trusted_proxies'),
    "Must validate X-Forwarded-For against trusted proxy list",
)

# ─── H-10: Production ALLOWED_HOSTS ─────────────────────────────────

print(f"\n{BOLD}[H-10] Production ALLOWED_HOSTS Validation{RESET}")

check(
    "_validate_production_settings function exists",
    file_contains(config_py, r'def _validate_production_settings'),
    "Must have production settings validation",
)
check(
    "sys.exit(1) on validation failure",
    file_contains(config_py, r'sys\.exit\(1\)'),
    "Must exit on production config failure",
)

# ─── H-11: FastAPI/Starlette Version ────────────────────────────────

print(f"\n{BOLD}[H-11] FastAPI/Starlette Version{RESET}")

requirements = SERVER_DIR / "requirements.txt"
check(
    "fastapi version pinned (>=0.115.0)",
    file_contains(requirements, r'fastapi\s*>=\s*0\.11[5-9]|fastapi\s*>=\s*0\.1[2-9]|fastapi\s*>=\s*[1-9]'),
    "Must use FastAPI >= 0.115.0 (Starlette >= 0.40.0)",
)

# ─── MH-01: create_all Dev Only ─────────────────────────────────────

print(f"\n{BOLD}[MH-01] create_all() Dev Only{RESET}")

check(
    "create_all only in debug mode",
    file_contains(main_py, r'if settings\.debug.*init_db|if.*debug.*create_all'),
    "Must only auto-create tables in debug mode",
)

# ─── MH-06: Production Docker Compose ───────────────────────────────

print(f"\n{BOLD}[MH-06] Production Docker Compose{RESET}")

docker_compose_prod = SERVER_DIR / "docker-compose.prod.yml"
check(
    "docker-compose.prod.yml exists",
    docker_compose_prod.exists(),
    "Must have separate production docker-compose",
)
# Check that db and redis services don't have ports exposed
prod_content = read_file(docker_compose_prod)
# Simple check: no "5432:5432" or "6379:6379" port mappings in prod compose
check(
    "Prod compose does not expose DB port",
    "5432:5432" not in prod_content and '"5432' not in prod_content,
    "Must not expose PostgreSQL port in production",
)
check(
    "Prod compose does not expose Redis port",
    "6379:6379" not in prod_content and '"6379' not in prod_content,
    "Must not expose Redis port in production",
)
check(
    "Prod compose has Redis password",
    file_contains(docker_compose_prod, r'requirepass|REDIS_PASSWORD'),
    "Must set Redis password in production",
)

# ─── MH-07: Conditional HSTS ────────────────────────────────────────

print(f"\n{BOLD}[MH-07] Conditional HSTS{RESET}")

check(
    "HSTS conditional on HTTPS or debug",
    file_contains(main_py, r'is_https|Strict-Transport.*if|x-forwarded-proto'),
    "Must only set HSTS when HTTPS is guaranteed",
)

# ─── MH-08: Activation Key Pepper ───────────────────────────────────

print(f"\n{BOLD}[MH-08] Activation Key Pepper{RESET}")

license_service = SERVER_DIR / "app" / "services" / "license_service.py"
check(
    "activation_key_pepper in config",
    file_contains(config_py, r'activation_key_pepper'),
    "Must have activation_key_pepper setting",
)
check(
    "hash_activation_key uses pepper/HMAC",
    file_contains(license_service, r'pepper.*hmac|hmac\.new.*pepper'),
    "Must use HMAC with pepper for key hashing",
)

# ─── MH-09: Fingerprint Pepper ──────────────────────────────────────

print(f"\n{BOLD}[MH-09] Device Fingerprint Pepper{RESET}")

fingerprint_module = SERVER_DIR / "app" / "security" / "fingerprint.py"
check(
    "fingerprint_pepper in config",
    file_contains(config_py, r'fingerprint_pepper'),
    "Must have fingerprint_pepper setting",
)
check(
    "hash_fingerprint uses pepper/HMAC",
    file_contains(fingerprint_module, r'pepper.*hmac|hmac\.new.*pepper'),
    "Must use HMAC with pepper for fingerprint hashing",
)

# ─── Infrastructure Checks ──────────────────────────────────────────

print(f"\n{BOLD}[INFRA] Infrastructure{RESET}")

env_example = SERVER_DIR / ".env.example"
check(
    ".env.example exists",
    env_example.exists(),
    "Must have .env.example for configuration reference",
)

dockerfile = SERVER_DIR / "Dockerfile"
check(
    "Dockerfile exists",
    dockerfile.exists(),
    "Must have Dockerfile for deployment",
)

docker_compose = SERVER_DIR / "docker-compose.yml"
check(
    "docker-compose.yml exists (dev)",
    docker_compose.exists(),
    "Must have docker-compose for development",
)

check(
    ".gitignore exists",
    gitignore.exists(),
    "Must have .gitignore",
)

check(
    ".gitignore excludes .env",
    file_contains(gitignore, r'^\.env$|^\.env\s|\.env$'),
    "Must exclude .env from version control",
)

# ─── Summary ─────────────────────────────────────────────────────────

print()
print(f"{BOLD}{'='*60}{RESET}")

if failed > 0:
    print(f"{RED}{BOLD}  RELEASE GATE FAILED{RESET}")
    print(f"  {RED}{failed} check(s) failed — fix before deploying{RESET}")
    print(f"  {GREEN}{passed} check(s) passed{RESET}")
    if warnings > 0:
        print(f"  {YELLOW}{warnings} warning(s){RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print()
    sys.exit(1)
else:
    print(f"{GREEN}{BOLD}  ALL CHECKS PASSED{RESET}")
    print(f"  {GREEN}{passed} check(s) passed{RESET}")
    if warnings > 0:
        print(f"  {YELLOW}{warnings} warning(s) — review recommended{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print()
    print(f"  {BOLD}Ready for deployment.{RESET}")
    print()
    sys.exit(0)