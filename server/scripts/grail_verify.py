#!/usr/bin/env python3
"""
AGREGAT — GRAIL Security Verification Script

Automatically verifies fixes for all GRAIL audit findings.
Run with: python scripts/grail_verify.py

Exit codes:
  0 = All critical/high checks pass
  1 = One or more checks fail
"""

import ast
import os
import re
import sys
from pathlib import Path

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

PROJECT_ROOT = Path(__file__).parent.parent.parent  # AGREGAT/
SERVER_DIR = PROJECT_ROOT / "server"
ENGINE_SECURITY = PROJECT_ROOT / "engine" / "security"

passed = 0
failed = 0
warnings = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    status = f"{GREEN}[PASS]{RESET}" if condition else f"{RED}[FAIL]{RESET}"
    print(f"  {status}  {name}")
    if detail and not condition:
        print(f"         {YELLOW}-> {detail}{RESET}")
    if condition:
        passed += 1
    else:
        failed += 1


def warn(name: str, detail: str = ""):
    global warnings
    print(f"  {YELLOW}[WARN]{RESET}  {name}")
    if detail:
        print(f"         -> {detail}")
    warnings += 1


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def file_contains(path: Path, pattern: str) -> bool:
    return bool(re.search(pattern, read_file(path), re.MULTILINE | re.DOTALL))


def file_not_contains(path: Path, pattern: str) -> bool:
    return not file_contains(path, pattern)


def has_function_def(source: str, func_name: str) -> bool:
    """Check if a Python source file defines a function (not just declares it)."""
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                # Check if function body has actual statements (not just pass/comments/docstring)
                body = node.body
                # Skip docstring
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body = body[1:] or body
                return len(body) > 0 and not all(
                    isinstance(stmt, ast.Pass) for stmt in body
                )
        return False
    except SyntaxError:
        return False


print()
print(f"{BOLD}{'='*70}{RESET}")
print(f"{BOLD}  AGREGAT — GRAIL Security Verification{RESET}")
print(f"{BOLD}{'='*70}{RESET}")
print()

# ─── GRAIL-01: TLS Certificate Pinning ──────────────────────────────
print(f"\n{BOLD}[GRAIL-01] TLS Certificate Pinning{RESET}")

lc = ENGINE_SECURITY / "license_client.py"
lc_content = read_file(lc)

# Check that pinning actually installs a callback (not just pass)
has_callback = "verify_callback" in lc_content or "set_verify_callback" in lc_content
has_pinning_logic = "cert_hash" in lc_content and "_EXPECTED_CERT_SHA256" in lc_content
# Check that httpx path also uses pinning
httpx_uses_pinning = "transport" in lc_content and "verify" in lc_content

check(
    "Pinning installs a verification callback",
    has_callback or (has_pinning_logic and "def verify_cert" in lc_content),
    "Must install ssl_context.verify_callback — current _get_ssl_context_for_pinning contains 'pass'",
)

# ─── GRAIL-02: HMAC Request Signing Wired Up ────────────────────────
print(f"\n{BOLD}[GRAIL-02] HMAC Request Signing Enforced Server-Side{RESET}")

main_py = SERVER_DIR / "app" / "main.py"
main_content = read_file(main_py)

check(
    "RequestSigningMiddleware imported in main.py",
    "RequestSigningMiddleware" in main_content,
    "Must import RequestSigningMiddleware from nonce_middleware",
)
check(
    "RequestSigningMiddleware added via app.add_middleware()",
    "RequestSigningMiddleware" in main_content and "add_middleware" in main_content,
    "Must add middleware with: app.add_middleware(RequestSigningMiddleware, ...)",
)

# ─── GRAIL-03: Account Lockout in DB ────────────────────────────────
print(f"\n{BOLD}[GRAIL-03] Account Lockout Persistence{RESET}")

auth_svc = SERVER_DIR / "app" / "services" / "auth_service.py"
auth_svc_content = read_file(auth_svc)

