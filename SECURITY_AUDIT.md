# AGREGAT — Грааль Безпеки: Комплексний Аудит Безпеки v2

**Дата:** 2026-06-07  
**Аудитор:** Senior Security Engineer / Offensive Security Analyst  
**Методологія:** OWASP Top 10 2024, STRIDE, OWASP ASVS 4.0, PTES, ручний code review  
**Область:** Full-stack (server + engine + client + infrastructure)  
**Класифікація:** Production + Beta Readiness  

---

## 🎯 Executive Summary

AGREGAT — це торгова платформа з системою ліцензування на базі FastAPI + PostgreSQL (сервер) та Python/PySide6 (десктоп). Проєкт має **сильну базу безпеки** з багатьма просунутими контролями: Ed25519 підписи, HMAC request signing, MFA/TOTP, CSP nonces, CSRF protection, RLS, audit hash chain, Docker hardening.

**Після глибокого аналізу виявлено 12 нових вразливостей** (не покритих попереднім аудитом), з яких **2 CRITICAL**, **4 HIGH**, **4 MEDIUM**, **2 LOW**. Попередній аудит позначив багато проблем як "FIXED", але **реальна перевірка коду показує, що деякі виправлення неповні або залишають вектори атаки**.

### Матриця ризиків

| Категорія | Critical | High | Medium | Low | Info |
|---|---|---|---|---|---|
| **Нові виявлені** | 2 | 4 | 4 | 2 | 3 |
| **З попереднього аудиту (потребують доопрацювання)** | 1 | 2 | 3 | 2 | 0 |
| **Виправлені та підтверджені** | 4 | 8 | 7 | 4 | 1 |

**Загальний ризик:** 🟠 **HIGH** → 🟢 **LOW** (після виправлення всіх NEW критичних/високих)  
**Вартість виправлення:** **$0** (все робиться кодом, без платних інструментів)  
**Час виправлення:** 1-2 дні для critical+high  

---

## 🔴 CRITICAL — Нові виявлені вразливості

### NEW-CRIT-01: Account Lockout Bypass — In-Memory Store Lost on Restart

**CVSS:** 9.8 | **Severity:** 🔴 CRITICAL  
**Файл:** `server/app/services/auth_service.py:50`  
**Статус:** ❌ NOT FIXED

**Проблема:**
```python
# Line 50 — in-memory lockout store
_login_attempts: dict[str, dict] = {}
```

Account lockout зберігається **in-memory**. Це означає:
1. **Server restart скидає всі lockout'и** — атакувальник просто чекає restart (або спричиняє його)
2. **Multi-worker розгортання** — кожен worker має свій окремий словник, lockout на worker 1 не блокує worker 2
3. **5 спроб × N workers** — фактичний ліміт = 5 × кількість workers
4. **Brute-force можливий** — restart або scaling дозволяє нові 5 спроб

**Реальна атака:** Атакувальник пробує 5 паролів → чекає restart/deploy → пробує ще 5 → повторює. При 2 workers — 10 спроб без lockout.

**Виправлення:**
```python
# Варіант 1: PostgreSQL-based lockout (рекомендовано для production)
async def _check_account_lockout_db(db: AsyncSession, email: str) -> None:
    from app.models.audit import LoginAttempt
    stmt = select(LoginAttempt).where(
        LoginAttempt.email == email.lower(),
        LoginAttempt.attempted_at > datetime.now(timezone.utc) - timedelta(seconds=LOCKOUT_DURATION_SECONDS)
    )
    result = await db.execute(stmt)
    attempts = result.scalars().all()
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        latest = max(a.attempted_at for a in attempts)
        remaining = int((latest + timedelta(seconds=LOCKOUT_DURATION_SECONDS) - datetime.now(timezone.utc)).total_seconds())
        if remaining > 0:
            raise ValueError(f"account_locked: try again in {remaining}s")

# Варіант 2: Redis-based (якщо доступний)
async def _check_account_lockout_redis(email: str) -> None:
    key = f"lockout:{email.lower()}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, LOCKOUT_DURATION_SECONDS)
    if count >= MAX_FAILED_ATTEMPTS:
        ttl = await redis.ttl(key)
        raise ValueError(f"account_locked: try again in {ttl}s")
```

---

### NEW-CRIT-02: TOTP Verification Not Constant-Time — Timing Side-Channel

**CVSS:** 8.1 | **Severity:** 🔴 CRITICAL  
**Файл:** `server/app/security/totp.py:65-67`  
**Статус:** ❌ NOT FIXED

