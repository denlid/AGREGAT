# 🏆 AGREGAT — GRAIL Security Audit (Beyond the Existing Report)

**Date:** 2026-06-07  
**Methodology:** Deep source-code static analysis (~5000 lines reviewed), STRIDE per-module, zero-trust architecture review  
**Scope:** Client (engine/security/*), Server (server/app/*), Infra (Docker, DB, CI)  
**Goal:** Find what the existing SECURITY_AUDIT.md missed — the "Grail" level

---

## TL;DR — The Existing Audit Is Good, But Missed Critical Gaps

The existing `SECURITY_AUDIT.md` correctly identified ~30 issues. **However**, several claimed fixes are **incomplete or theatrical** (security theater). Below I expose what's still broken after all those "FIXED" checkmarks.

---

## 🔴 GRAIL-01: TLS Certificate Pinning Is NOT Actually Enforced

**File:** `engine/security/license_client.py:502-527`  
**Status in existing audit:** ✅ "MED-01 FIXED — Pinning enabled by default with bundled cert fingerprint"  
**Reality:** ❌ **NOT FIXED — the implementation is incomplete**

The function `_get_ssl_context_for_pinning()` creates an SSL context with `CERT_REQUIRED` but **never installs a callback or custom verifier**:

```python
def _get_ssl_context_for_pinning():
    ctx = ssl.create_default_context(cafile=ca_file)
    if _EXPECTED_CERT_SHA256:
        original_verify = ctx.verify_mode
        ctx.verify_mode = ssl.CERT_REQUIRED
        # We'll verify the fingerprint in the HTTP client callback
        # For urllib, we use a custom HTTPSConnection
        pass  # <--- THIS NEVER HAPPENS!
    return ctx
```

Furthermore, the httpx client path (`license_client.py:691-692`) **doesn't use this context at all**:
```python
with _httpx.Client(timeout=10.0, verify=ssl_verify) as client:
```
It uses `certifi.where()` or system CA — no pinning.

**Impact:** An attacker with a compromised CA can MITM all license verification traffic. Pinning = 0%.

### Fix:
```python
# In license_client.py, replace _get_ssl_context_for_pinning():
import ssl
from cryptography.hazmat.primitives import hashes

def _get_ssl_context_for_pinning():
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    if _EXPECTED_CERT_SHA256:
        def verify_cert(conn, cert, errno, depth, preverify_ok):
            if depth == 0:  # Leaf cert
                der = cert[0].tobytes()  # pyopenssl
                cert_hash = hashlib.sha256(der).hexdigest().upper()
                expected = _EXPECTED_CERT_SHA256.upper().replace(":", "").replace("SHA256=", "")
                if cert_hash != expected:
                    raise ssl.CertificateError(
                        f"Certificate pinning mismatch. Expected SHA256:{expected}, got SHA256:{cert_hash}"
                    )
            return preverify_ok
        ctx.verify_callback = verify_cert

    return ctx

# For httpx path, use a custom transport:
if HAS_HTTPX:
    import httpx
    ctx = _get_ssl_context_for_pinning()
    transport = httpx.HTTPTransport(verify=ctx)
    with httpx.Client(timeout=10.0, transport=transport) as client:
```

---

## 🔴 GRAIL-02: HMAC Request Signing Is Client-Only — Server Never Verifies

**File:** `server/app/security/nonce_middleware.py` vs `server/app/main.py`  
**Status in existing audit:** ✅ Not flagged  
**Reality:** ❌ **Architecture gap**

The `RequestSigningMiddleware` is defined but **never added to the FastAPI app** in `main.py`. The client signs every request with HMAC-SHA256, but the server happily accepts unsigned requests.

I checked `server/app/main.py` — there is no `app.add_middleware(RequestSigningMiddleware, ...)` anywhere.

**Impact:** All "E2E-01: HMAC request signing" claims are theatrical. No MITM defense beyond TLS. No replay protection. No request integrity.

### Fix:
```python
# server/app/main.py — add:
from app.security.nonce_middleware import RequestSigningMiddleware

# Add after other middleware (but before routes)
app.add_middleware(RequestSigningMiddleware, redis_client=redis_client)
```

---

## 🔴 GRAIL-03: Account Lockout Is In-Memory — Bypassed by Restart / Multi-Instance

**File:** `server/app/services/auth_service.py:49-50`  
**Status in existing audit:** ✅ "HIGH-09 FIXED — Email + audit log on lockout"  
**Reality:** ❌ **Lockout mechanism itself is still broken**

```python
# Line 49-50 — the comment even admits it:
# NOTE: For multi-instance deployment, migrate to PostgreSQL or Redis.
_login_attempts: dict[str, dict] = {}
```

This means:
- **Server restart** → lockout reset (all brute-force attempts forgotten)
- **Multiple server instances** → each has its own attempt counter (5 attempts per *instance*, not per *user*)
- **Horizontal scaling** → attacker gets N * 5 attempts where N = instance count

### Fix:
```python
# Move lockout to PostgreSQL (use the existing rate_limit_events table or add a new one)
# Create table:
CREATE TABLE IF NOT EXISTS login_attempts (
    email TEXT NOT NULL,
    attempt_count INT NOT NULL DEFAULT 1,
    locked_until TIMESTAMPTZ,
    first_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

# In auth_service.py, use DB queries instead of in-memory dict:
async def _check_account_lockout(db: AsyncSession, email: str) -> None:
    stmt = select(LoginAttempt).where(
        LoginAttempt.email == email,
        LoginAttempt.locked_until > func.now()
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise ValueError("account_locked")
```

---

## 🔴 GRAIL-04: InMemoryRateLimiter Has No Eviction — Memory Exhaustion DoS

**File:** `server/app/security/rate_limit.py:38-39`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ❌ **Unbounded memory growth**

```python
class InMemoryRateLimiter:
    def __init__(self):
        self._windows: dict[str, _WindowEntry] = defaultdict(_WindowEntry)
```

Every unique `{ip}:{endpoint}` pair creates a new entry. With 65,535 possible source IPs (IPv4 behind NAT) × 10 endpoints = 655,350 entries. Each entry holds timestamps. No eviction. No max size. No LRU.

**Attack:** Attacker sends 1 request per second from rotating IPs (via proxies or botnet). After ~1M unique IPs, the server runs out of memory.

### Fix:
```python
from collections import OrderedDict
import threading

class BoundedRateLimiter(InMemoryRateLimiter):
    MAX_ENTRIES = 100_000

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._access_order = OrderedDict()

    def consume(self, subject, endpoint, max_requests, window_seconds):
        with self._lock:
            key = f"{subject}:{endpoint}"
            if key not in self._access_order and len(self._access_order) >= self.MAX_ENTRIES:
                # Evict oldest
                oldest_key, _ = self._access_order.popitem(last=False)
                self._windows.pop(oldest_key, None)
            self._access_order[key] = time.time()
            return super().consume(subject, endpoint, max_requests, window_seconds)
```

---

## 🟠 GRAIL-05: String Obfuscation Is STILL Weak XOR(0x3F) in `anti_debug.py`

**File:** `engine/security/anti_debug.py:27-32`  
**Status in existing audit:** ✅ "HIGH-08 FIXED — HMAC-SHA256 per-string keystream"  
**Reality:** ❌ **anti_debug.py was NEVER updated**

```python
# Line 27-32 — STILL the old XOR(0x3F)!
def _d(s: str) -> str:
    try:
        return bytes(a ^ 0x3F for a in base64.b64decode(s)).decode('utf-8')
    except Exception:
        return ""
```

The audit report claims this was fixed with "HMAC-SHA256 per-string keystream + Nuitka --obfuscate-string-literals", but only `license_manager.py` was updated. `anti_debug.py` still uses the trivially reversible single-byte XOR.

Since `anti_debug.py` contains critical obfuscated strings (VM detection patterns, sandbox names, env var names), reversing them gives attackers insight into the anti-tamper logic.

### Fix:
```python
# anti_debug.py — use the same HMAC-SHA256 approach
_OBFUSCATION_KEY = hashlib.sha256(
    b"ANTI_DEBUG_2026" + struct.pack(">Q", os.getpid() & 0xFFFFFFFF)
).digest()[:16]

def _d(s: str) -> str:
    try:
        raw = base64.b64decode(s)
        key = _OBFUSCATION_KEY
        return bytes(a ^ key[i % len(key)] for i, a in enumerate(raw)).decode('utf-8')
    except Exception:
        return ""
```

---

## 🟠 GRAIL-06: `verify_checkpoints()` Is Complete No-Op

**File:** `engine/security/anti_debug.py:586-598`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ❌ **Security theater**

```python
def verify_checkpoints() -> tuple[bool, str]:
    for name, expected_hash in _CHECKPOINTS.items():
        # The checkpoint was set at function entry, so we need
        # to re-check from within the same function context.
        # This is called periodically by the watchdog.
        pass  # <--- NEVER ACTUALLY VERIFIES!
    return True, "ok"
```

The checkpoint system is a great concept but **never implemented**. Calls to `verify_checkpoints()` always return `True, "ok"`. An attacker can patch any function bytecode without detection.

### Fix:
```python
import sys

def verify_checkpoints() -> tuple[bool, str]:
    for name, expected_hash in list(_CHECKPOINTS.items()):
        try:
            # Get current bytecode hash of the function that set this checkpoint
            frame = sys._getframe(1)  # In real use, pass module+func name
            # Alternative: iterate through all registered modules
            for mod_name, func_name in _CHECKPOINT_REGISTRY.get(name, []):
                module = sys.modules.get(mod_name)
                if module:
                    func = getattr(module, func_name, None)
                    if func and hasattr(func, "__code__"):
                        current = hashlib.sha256(func.__code__.co_code).hexdigest()
                        if current != expected_hash:
                            return False, f"checkpoint_failed:{name}"
        except Exception:
            pass
    return True, "ok"
```

---

## 🟠 GRAIL-07: Multi-Threading Data Races Everywhere

**Files:** Multiple  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ❌ **Multiple unprotected global state mutations**

| Location | Variable | Risk |
|---|---|---|
| `license_client.py:568-569` | `_last_known_server_time` | Race condition on time manipulation detection |
| `anti_debug.py:524` | `_CHECKPOINTS` | Race condition on patching detection |
| `request_signing.py:43,51-54` | `_used_nonces` | Nonce reuse via race (potential replay) |
| `auth_service.py:49-50` | `_login_attempts` | Lockout bypass via race (HB-FAIL) attack |
| `rate_limit.py:39` | `_windows` | Counts may exceed limits due to race |

### Fix:
Add `threading.Lock()` to all global mutable state:
```python
# Add locks to each module
_license_lock = threading.Lock()
_nonce_lock = threading.Lock()

# Wrap all mutations:
with _license_lock:
    _last_known_server_time = server_time_iso
```

---

## 🟠 GRAIL-08: Weak Anti-Debug — NtGlobalFlag Check = NotImplemented

**File:** `engine/security/anti_debug.py:107-117`  
**Status in existing audit:** ✅ "P0-3: NtGlobalFlag check" listed as implemented  
**Reality:** ❌ **Stub — still `pass`**

```python
# P0-3: NtGlobalFlag check — set when process is debugged
try:
    is_64bit = struct.calcsize("P") == 8
    peb_offset = 0x60 if is_64bit else 0x30
    # Read PEB via TEB...
    # NtGlobalFlag is at PEB+0x68 (x64) or PEB+0x44 (x86)
    pass  # Inline assembly not safe for AV; use NtQueryInfoProcess instead
```

The code admits inline assembly is unsafe but never implemented the safe alternative. This anti-debug method provides 0 protection.

### Fix:
```python
# Use NtQueryInformationProcess instead of inline assembly
try:
    from ctypes import wintypes
    ntdll = ctypes.windll.ntdll
    PROCESSINFOCLASS = 0  # ProcessBasicInformation
    pbi = ctypes.create_string_buffer(48)  # PROCESS_BASIC_INFORMATION
    ret = ntdll.NtQueryInformationProcess(
        ctypes.c_void_p(-1), PROCESSINFOCLASS,
        pbi, ctypes.sizeof(pbi), None
    )
    if ret == 0:
        # PEB is at offset 8 in PROCESS_BASIC_INFORMATION
        peb_ptr = struct.unpack_from("P", pbi, 8)[0]
        # NtGlobalFlag is at PEB+0x68 (x64) or PEB+0x44 (x86)
        flag_offset = 0x68 if struct.calcsize("P") == 8 else 0x44
        nt_global_flag = ctypes.c_uint32.from_address(peb_ptr + flag_offset).value
        FLG_HEAP_ENABLE_TAIL_CHECK = 0x10
        FLG_HEAP_ENABLE_FREE_CHECK = 0x20
        FLG_HEAP_VALIDATE_PARAMETERS = 0x40
        if nt_global_flag & (FLG_HEAP_ENABLE_TAIL_CHECK | FLG_HEAP_ENABLE_FREE_CHECK | FLG_HEAP_VALIDATE_PARAMETERS):
            return True  # Debugger detected via NtGlobalFlag
except Exception:
    pass
```

---

## 🟠 GRAIL-09: Integrity Hash Check Can Be Bypassed via Env Var

**File:** `engine/security/anti_debug.py:295-306`  
**Status in existing audit:** ✅ "HIGH-07 FIXED — SHA-256 hash comparison with build-time embedded hashes"  
**Reality:** ⚠️ **The env var approach is bypassable**

```python
_EXPECTED_HASHES_ENV = os.environ.get("AGREGAT_SECURITY_HASHES", "")
```

In production (Nuitka-compiled), environment variables can be set at runtime. An attacker can:
1. Modify `license_client.py` to bypass checks
2. Run: `AGREGAT_SECURITY_HASHES=old_hash AGREGAT_DEV_MODE=1 ./agregat.exe`

### Fix:
Embed hashes in the compiled binary at build time, not in env vars:
```python
# In the build script (build_final_windows.cmd):
# 1. Compute hashes of all security files
# 2. Write them as a Python constant into a generated module
# 3. Import that module in anti_debug.py

# Generated file: engine/security/_build_hashes.py (gitignored)
# Content:
BUILD_HASHES = {
    "license_manager.py": "abc123...",
    "license_client.py": "def456...",
}
```

---

## 🟠 GRAIL-10: Client Audit Log Has No Integrity Protection

**File:** `engine/security/license_client.py:545-563`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ❌ **Local audit log can be deleted/modified by attacker**

```python
def _log_client_event(action: str, **kwargs):
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, **kwargs}
    with open(_CLIENT_AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
```

No HMAC, no signing, no append-only mode. An attacker with local access can:
1. Delete the entire audit log → no evidence
2. Modify entries → cover tracks  
3. Plant fake entries → frame someone

### Fix:
```python
import hmac

_AUDIT_KEY = hashlib.sha256(_CACHE_KEY_PREFIX + b"AUDIT_LOG" + _CACHE_KEY_SALT).digest()

def _log_client_event(action: str, **kwargs):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, **kwargs}
        entry_str = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        # HMAC integrity
        entry_mac = hmac.new(_AUDIT_KEY, entry_str.encode(), hashlib.sha256).hexdigest()
        with open(_CLIENT_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{entry_mac}:{entry_str}\n")
    except Exception:
        pass

def verify_audit_log() -> bool:
    """Verify integrity of entire audit log."""
    try:
        if not _CLIENT_AUDIT_LOG.exists():
            return True
        lines = _CLIENT_AUDIT_LOG.read_text().splitlines()
        for line in lines:
            if ":" not in line:
                return False
            mac, entry_str = line.split(":", 1)
            expected_mac = hmac.new(_AUDIT_KEY, entry_str.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, expected_mac):
                return False
        return True
    except Exception:
        return False
```

---

## 🟡 GRAIL-11: Error Messages Leak User Enumeration Info

**File:** `server/app/services/license_service.py:192-207`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ❌ **Different error messages for different failure modes**

The activation endpoint returns distinct errors:
- `"invalid_key"` → key doesn't exist
- `"license_expired"` → key exists but expired
- `"email_mismatch"` → key exists, email doesn't match
- `"user_suspended"` → user exists but suspended
- `"device_limit_reached"` → user exists, device limit hit

An attacker can enumerate valid license keys and emails by observing the error.

### Fix:
```python
# Return a single generic error for all activation failures:
raise HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="activation_failed",  # Same message for ALL failure modes
)
# Log the actual reason internally:
await log_event(db, action="client.activation_failed", payload={"real_reason": actual_error, ...})
```

---

## 🟡 GRAIL-12: Cross-Origin-Embedder-Policy: require-corp Is Too Aggressive

**File:** `server/app/main.py:293`  
**Status in existing audit:** ✅ "MED-06 FIXED"  
**Reality:** ⚠️ **Will break legitimate cross-origin resources**

```python
response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
```

`require-corp` means **any** cross-origin resource (images, scripts, fonts) without `Cross-Origin-Resource-Policy: cross-origin` will be blocked. This is a very strict policy that breaks:
- CDN-hosted fonts
- Third-party analytics (even if blocked by CSP)
- Browser extensions
- Any external resource

### Fix:
```python
# Use credentialless (available in Chrome 110+) instead of require-corp:
response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
# This blocks cross-origin resources with credentials but allows anonymous ones.
# Much more compatible while still providing isolation.
```

---

## 🟡 GRAIL-13: Timing Anti-Debug Loop Is Trivially Bypassed

**File:** `engine/security/anti_debug.py:204-215`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ⚠️ **Pattern is detectable and bypassable**

```python
def timing_check(iterations=500000, max_seconds=2.0):
    start = time.perf_counter()
    _dummy = 0
    for i in range(iterations):
        _dummy ^= (i * 7 + 13) & 0xFF
    elapsed = time.perf_counter() - start
    return elapsed > max_seconds
```

A sophisticated attacker can:
1. NOP-out the timing check (replace with `return False`)
2. Use a debugger that doesn't slow execution (hardware breakpoints, time-travel debugging)
3. Hook `time.perf_counter()` to return fake values

### Fix — Add jitter and multiple timing methods:
```python
import random

def timing_check() -> bool:
    """Anti-debug timing check with randomized iteration count and multiple methods."""
    results = []
    for _ in range(3):
        iterations = random.randint(300000, 700000)
        start_perf = time.perf_counter()
        start_mono = time.monotonic()
        _dummy = 0
        for i in range(iterations):
            _dummy ^= (i * 7 + 13) & 0xFF
        elapsed_perf = time.perf_counter() - start_perf
        elapsed_mono = time.monotonic() - start_mono
        # Both should be approximately equal — large discrepancy = debugger
        if abs(elapsed_perf - elapsed_mono) > 0.5:
            results.append(True)
        elif elapsed_perf > 2.0 or elapsed_mono > 2.0:
            results.append(True)
        else:
            results.append(False)
    # Return True if majority of checks detected debugger
    return sum(results) >= 2
```

---

## 🟡 GRAIL-14: `get_binary_text_hash()` Can Crash on Large Binaries

**File:** `engine/security/anti_tamper.py:199-201`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ⚠️ **Memory allocation can fail for large .text sections**

```python
text_data = ctypes.string_at(text_start, virt_size)
```

If `.text` is >2GB (e.g., Nuitka-compiled with many libraries), `ctypes.string_at()` allocates `virt_size` bytes in a single contiguous block. This can:
- Crash with `MemoryError`
- Take excessive time (DoS vector for legitimate users)

### Fix:
```python
def get_binary_text_hash() -> Optional[str]:
    MAX_TEXT_SIZE = 100 * 1024 * 1024  # 100 MB limit
    h = hashlib.sha256()
    # ... parse PE, find .text section ...
    if virt_size > MAX_TEXT_SIZE:
        # Read in chunks
        CHUNK = 64 * 1024
        offset = 0
        while offset < virt_size:
            chunk_size = min(CHUNK, virt_size - offset)
            chunk = ctypes.string_at(text_start + offset, chunk_size)
            h.update(chunk)
            offset += chunk_size
        return h.hexdigest()
    else:
        text_data = ctypes.string_at(text_start, virt_size)
        return hashlib.sha256(text_data).hexdigest()
```

---

## 🟢 GRAIL-15: `release_gate.py` Checks Wrong `.gitignore`

**File:** `server/scripts/release_gate.py:221`  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ⚠️ **Checks `server/.gitignore` instead of root `.gitignore`**

```python
gitignore = SERVER_DIR / ".gitignore"  # server/.gitignore — may not exist!
```

There is no `server/.gitignore` file. The root `.gitignore` is what matters. The release gate check for `.gitignore` will silently pass (file_not_contains returns True for missing file), giving a false sense that signing keys are excluded.

### Fix:
```python
gitignore = PROJECT_ROOT / ".gitignore"  # Root .gitignore, not server/.gitignore
```

---

## 🟢 GRAIL-16: `time.time()` Used Instead of `time.monotonic()` for Rate Limiting

**File:** `server/app/security/rate_limit.py` (multiple places)  
**Status in existing audit:** ❌ Not flagged  
**Reality:** ⚠️ **System clock changes can extend or shrink rate limit windows**

`time.time()` returns wall clock time, which can:
- Jump forward (NTP sync) → old entries evicted prematurely → rate limit bypass
- Jump backward (manual adjustment) → entries stay longer → false positives

### Fix:
```python
# Use time.monotonic() for all rate limit calculations
import time as _time

class InMemoryRateLimiter:
    def _cleanup(self, key: str, window_seconds: float) -> None:
        entry = self._windows[key]
        cutoff = _time.monotonic() - window_seconds  # Use monotonic, not time()
        entry.timestamps = [t for t in entry.timestamps if t > cutoff]
```

---

## 🟢 GRAIL-17: Timing Check in `anti_debug.py` Uses `perf_counter()` Only

**File:** `engine/security/anti_debug.py:210-215`  
**Impact:** Attacker can hook `perf_counter()` to bypass timing detection. See GRAIL-13 fix above.

---

## 🏆 Summary: What the Existing Audit Got Wrong

| Claim in SECURITY_AUDIT.md | Reality | Actual Severity |
|---|---|---|
| MED-01: "TLS certificate pinning enabled" | ❌ Pinning code is `pass` — never enforces | **CRITICAL** |
| HIGH-08: "String obfuscation fixed" | ❌ Only half-fixed; `anti_debug.py` still uses XOR(0x3F) | **HIGH** |
| HIGH-07: "SHA-256 hash comparison" | ⚠️ Hash set via env var — trivial bypass | **HIGH** |
| P0-3: "NtGlobalFlag check" | ❌ Code is `pass` (inline assembly comment) | **HIGH** |
| E2E-01: "HMAC request signing" | ❌ Client signs, server NEVER verifies | **CRITICAL** |
| HIGH-09: "Account lockout notification" | ⚠️ Notification added, lockout is still in-memory | **HIGH** |
| (not flagged) | ❌ InMemoryRateLimiter has no eviction — memory DoS | **CRITICAL** |
| (not flagged) | ❌ Thread safety — multiple global state races | **HIGH** |
| (not flagged) | ❌ `verify_checkpoints()` is no-op | **MEDIUM** |
| MED-06: "COEP: require-corp" | ⚠️ Breaks cross-origin resources | **MEDIUM** |
| (not flagged) | ❌ Error messages leak user enumeration | **MEDIUM** |
| (not flagged) | ❌ Client audit log has no HMAC integrity | **MEDIUM** |
| (not flagged) | ⚠️ `release_gate.py` checks non-existent file | **LOW** |
| (not flagged) | ⚠️ `time.time()` not `monotonic()` for rate limits | **LOW** |

---

## 🎯 Priority Action Plan (Highest ROI for $0)

### Phase G1 — Immediate (Fix before any deployment, ~2 hours)
1. **GRAIL-02**: Wire up `RequestSigningMiddleware` in `main.py` (5 min)
2. **GRAIL-01**: Implement actual TLS cert pinning callback (30 min)
3. **GRAIL-03**: Move account lockout to PostgreSQL (45 min)
4. **GRAIL-04**: Add eviction to `InMemoryRateLimiter` (15 min)

### Phase G2 — High Priority (~3 hours)
5. **GRAIL-05**: Fix string obfuscation in `anti_debug.py` (15 min)
6. **GRAIL-07**: Add locks to all global mutable state (30 min)
7. **GRAIL-08**: Implement `NtGlobalFlag` check properly (20 min)
8. **GRAIL-09**: Move integrity hashes to build-time constants (30 min)
9. **GRAIL-11**: Normalize error messages for user enumeration prevention (15 min)

### Phase G3 — Medium Priority (~2 hours)
10. **GRAIL-06**: Implement actual checkpoint verification (30 min)
11. **GRAIL-10**: Add HMAC integrity to client audit log (20 min)
12. **GRAIL-12**: Switch to `credentialless` COEP (5 min)
13. **GRAIL-13**: Improve timing check with jitter + multiple methods (30 min)
14. **GRAIL-14**: Add chunked reading for .text hash (15 min)

### Phase G4 — Low Priority / Operational (~30 min)
15. **GRAIL-15**: Fix `release_gate.py` path (1 min)
16. **GRAIL-16**: Replace `time.time()` with `time.monotonic()` for rate limits (15 min)

---

## 🧪 Free Testing Tools (Zero Budget)

```
# 1. TLS pinning test — should fail if cert not pinned
curl -k https://your-server/api/v1/auth/activate

# 2. Account lockout bypass test
for i in {1..20}; do
    curl -X POST https://your-server/api/v1/auth/activate -H "Content-Type: application/json" \
         -d '{"email":"attacker@test.com","activation_key":"INVALID"}' &
done
# If >5 req/s succeed → rate limit or lockout is bypassed

# 3. Rate limiter memory test
for i in {1..100000}; do
    curl -H "X-Forwarded-For: 10.0.0.$((RANDOM % 65535))" \
         https://your-server/health &
done
# Monitor memory usage — should not grow unbounded

# 4. Race condition test (send parallel activation requests)
for i in {1..50}; do
    curl -X POST https://your-server/api/v1/auth/activate ... &
done
wait
# Check if more than max_devices devices were created

# 5. User enumeration test
curl -s https://your-server/api/v1/auth/activate -d '{"email":"real@user.com","activation_key":"VALID-KEY"}'
curl -s https://your-server/api/v1/auth/activate -d '{"email":"fake@user.com","activation_key":"VALID-KEY"}'
# If responses differ → enumeration possible

# 6. pip-audit (dependency scan)
pip-audit -r requirements.txt

# 7. Bandit (SAST)
bandit -r engine/security/ server/app/

# 8. OWASP ZAP baseline
docker run -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t https://your-server
```

---

*This audit was conducted by analyzing actual source code — not just documentation. Every claim was verified against the code on disk.*
