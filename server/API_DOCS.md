# AGREGAT Server — API Documentation

**For Desktop Developers:** This document describes all API endpoints the desktop client needs to interact with.

**Base URL (Production):** `https://your-supabase-project.supabase.co/functions/v1`
**Base URL (Local Dev):** `http://localhost:8000`

---

## Authentication Flow

```
1. User enters email + activation key in desktop app
2. Desktop → POST /v1/auth/activate { email, activation_key, device_fingerprint, os_info, app_version }
3. Server validates → returns { access_token, refresh_token, entitlements, expires_at }
4. Desktop stores tokens in OS keychain
5. Desktop starts heartbeat timer (120s)
6. Every 120s: Desktop → POST /v1/heartbeat
7. Server responds with updated entitlements or REVOKE command
```

---

## Public Endpoints

### POST /v1/auth/activate

Activate a license key and receive authentication tokens.

**Request Body:**
```json
{
  "email": "user@example.com",
  "activation_key": "AGR-BETA-XXXX-XXXX-XXXX",
  "device_fingerprint": "sha256_hex_string",
  "device_name": "DESKTOP-ABC123",
  "os_info": "Windows 11 Pro x64",
  "app_version": "7.78.0"
}
```

**Response (200):**
```json
{
  "status": "activated",
  "user_id": "uuid",
  "access_token": "opaque_token_string",
  "refresh_token": "opaque_refresh_string",
  "entitlements": [
    { "capability": "app.launch", "limit_value": null },
    { "capability": "engine.visual_backtest", "limit_value": null },
    { "capability": "ai.assistant", "limit_value": null }
  ],
  "expires_at": "2026-06-13T12:00:00Z",
  "license_tier": "beta"
}
```

**Errors:**
- `400` — Invalid request body
- `401` — Invalid activation key
- `403` — License expired or revoked
- `409` — Device limit reached (already activated on another device)
- `429` — Too many attempts

---

### POST /v1/auth/refresh

Refresh an expired access token using the refresh token.

**Headers:**
```
Authorization: Bearer <refresh_token>
```

**Response (200):**
```json
{
  "access_token": "new_opaque_token",
  "expires_at": "2026-06-06T12:05:00Z"
}
```

**Errors:**
- `401` — Invalid or expired refresh token
- `403` — Session revoked

---

### POST /v1/auth/logout

Revoke the current session.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200):**
```json
{ "status": "logged_out" }
```

---

### GET /v1/me

Get current user info and capabilities.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200):**
```json
{
  "user_id": "uuid",
  "email": "user@example.com",
  "license_tier": "beta",
  "license_expires_at": "2026-06-13T12:00:00Z",
  "entitlements": [
    { "capability": "app.launch", "limit_value": null },
    { "capability": "engine.visual_backtest", "limit_value": null }
  ],
  "device_id": "uuid"
}
```

**Errors:**
- `401` — Invalid or expired token
- `403` — License revoked

---

### GET /v1/entitlements

Get current entitlements only (lighter than /v1/me).

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200):**
```json
{
  "entitlements": [
    { "capability": "app.launch", "limit_value": null },
    { "capability": "engine.visual_backtest", "limit_value": null },
    { "capability": "optimizer.grid_search", "limit_value": 100 }
  ],
  "expires_at": "2026-06-13T12:00:00Z"
}
```

---

### POST /v1/heartbeat

Keep-alive heartbeat. Call every 120 seconds.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "app_version": "7.78.0",
  "integrity_hash": "sha256_of_critical_files",
  "current_strategy_hash": "sha256_of_loaded_strategy_or_null"
}
```

**Response (200):**
```json
{
  "status": "ok",
  "command": null,
  "entitlements": [
    { "capability": "app.launch", "limit_value": null }
  ],
  "expires_at": "2026-06-13T12:00:00Z",
  "server_time": "2026-06-06T12:00:00Z"
}
```

**Server Commands (via `command` field):**
- `null` — Normal, continue operation
- `"revoke"` — License revoked, block application immediately
- `"update_capabilities"` — Entitlements changed, update CapabilityManager
- `"force_update"` — New version required, show update dialog

**Errors:**
- `401` — Token expired (use refresh token)
- `403` — Session or license revoked → BLOCK APPLICATION

---

### POST /v1/strategies/register

Register a strategy (silent, fire-and-forget).

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "strategy_hash": "sha256_of_strategy_code",
  "strategy_code": "full Python code of the strategy",
  "file_name": "my_strategy.py",
  "metadata": {
    "app_version": "7.78.0",
    "timestamp": "2026-06-06T12:00:00Z"
  }
}
```