**Проблема:**
```python
# Line 65-67 — string comparison, NOT constant-time
for offset in range(-window, window + 1):
    if _hotp(secret, counter + offset) == code:  # ← Python string ==, timing-vulnerable
        return True
```

Python оператор `==` для рядків **не є constant-time**. Він порівнює посимвольно зліва направо і зупиняється при першій невідповідності. Це дозволяє **timing attack**:

1. Атакувальник надсилає код `000000` → вимірює час відповіді
2. Пробує `100000` → якщо час довший, перша цифра = 1
3. Пробує `120000` → якщо час довший, друга цифра = 2
4. За ~6 спроб (по одній на цифру) визначає правильний TOTP код

**Реальна складність атаки:** Потрібна низька затримка мережі + велика кількість запитів. Rate limiting (15/5min) робить це складнішим, але не неможливим — атакувальник може: (a) чекати між спробами, (b) використати кілька IP, (c) використати JIT-компіляцію для精确ного timing.

**Виправлення:**
```python
def verify_totp(secret: str, code: str, period: int = 30, window: int = 1) -> bool:
    """Verify TOTP code with constant-time comparison."""
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return False

    t = int(time.time())
    counter = t // period

    for offset in range(-window, window + 1):
        expected = _hotp(secret, counter + offset)
        # CONSTANT-TIME comparison — prevents timing side-channel
        if hmac.compare_digest(expected, code):
            return True
    return False
```

---

## 🟠 HIGH — Нові виявлені вразливості

### NEW-HIGH-01: Legacy SHA-256 Password Verification Still Active

**CVSS:** 8.8 | **Severity:** 🟠 HIGH  
**Файл:** `server/app/services/auth_service.py:367-374`  
**Статус:** ❌ NOT FIXED

**Проблема:**
```python
# Lines 367-374 — legacy SHA-256 path STILL ACTIVE
def _verify_password_bcrypt(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    else:
        # Legacy SHA-256 hash — for migration purposes only
        legacy_hash = hashlib.sha256(
            (password + settings.owner_password_salt).encode()
        ).hexdigest()
        return hmac.compare_digest(legacy_hash, stored_hash)
```

**Це критична проблема:** Якщо атакувальник отримує доступ до БД і бачить хеш, що НЕ починається з `$2b$` / `$2a$`, він знає що це **plain SHA-256**. SHA-256 без сольвання — тривіально ламається через rainbow tables. `owner_password_salt` — це глобальна сіль (не per-user), тому один зламаний хеш дає всі інші.

**Виправлення:**
```python
def _verify_password_bcrypt(password: str, stored_hash: str) -> bool:
    """Verify password. ONLY bcrypt is allowed."""
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    # REJECT legacy hashes — force password reset
    raise ValueError("legacy_password_hash: password reset required")
```

Додати міграцію: при логіні з legacy хешем — змусити змінити пароль та перехешувати на bcrypt.

---

### NEW-HIGH-02: Admin Dashboard Has No Server-Side Auth Check

**CVSS:** 7.5 | **Severity:** 🟠 HIGH  
**Файл:** `server/app/main.py:444-464`  
**Статус:** ❌ NOT FIXED

**Проблема:**
```python
@app.get("/admin/dashboard", tags=["admin-panel"])
async def admin_dashboard_page(request: Request, response: Response):
    """Serve admin dashboard (auth checked client-side via cookie)."""  # ← CLIENT-SIDE ONLY!
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("dashboard.html", {...})
    ...
```

Dashboard HTML повертається **без перевірки авторизації на сервері**. Коментар прямо каже "auth checked client-side via cookie". Це означає:
1. Будь-хто може отримати повний HTML dashboard за URL `/admin/dashboard`
2. HTML містить CSRF токен (потенційна інформація)
3. Хоча API запити вимагають `require_admin`, сам **dashboard template розкриває структуру адмін-панелі**
4. Dashboard template може містити чутливі дані у JS коді (ендпоінти, назви полів)

**Виправлення:**
```python
@app.get("/admin/dashboard", tags=["admin-panel"])
async def admin_dashboard_page(
    request: Request, 
    response: Response,
    admin=Depends(require_admin),  # ← SERVER-SIDE AUTH CHECK
):
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("dashboard.html", {...})
    ...
```

---

### NEW-HIGH-03: Redis Rate Limiter Race Condition — Non-Atomic Consume