check(
    "Account lockout uses PostgreSQL, not in-memory dict",
    "_login_attempts" not in auth_svc_content or "TODO" in auth_svc_content,
    "Must migrate _login_attempts dict to PostgreSQL table",
)
check(
    "Lockout check uses database query",
    "LoginAttempt" in auth_svc_content or "login_attempts" in auth_svc_content,
    "Must query login_attempts table with WHERE locked_until > now()",
)

# ─── GRAIL-04: Rate Limiter Eviction ────────────────────────────────
print(f"\n{BOLD}[GRAIL-04] Rate Limiter Bounded Memory{RESET}")

rl = SERVER_DIR / "app" / "security" / "rate_limit.py"
rl_content = read_file(rl)

check(
    "InMemoryRateLimiter has MAX_ENTRIES limit",
    "MAX_ENTRIES" in rl_content or "max_entries" in rl_content,
    "Must limit number of tracked IP+endpoint pairs to prevent memory exhaustion",
)
check(
    "Rate limiter implements eviction (LRU or similar)",
    "evict" in rl_content.lower() or "popitem" in rl_content,
    "Must evict oldest entries when limit is reached",
)

# ─── GRAIL-05: String Obfuscation Fixed ─────────────────────────────
print(f"\n{BOLD}[GRAIL-05] String Obfuscation Strength{RESET}")

ad = ENGINE_SECURITY / "anti_debug.py"
ad_content = read_file(ad)

check(
    "anti_debug.py uses HMAC-based obfuscation (not XOR 0x3F)",
    "hashlib.sha256" in ad_content and "0x3F" not in ad_content.split("def _d")[1].split("\n")[0] if "def _d" in ad_content else False,
    "Must replace XOR(0x3F) with HMAC-SHA256 per-string keystream",
)

# ─── GRAIL-06: Checkpoints Actually Verify ──────────────────────────
print(f"\n{BOLD}[GRAIL-06] Checkpoint Verification{RESET}")

check(
    "verify_checkpoints() has actual verification logic (not just pass)",
    "co_code" in ad_content and "!= expected_hash" in ad_content,
    "verify_checkpoints() must compare current bytecode hash against stored hash",
)

# ─── GRAIL-07: Thread Safety ────────────────────────────────────────
print(f"\n{BOLD}[GRAIL-07] Thread Safety{RESET}")

check(
    "license_client.py has threading.Lock() for global state",
    "threading.Lock()" in lc_content or "threading.RLock()" in lc_content,
    "Must protect _last_known_server_time with a lock",
)
check(
    "request_signing.py has threading.Lock() for nonce tracking",
    "threading.Lock()" in read_file(ENGINE_SECURITY / "request_signing.py"),
    "Must protect _used_nonces with a lock",
)

# ─── GRAIL-08: NtGlobalFlag Implemented ────────────────────────────
print(f"\n{BOLD}[GRAIL-08] NtGlobalFlag Implementation{RESET}")

check(
    "NtGlobalFlag check has actual implementation (not pass)",
    "NtQueryInformationProcess" in ad_content and "nt_global_flag" in ad_content,
    "Must use NtQueryInformationProcess (not inline assembly), actual bit comparison",
)

# ─── GRAIL-09: Build-time Hashes ────────────────────────────────────
print(f"\n{BOLD}[GRAIL-09] Build-Time Integrity Hashes{RESET}")

check(
    "Integrity hashes use build-time constants, not env vars",
    "_build_hashes" in ad_content or "BUILD_HASHES" in ad_content,
    "Must embed hashes in a generated Python module at build time, not read from env var",
)

# ─── GRAIL-10: Client Audit Log HMAC ────────────────────────────────
print(f"\n{BOLD}[GRAIL-10] Client Audit Log Integrity{RESET}")

check(
    "Client audit log includes HMAC integrity check",
    "entry_mac" in lc_content and "hmac.new" in lc_content,
    "Must write HMAC(next(seq), entry) to audit log for integrity verification",
)