**Response (200):**
```json
{
  "status": "registered",
  "strategy_hash": "sha256_of_strategy_code",
  "first_seen": false
}
```

**Note:** This endpoint is non-blocking. If it fails, the desktop app should continue normally.

---

### POST /v1/audit/client-event

Send a client-side audit event.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "action": "backtest.run",
  "payload": {
    "strategy_name": "my_strategy.py",
    "asset": "DE40",
    "bars_processed": 50000
  }
}
```

**Response (200):**
```json
{ "status": "logged", "event_id": 12345 }
```

---

## Error Response Format

All errors follow this format:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "detail": "Optional additional details"
}
```

**Common Error Codes:**
| Code | HTTP Status | Description |
|---|---|---|
| `invalid_request` | 400 | Malformed request body |
| `unauthorized` | 401 | Missing or invalid token |
| `forbidden` | 403 | Action not permitted |
| `license_expired` | 403 | License has expired |
| `license_revoked` | 403 | License was revoked by admin |
| `device_limit_reached` | 409 | Already activated on max devices |
| `rate_limited` | 429 | Too many requests |
| `server_error` | 500 | Internal server error |

---

## Desktop Implementation Guide

### 1. Token Storage
Use OS keychain ONLY. Never store tokens in plaintext files.

```python
import keyring

SERVICE = "agregat"
TOKEN_USER = "auth_tokens"

def store_tokens(access: str, refresh: str):
    payload = json.dumps({"access": access, "refresh": refresh})
    keyring.set_password(SERVICE, TOKEN_USER, payload)

def load_tokens() -> dict | None:
    raw = keyring.get_password(SERVICE, TOKEN_USER)
    return json.loads(raw) if raw else None
```

### 2. HTTP Client
Use `httpx` with TLS verification. In production, add certificate pinning.

```python
import httpx

BASE_URL = "https://your-server.com"  # From config, not hardcoded

async def api_request(method: str, path: str, token: str | None = None, **kwargs):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method, f"{BASE_URL}{path}",
            headers=headers, **kwargs
        )
        response.raise_for_status()
        return response.json()
```

### 3. Heartbeat Manager
Run on a QTimer, handle server commands.

```python
class HeartbeatManager:
    INTERVAL_MS = 120_000  # 120 seconds
    GRACE_PERIOD_MS = 420_000  # 7 minutes
    
    def __init__(self):
        self._timer = QTimer()
        self._timer.timeout.connect(self._send_heartbeat)
        self._missed = 0
        self._max_missed = self.GRACE_PERIOD_MS // self.INTERVAL_MS
    
    def start(self):
        self._timer.start(self.INTERVAL_MS)
    
    async def _send_heartbeat(self):
        try:
            result = await api_request("POST", "/v1/heartbeat", token=self._access_token)
            self._missed = 0
            
            if result.get("command") == "revoke":
                self._on_revoke(result.get("detail", "License revoked"))
            elif result.get("command") == "update_capabilities":
                self._capabilities.update_from_server(result["entitlements"])
        except Exception:
            self._missed += 1
            if self._missed >= self._max_missed:
                self._on_grace_expired()
```

### 4. Capability Checks
Replace boolean license checks with capability-based checks.

```python
# OLD (insecure):
if license_manager.verify_license():
    run_backtest()

# NEW (secure):
if capability_manager.has("engine.visual_backtest"):
    run_backtest()
else:
    show_activation_dialog()
```

---

## Admin Endpoints

Admin endpoints are for the web admin panel only. Desktop clients should NOT call these.

See `server/README.md` for admin panel access instructions.

---

**Last Updated:** 2026-06-06
**Version:** 1.0.0