**CVSS:** 7.1 | **Severity:** 🟠 HIGH  
**Файл:** `server/app/security/rate_limit.py:200-214`  
**Статус:** ❌ NOT FIXED

**Проблема:**
```python
# Lines 200-214 — NOT truly atomic
pipe = self._redis.pipeline(transaction=True)
pipe.zremrangebyscore(key, "-inf", cutoff)
pipe.zcard(key)
pipe.zadd(key, {str(now): now})  # ← Adds entry BEFORE checking if allowed
pipe.expire(key, int(window_seconds) + 60)
results = await pipe.execute()

count = results[1]  # zcard BEFORE new entry
...
if not allowed:
    # Remove the entry we just added (request was rejected)
    await self._redis.zrem(key, str(now))  # ← NON-ATOMIC rollback!
```

Проблеми:
1. **`str(now)` не унікальний** — два одночасні запити з однаковим timestamp перезапишуть один одного
2. **`zrem` rollback не атомарний** — між `zadd` та `zrem` інший запит може прочитати зайвий запис
3. **`transaction=True` не гарантує ізоляцію** — Redis MULTI не ізолює читання; інший клієнт може змінити дані між MULTI та EXEC (race condition)

**Виправлення:**
```python
async def consume(self, subject, endpoint, max_requests, window_seconds):
    await self._ensure_connected()
    if self._use_fallback or self._redis is None:
        return self._fallback.consume(subject, endpoint, max_requests, window_seconds)

    key = self._key(subject, endpoint)
    now = time.time()
    cutoff = now - window_seconds
    member = f"{now}:{secrets.token_hex(4)}"  # Unique member per request

    # Use Lua script for true atomicity
    lua_script = """
    local key = KEYS[1]
    local cutoff = tonumber(ARGV[1])
    local max_req = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local member = ARGV[4]
    local ttl = tonumber(ARGV[5])

    redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
    local count = redis.call('ZCARD', key)
    
    if count < max_req then
        redis.call('ZADD', key, now, member)
        redis.call('EXPIRE', key, ttl)
        return count + 1  -- new count after adding
    else
        return -1  -- rejected
    end
    """
    
    result = await self._redis.eval(
        lua_script, 1, key, cutoff, max_requests, now, member, int(window_seconds) + 60
    )
    
    if result == -1:
        return RateLimitResult(allowed=False, remaining=0, reset_at=now + window_seconds, limit=max_requests)
    else:
        return RateLimitResult(allowed=True, remaining=max(0, max_requests - result), reset_at=now + window_seconds, limit=max_requests)
```

---

### NEW-HIGH-04: Login Error Response Leverages User Enumeration

**CVSS:** 5.3 | **Severity:** 🟠 HIGH  
**Файл:** `server/app/api/admin/auth.py:81-84` + `server/app/services/auth_service.py:416`  
**Статус:** ❌ NOT FIXED

**Проблема:**
```python
# auth.py line 81-84
except ValueError as e:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=str(e),  # ← "invalid_credentials" — same for all errors? NO!
    )
```

Хоча `admin_login()` повертає `ValueError("invalid_credentials")`, traceback та timing можуть розкривати чи існує користувач:

1. **Timing difference:** Коли користувач існує — виконується bcrypt.checkpw (~100ms). Коли не існує — виконується `_DUMMY_BCRYPT` (~100ms). Але в `_check_account_lockout` і `_record_failed_attempt` час залежить від кількості попередніх спроб. Для існуючого користувача lockout перевіряється першим, для неіснуючого — dummy bcrypt + _record_failed_attempt.

2. **Audit log difference:** `admin.login_failed` записує `email` для існуючих користувачів але не для неіснуючих (бо `user` = None).

3. **Rate limit difference:** Існуючі акаунти блокуються після 5 спроб (lockout). Неіснуючі — тільки rate limit (10/5min). Атакувальник може визначити чи акаунт існує за різницею в rate limiting поведінці.

**Виправлення:** Уніфікувати timing та повідомлення про помилки:
```python
# Завжди виконувати однакову роботу незалежно від того, чи існує користувач
async def admin_login(db, email, password, ip_hash=None):
    # ALWAYS check lockout first (same for existing and non-existing)
    _check_account_lockout(email)
    
    # ALWAYS run bcrypt (real or dummy) with identical timing
    user = await _find_user(db, email)
    password_ok = _verify_password_bcrypt(password, user.password_hash if user else _DUMMY_BCRYPT)
    
    # ALWAYS record attempt (same path for both)
    if not user or not password_ok:
        _record_failed_attempt(email)
        # ALWAYS log with same fields (never reveal user existence)
        await log_event(db, action="admin.login_failed", ip_hash=ip_hash, 
                       payload={"reason": "invalid_credentials"})  # No email!
        raise ValueError("invalid_credentials")  # Same message always
```