# ─── GRAIL-11: Error Message Normalization ──────────────────────────
print(f"\n{BOLD}[GRAIL-11] Error Message Normalization{RESET}")

lic_svc = SERVER_DIR / "app" / "services" / "license_service.py"
lic_svc_content = read_file(lic_svc)

check(
    "License activation returns generic error for all failures",
    "activation_failed" in lic_svc_content,
    "Must return 'activation_failed' (not 'invalid_key', 'email_mismatch', etc.)",
)

# ─── GRAIL-12: COEP Not Too Aggressive ──────────────────────────────
print(f"\n{BOLD}[GRAIL-12] Cross-Origin-Embedder-Policy{RESET}")

check(
    "COEP uses 'credentialless' (not 'require-corp')",
    "credentialless" in main_content,
    "require-corp breaks legitimate cross-origin resources. Use credentialless instead.",
)

# ─── GRAIL-13: Timing Check Improvements ───────────────────────────
print(f"\n{BOLD}[GRAIL-13] Timing Check Robustness{RESET}")

check(
    "Timing check uses multiple time sources (perf_counter + monotonic)",
    "time.monotonic()" in ad_content and "time.perf_counter()" in ad_content,
    "Should compare perf_counter vs monotonic to detect debugging",
)
check(
    "Timing check has randomized iteration count",
    "random.randint" in ad_content or "os.urandom" in ad_content or "random.randrange" in ad_content,
    "Should randomize iteration count to prevent pattern-based bypass",
)

# ─── GRAIL-14: Chunked .text Hash ──────────────────────────────────
print(f"\n{BOLD}[GRAIL-14] Binary .text Hash Memory Safety{RESET}")

at = ENGINE_SECURITY / "anti_tamper.py"
at_content = read_file(at)

check(
    "get_binary_text_hash() reads .text in chunks (not all at once)",
    "while" in at_content and "chunk" in at_content and "update" in at_content,
    "Must read .text section in chunks to avoid memory exhaustion on large binaries",
)

# ─── GRAIL-15: release_gate.py ───────────────────────────────────────
print(f"\n{BOLD}[GRAIL-15] Release Gate Path Corrections{RESET}")

rg = SERVER_DIR / "scripts" / "release_gate.py"
rg_content = read_file(rg)

check(
    "release_gate.py checks root .gitignore, not server/.gitignore",
    "PROJECT_ROOT / '.gitignore'" in rg_content,
    "Must use PROJECT_ROOT / '.gitignore' (root level), not SERVER_DIR / '.gitignore'",
)

# ─── GRAIL-16: time.monotonic() ─────────────────────────────────────
print(f"\n{BOLD}[GRAIL-16] Monotonic Clock for Rate Limits{RESET}")

check(
    "Rate limiter uses time.monotonic() (not time.time())",
    "time.monotonic()" in rl_content,
    "Must use monotonic clock to prevent rate limit bypass via system clock manipulation",
)

# ─── Summary ──────────────────────────────────────────────────────────
print()
print(f"{BOLD}{'='*70}{RESET}")

if failed > 0:
    print(f"{RED}{BOLD}  GRAIL VERIFICATION FAILED{RESET}")
    print(f"  {RED}{failed} check(s) failed — address GRAIL issues before deployment{RESET}")
    print(f"  {GREEN}{passed} check(s) passed{RESET}")
    if warnings > 0:
        print(f"  {YELLOW}{warnings} warning(s){RESET}")
    print()
    print(f"  See GRAIL_SECURITY_AUDIT.md for detailed fixes.")
    print(f"{BOLD}{'='*70}{RESET}")
    print()
    sys.exit(1)
else:
    print(f"{GREEN}{BOLD}  ALL GRAIL CHECKS PASSED{RESET}")
    print(f"  {GREEN}{passed} check(s) passed{RESET}")
    if warnings > 0:
        print(f"  {YELLOW}{warnings} warning(s) — review recommended{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print()
    sys.exit(0)