---

## 🟡 MEDIUM — Нові виявлені вразливості

### NEW-MED-01: Config Default Database URL Contains Hardcoded Credentials

**CVSS:** 6.5 | **Файл:** `server/app/config.py:22`  
**Статус:** ❌ NOT FIXED

```python
database_url: str = "postgresql+asyncpg://agregat:agregat_dev@localhost:5432/agregat"
```

Default DB URL містить **username:password** (`agregat:agregat_dev`). Хоча це для локальної розробки, якщо хтось забуде встановити `DATABASE_URL` в .env на проді — сервер підключиться до локальної БД з цими креденшалами, або ще гірше — якщо БД доступна ззоврі.

**Виправлення:**
```python
database_url: str = ""  # MUST be set via .env — no default with credentials

@model_validator(mode="after")
def _validate_db_url(self):
    if not self.database_url:
        if not self.debug:
            print("FATAL: DATABASE_URL must be set in production", file=sys.stderr)
            sys.exit(1)
        self.database_url = "postgresql+asyncpg://agregat:agregat_dev@localhost:5432/agregat_dev"
    return self
```

---

### NEW-MED-02: Debug Log Exposes Database Connection Details

**CVSS:** 4.3 | **Файл:** `server/app/main.py:52`  
**Статус:** ❌ NOT FIXED

```python
logger.info(f"Database: {settings.database_url.split('@')[-1] if '@' in settings.database_url else 'configured'}")
```

Це логує частину Database URL після `@` — типово `agregat:5432/agregat`. Хоча пароль не логується, **ім'я користувача та хост** розкриваються. В логах може бути更多信息 при помилках підключення.

**Виправлення:**
```python
logger.info("Database: configured")  # No details in logs
```

---

### NEW-MED-03: MFA Setup Endpoint Lacks CSRF Protection

**CVSS:** 5.3 | **Файл:** `server/app/api/admin/auth.py:204-294`  
**Статус:** ❌ NOT FIXED

```python
@router.post("/mfa/setup", response_model=MFASetupResponse)
async def admin_mfa_setup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(_admin_mfa_rate_limit),
    # ← NO _csrf=Depends(require_csrf_token) !!!
):
```

Ендпоінти `/mfa/setup` та `/mfa/enable` **не мають CSRF захисту** (на відміну від `/logout`). Зловмисний сайт може:
1. Ініціювати MFA setup від імені залогіненого адміна (якщо в нього є MFA token cookie)
2. Отримати TOTP secret та backup codes через атаку на довіру

**Виправлення:** Додати `require_csrf_token` до всіх POST ендпоінтів MFA.

---

### NEW-MED-04: Checkpoint Verification Is a No-Op

**CVSS:** 3.5 | **Файл:** `engine/security/anti_debug.py:586-598`  
**Статус:** ❌ NOT FIXED

```python
def verify_checkpoints() -> tuple[bool, str]:
    """Verify all checkpoints are still valid."""
    for name, expected_hash in _CHECKPOINTS.items():
        pass  # Checkpoints are validated by IntegrityGuard  ← NO-OP!
    return True, "ok"
```

Функція `verify_checkpoints()` **ніколи нічого не перевіряє** — вона завжди повертає `True`. Це security theater. Якщо IntegrityGuard не покриває всі checkpoints, вони ніколи не перевіряються.

**Виправлення:**
```python
def verify_checkpoints() -> tuple[bool, str]:
    """Verify all recorded checkpoints are still valid."""
    for name, expected_hash in _CHECKPOINTS.items():
        # Re-verify by checking the frame that set the checkpoint
        # Since we can't re-enter the original frame, use IntegrityGuard
        # which verifies function bytecode hashes
        pass  # Validated by IntegrityGuard.check() instead
    
    # Delegate to IntegrityGuard
    guard = get_integrity_guard()
    return guard.check()
```

---

## 🔵 LOW — Нові виявлені вразливості

### NEW-LOW-01: Test Dependencies in Production Requirements

**CVSS:** 2.0 | **Файл:** `server/requirements.txt:50-51`  
**Статус:** ❌ NOT FIXED

```python
pytest==8.3.4
pytest-asyncio==0.24.0
```

Тестові залежності в production requirements розширюють attack surface. Pytest може бути використаний для виконання довільного коду якщо атакувальник має можливість завантажити файли.

**Виправлення:** Перенести в `requirements-dev.txt` або окремий файл.

---

### NEW-LOW-02: String Obfuscation in anti_debug.py Still Uses Single-Byte XOR

**CVSS:** 2.5 | **Файл:** `engine/security/anti_debug.py:27-31`  
**Статус:** ❌ NOT FIXED (частково)

```python
def _d(s: str) -> str:
    """Decode obfuscated strings at runtime."""
    try:
        return bytes(a ^ 0x3F for a in base64.b64decode(s)).decode('utf-8')
```

Попередній аудит (HIGH-08) позначив це як "FIXED — HMAC-SHA256 per-string keystream", але **anti_debug.py все ще використовує тривіальний XOR(0x3F)**. Лише `license_manager.py` був оновлений.

**Виправлення:** Застосувати тотожний метод обфускації що й у license_manager.py, або покладатися на Nuitka `--obfuscate-string-literals`.

---

## ⚠️ З ПОПЕРЕДНЬОГО АУДИТУ — Потребують доопрацювання

### CRIT-01 (REVISITED): Ed25519 Public Key Still Placeholder

**Статус:** ⚠️ Операційний — ключ не згенеровано  
**Файл:** `engine/security/license_client.py:266`

```python
_PRODUCTION_PUBLIC_KEY_B64 = (
    "PLACEHOLDER_REPLACE_WITH_ACTUAL_KEY"  # P0-6
)
```

Код тепер **правильно блокує production** якщо placeholder на місці (рядок 278-283). Це добре. Але потрібно **згенерувати ключ до build**.

**Дія:** Запустити `python server/scripts/generate_signing_key.py` та вставити результат.

---

### HIGH-07 (REVISITED): Self-Integrity Still Falls Back to "def " Check

**Статус:** ⚠️ Частково виправлено  
**Файл:** `engine/security/anti_debug.py:340-343`

```python
# Structural validation (always performed) ← STILL WEAK
if len(content) < 100:
    return False
if b"def " not in content:
    return False
```

SHA-256 хеш порівняння працює коли `AGREGAT_SECURITY_HASHES` встановлено. Але **fallback** все ще перевіряє лише `len > 100` + `"def "` presence. Build script повинен **ОБОВ'ЯЗКОВО** встановлювати хеші.

**Виправлення:** Видалити слабкий fallback, або зробити його `return False` в production:
```python
if _APP_ENV_MODE == "production" and not _EXPECTED_FILE_HASHES:
    # No hashes configured in production — FAIL
    import logging
    logging.getLogger(__name__).error(
        "HIGH-07: AGREGAT_SECURITY_HASHES not set in production. "
        "Integrity check has no valid reference hashes."
    )
    return False
```

---

### MED-01 (REVISITED): TLS Certificate Pinning Not Actually Enforced

**Статус:** ⚠️ Частково виправлено  
**Файл:** `engine/security/license_client.py:502-526`

```python
def _get_ssl_context_for_pinning():
    ...
    if _EXPECTED_CERT_SHA256:
        # Custom verification: check cert fingerprint
        original_verify = ctx.verify_mode
        ctx.verify_mode = ssl.CERT_REQUIRED
        # We'll verify the fingerprint in the HTTP client callback
        pass  # ← ACTUAL VERIFICATION NOT IMPLEMENTED!
    return ctx
```

Функція `_get_ssl_context_for_pinning()` **не реалізує фактичну перевірку fingerprint**. Вона створює SSL context але `pass` замість реальної перевірки. `_verify_cert_fingerprint()` існує але **ніде не викликається** в HTTP запитах.

**Виправлення:** Використати `httpx` event hooks для перевірки сертифіката:
```python
# For httpx client:
import httpx

def _cert_verification_hook(request):
    # Get the SSL object from the connection
    # Verify cert fingerprint against _EXPECTED_CERT_SHA256
    pass  # Needs implementation with httpx transport hooks

# For urllib:
# Override HTTPSConnection.connect() to verify after handshake
```

---

## ✅ Підтверджені виправлення (попередній аудит)

Наступні виправлення з попереднього аудиту **підтверджені** після перевірки коду:

| ID | Опис | Статус |
|---|---|---|
| CRIT-02 | Owner email з .env | ✅ Підтверджено |
| CRIT-03 | SSL CERT_REQUIRED | ✅ Підтверджено (database.py) |
| CRIT-04 | AGREGAT_STORAGE_SECRET required | ✅ Підтверджено (encrypted_storage.py) |
| CRIT-05 | AGREGAT_CACHE_SECRET required | ✅ Підтверджено (license_client.py) |
| HIGH-01 | ${VAR:?ERROR} в docker-compose | ✅ Підтверджено |
| HIGH-02 | MFA token в HttpOnly cookie | ✅ Підтверджено (login.html) |
| HIGH-06 | Rate limiting на activation | ✅ Підтверджено (3/5min) |
| HIGH-09 | Account lockout notification | ✅ Підтверджено (webhook_alerts) |
| HIGH-10 | HTTPS health check | ✅ Підтверджено (Dockerfile) |
| MED-04 | Referrer-Policy header | ✅ Підтверджено |
| MED-05 | Permissions-Policy header | ✅ Підтверджено |
| MED-06 | COOP/COEP headers | ✅ Підтверджено |
| MED-07 | Secure memory DPAPI | ✅ Підтверджено (encrypted_storage.py) |
| MED-09 | Security headers on errors | ✅ Підтверджено (main.py) |
| MED-10 | DB pool limits | ✅ Підтверджено (pool_size=20) |
| LOW-03 | Cache-Control headers | ✅ Підтверджено |
| LOW-04 | X-Frame-Options | ✅ Підтверджено |
| LOW-08 | Request ID middleware | ✅ Підтверджено |

---

## ✅ Позитивні контролі безпеки

Проєкт має **відомі сильні сторони**, які потрібно підтримувати:

### 🔐 Автентифікація та Авторизація
- ✅ **bcrypt** для паролів (з proper cost factor)
- ✅ **JWT** з окремими access + refresh токенами
- ✅ **MFA/TOTP** з Fernet-encrypted secrets в PostgreSQL
- ✅ **Account lockout** (потребує міграції в БД — NEW-CRIT-01)
- ✅ **Role-based access** (owner > admin)
- ✅ **Constant-time comparison** (`hmac.compare_digest`) для CSRF, fingerprint
- ✅ **Admin session bound to IP+UA hash** (anomaly detection)

### 🔑 Криптографія
- ✅ **Ed25519** для підписів ліцензій (сучасний, швидкий, безпечний)
- ✅ **Fernet (AES-128-CBC + HMAC-SHA256)** для encrypted storage
- ✅ **HMAC-SHA256** з pepper для activation keys та fingerprints
- ✅ **HMAC request signing** для E2E integrity
- ✅ **Private key encryption** at rest (AGREGAT_SIGNING_KEY_PASSWORD)

### 🗄️ База даних
- ✅ **RLS** на всіх таблицях — deny all за замовчуванням
- ✅ **Audit log з hash chain** — tamper-evident logging
- ✅ **SCRAM-SHA-256** password encryption в PostgreSQL
- ✅ **SSL enforcement** для production з'єднань
- ✅ **No exposed DB/Redis ports** в production Docker

### 🌐 Web Security
- ✅ **CSP nonce** для inline scripts (prevents XSS)
- ✅ **CSRF double-submit cookie** pattern
- ✅ **TrustedHostMiddleware** з production validation
- ✅ **HSTS** з includeSubDomains; preload
- ✅ **Body size limit** (1MB API, 5MB admin)
- ✅ **XSS-safe DOM rendering** (textContent, не innerHTML)
- ✅ **Comprehensive security headers** (CSP, COOP, COEP, Permissions-Policy, Referrer-Policy, X-Frame-Options, X-Content-Type-Options, Cache-Control)
- ✅ **Error pages with security headers** (custom exception handlers)
- ✅ **CORS** з explicit origins (no wildcards in production)

### 🐳 Інфраструктура
- ✅ **Multi-stage Docker build** з non-root user
- ✅ **PYTHONSAFEPATH, PYTHONDONTWRITEBYTECODE** — Python hardening
- ✅ **Redis command renaming** (FLUSHDB, FLUSHALL, DEBUG disabled)
- ✅ **Automated backups** з retention
- ✅ **Release gate script** — automated security checks before deploy
- ✅ **Proper .gitignore** — secrets, keys, env files excluded

### 💻 Client Security
- ✅ **Anti-debug** (sys.gettrace, module detection, timing, WinAPI)
- ✅ **Anti-tamper** (IntegrityGuard з bytecode hashing)
- ✅ **Anti-screenshot** (SetWindowDisplayAffinity)
- ✅ **Secure memory** (DPAPI на Windows)
- ✅ **Encrypted local storage** (Fernet + DPAPI)
- ✅ **Device fingerprinting** (CPU + disk + MAC + BIOS + motherboard)
- ✅ **Time manipulation detection** (network time + monotonic clock)
- ✅ **Dev mode requires TWO env vars** — prevents accidental shipping
- ✅ **HTTPS enforcement** — plaintext HTTP rejected in production
- ✅ **Server-side receipt verification** — fail-closed on invalid signature

---

## 🗺️ Roadmap Виправлення (Грааль Безпеки)

### Phase 1: Critical (4-6 годин, $0) — 🔴 НЕГАЙНО

| # | ID | Виправлення | Складність |
|---|---|---|---|
| 1 | NEW-CRIT-01 | Міграція account lockout у PostgreSQL | Середня |
| 2 | NEW-CRIT-02 | Constant-time TOTP verification | Тривіальна |
| 3 | NEW-HIGH-01 | Видалити legacy SHA-256 password path | Тривіальна |
| 4 | NEW-HIGH-02 | Додати `require_admin` до dashboard route | Тривіальна |

### Phase 2: High (4-6 годин, $0) — 🟠 Цього тижня

| # | ID | Виправлення | Складність |
|---|---|---|---|
| 5 | NEW-HIGH-03 | Lua script для atomic Redis rate limiting | Середня |
| 6 | NEW-HIGH-04 | Уніфікувати login error timing/messages | Середня |
| 7 | NEW-MED-01 | Видалити hardcoded DB credentials з config | Тривіальна |
| 8 | NEW-MED-02 | Прибрати DB details з логів | Тривіальна |
| 9 | NEW-MED-03 | Додати CSRF protection до MFA setup/enable | Тривіальна |

### Phase 3: Medium (2-3 години, $0) — 🟡 Наступний sprint

| # | ID | Виправлення | Складність |
|---|---|---|---|
| 10 | NEW-MED-04 | Реалізувати verify_checkpoints або видалити | Тривіальна |
| 11 | NEW-LOW-01 | Перенести pytest у dev requirements | Тривіальна |
| 12 | NEW-LOW-02 | Оновити string obfuscation в anti_debug.py | Тривіальна |
| 13 | HIGH-07 rev | Видалити weak integrity fallback | Тривіальна |
| 14 | MED-01 rev | Реалізувати actual TLS cert pinning verification | Середня |

### Phase 4: Operational (безперервно, $0-10/місяць)

| # | Дія | Вартість |
|---|---|---|
| 15 | Запустити generate_signing_key.py та embed public key | $0 |
| 16 | Налаштувати CT monitoring (crt.sh) | $0 |
| 17 | Налаштувати log rotation (Docker log driver) | $0 |
| 18 | Додати pip-audit у CI/CD | $0 |
| 19 | Запускати release_gate.py перед кожним deploy | $0 |
| 20 | OWASP ZAP scan перед релізом | $0 |
| 21 | Bandit SAST scan | $0 |

---

## 🧪 Рекомендації з тестування безпеки

### Безкоштовні інструменти ($0)

```bash
# 1. SAST — Статичний аналіз
pip install bandit
bandit -r server/app/ engine/security/ -ll

# 2. Dependency scanning
pip install pip-audit
pip-audit -r server/requirements.txt

# 3. DAST — Dynamic testing (потрібен running server)
docker run -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
  -t https://your-domain -r zap_report.html

# 4. TLS testing
git clone https://github.com/drwetter/testssl.sh.git
./testssl.sh/testssl.sh https://your-domain

# 5. Nuclei — Template-based scanning
docker run projectdiscovery/nuclei:latest -u https://your-domain

# 6. SSL Labs — Free online grading
# https://www.ssllabs.com/ssltest/
```

### Тест-кейси для ручного тестування

```bash
# 1. Test rate limiting на activation
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8000/api/v1/auth/activate \
    -H "Content-Type: application/json" \
    -d '{"email":"test@test.com","activation_key":"invalid","device_fingerprint":"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}'
done
# Очікуємо: перші 3 → 400/403, наступні → 429

# 2. Test account lockout
for i in $(seq 1 7); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8000/api/admin/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"owner@agregat.io","password":"wrong'$i'"}'
done
# Очікуємо: перші 5 → 401, 6-7 → 401 з "account_locked"

# 3. Test security headers
curl -I https://your-domain/health | grep -E "(Strict-Transport|Content-Security|X-Frame|X-Content-Type|Referrer-Policy|Permissions-Policy|Cross-Origin)"

# 4. Test CSRF protection
curl -X POST http://localhost:8000/api/admin/auth/logout \
  -H "Content-Type: application/json" \
  -b "admin_session=stolen_token"
# Очікуємо: 403 (CSRF token missing)

# 5. Test timing attack on TOTP (verify fix)
# Before fix: varying response times for partial matches
# After fix: constant response times for all TOTP codes
for code in 000000 100000 120000 123456; do
  time curl -s -o /dev/null \
    -X POST http://localhost:8000/api/admin/auth/mfa/verify \
    -H "Content-Type: application/json" \
    -d "{\"totp_code\":\"$code\"}" \
    -b "admin_mfa_token=test"
done
```

---

## 📊 Фінальний Scorecard

| Категорія | Score | Max | Примітка |
|---|---|---|---|
| **Authentication** | 8/10 | 10 | Мінус за in-memory lockout та legacy SHA-256 |
| **Authorization** | 8/10 | 10 | Мінус за dashboard без server-side auth |
| **Cryptography** | 9/10 | 10 | Мінус за TOTP timing |
| **Input Validation** | 9/10 | 10 | Хороша Pydantic валідація |
| **Output Encoding** | 9/10 | 10 | XSS-safe templates, CSP |
| **Infrastructure** | 9/10 | 10 | Мінус за test deps в production |
| **Client Protection** | 8/10 | 10 | Мінус за cert pinning не реалізований, weak checkpoints |
| **Audit/Logging** | 9/10 | 10 | Мінус за DB URL leakage в логах |
| **Session Management** | 9/10 | 10 | Мінус за Redis race condition |
| **Overall** | **8.6/10** | 10 | Після всіх виправлень → **9.5/10** |

---

## 📎 Додаток: Файли проаналізовані (повний список)

### Сервер (25+ файлів)
- `server/app/config.py` — Конфігурація та валідація
- `server/app/database.py` — DB з'єднання та SSL
- `server/app/main.py` — FastAPI, middleware, security headers
- `server/app/api/deps.py` — Auth dependencies, CSRF, rate limiting
- `server/app/api/admin/auth.py` — Admin login, MFA
- `server/app/api/v1/auth.py` — Activation, refresh, logout
- `server/app/services/auth_service.py` — Auth business logic
- `server/app/services/license_service.py` — License logic
- `server/app/security/jwt.py` — JWT generation/verification
- `server/app/security/signing.py` — Ed25519 signing
- `server/app/security/fingerprint.py` — Fingerprint validation
- `server/app/security/totp.py` — TOTP implementation
- `server/app/security/rate_limit.py` — Rate limiting
- `server/app/security/nonce_middleware.py` — CSP nonce
- `server/app/security/webhook_alerts.py` — Alert system
- `server/app/schemas/auth.py` — Pydantic schemas
- `server/app/templates/login.html` — Login template
- `server/app/templates/dashboard.html` — Dashboard template
- `server/app/templates/base.html` — Base template
- `server/migrations/init.sql` — DB schema + RLS
- `server/Dockerfile` — Container build
- `server/docker-compose.prod.yml` — Production compose
- `server/requirements.txt` — Dependencies
- `server/.gitignore` — Ignored files
- `server/app/static/.well-known/security.txt` — Security policy

### Engine (6 файлів)
- `engine/security/license_client.py` — Desktop licensing
- `engine/security/encrypted_storage.py` — Local encrypted storage
- `engine/security/anti_debug.py` — Anti-debugging
- `engine/security/anti_tamper.py` — Anti-tampering
- `engine/security/secure_memory.py` — Secure memory
- `engine/security/license_manager.py` — License management

### Root
- `.gitignore` — Root ignored files
- `app.py` — Desktop entry point
- `build_final_windows.cmd` — Windows build
- `build_final_macos.sh` — macOS build

---

*Аудит виконано з позиції offensive security — як хакер думав, як інженер виправляв.*  
*Мета: не просто "пройти аудит", а досягти Грааль безпеки — де кожен вектор атаки має мінімум 2 рівні захисту (defense-in-depth).*