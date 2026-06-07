"""
AGREGAT Server — Webhook Security Alerts

E2E-06: Real-time security alerting via Telegram/Discord webhooks.

Sends instant notifications for critical security events:
- Admin login (success + failure)
- License activation anomalies
- Brute force detection
- Suspicious API patterns
- License revocation

Free to use — no paid monitoring services needed.
Works with Telegram Bot API (free) and Discord Webhooks (free).
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_HTTPX = False


# ─── Configuration ─────────────────────────────────────────────────────

# Webhook URLs — set via environment variables
# Telegram: https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>&text=<msg>
# Discord: https://discord.com/api/webhooks/<id>/<token>
TELEGRAM_WEBHOOK_URL = os.environ.get("SECURITY_ALERTS_TELEGRAM", "")
DISCORD_WEBHOOK_URL = os.environ.get("SECURITY_ALERTS_DISCORD", "")

# Alert severity levels
SEVERITY_CRITICAL = "🔴 CRITICAL"
SEVERITY_HIGH = "🟠 HIGH"
SEVERITY_MEDIUM = "🟡 MEDIUM"
SEVERITY_LOW = "🟢 LOW"


# ─── Alert Formatting ──────────────────────────────────────────────────

def _format_telegram_alert(severity: str, title: str, details: dict) -> str:
    """Format alert for Telegram (Markdown)."""
    lines = [
        f"🔒 *AGREGAT Security Alert*",
        f"",
        f"*{severity}*: {title}",
        f"*Time*: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"",
    ]
    
    for key, value in details.items():
        lines.append(f"*{key}*: `{value}`")
    
    return "\n".join(lines)


def _format_discord_alert(severity: str, title: str, details: dict) -> dict:
    """Format alert for Discord (Embed)."""
    color_map = {
        SEVERITY_CRITICAL: 0xFF0000,
        SEVERITY_HIGH: 0xFF6600,
        SEVERITY_MEDIUM: 0xFFCC00,
        SEVERITY_LOW: 0x00CC00,
    }
    
    fields = [
        {"name": key, "value": f"`{value}`", "inline": True}
        for key, value in details.items()
    ]
    
    return {
        "embeds": [{
            "title": f"🔒 {severity}: {title}",
            "color": color_map.get(severity, 0x999999),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": fields,
            "footer": {"text": "AGREGAT Security Monitor"},
        }]
    }


# ─── Sending ───────────────────────────────────────────────────────────

def _send_telegram(message: str) -> bool:
    """Send alert to Telegram webhook."""
    if not TELEGRAM_WEBHOOK_URL:
        return False
    
    try:
        if HAS_HTTPX:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    TELEGRAM_WEBHOOK_URL,
                    json={"text": message, "parse_mode": "Markdown"},
                )
                return resp.status_code == 200
        else:
            data = json.dumps({"text": message, "parse_mode": "Markdown"}).encode()
            req = urllib.request.Request(
                TELEGRAM_WEBHOOK_URL,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
    except Exception:
        return False


def _send_discord(payload: dict) -> bool:
    """Send alert to Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        return False
    
    try:
        if HAS_HTTPX:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(DISCORD_WEBHOOK_URL, json=payload)
                return resp.status_code in (200, 204)
        else:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                DISCORD_WEBHOOK_URL,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 204)
    except Exception:
        return False


def send_security_alert(severity: str, title: str, details: dict) -> bool:
    """
    Send a security alert to all configured webhooks.
    
    Args:
        severity: SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, or SEVERITY_LOW
        title: Alert title
        details: Key-value pairs to include in the alert
    
    Returns True if at least one webhook was notified.
    """
    sent = False
    
    # Telegram
    if TELEGRAM_WEBHOOK_URL:
        telegram_msg = _format_telegram_alert(severity, title, details)
        if _send_telegram(telegram_msg):
            sent = True
    
    # Discord
    if DISCORD_WEBHOOK_URL:
        discord_payload = _format_discord_alert(severity, title, details)
        if _send_discord(discord_payload):
            sent = True
    
    return sent


# ─── Pre-built Alert Functions ─────────────────────────────────────────

def alert_admin_login(email: str, ip_hash: str, success: bool, mfa: bool = False):
    """Alert on admin login attempt."""
    severity = SEVERITY_LOW if success else SEVERITY_MEDIUM
    title = "Admin Login" + (" Failed" if not success else "")
    details = {
        "Email": email,
        "IP Hash": ip_hash[:16] + "...",
        "MFA": "Yes" if mfa else "No",
        "Status": "Success" if success else "Failed",
    }
    send_security_alert(severity, title, details)


def alert_brute_force_detected(ip_hash: str, attempts: int, endpoint: str):
    """Alert on brute force detection."""
    details = {
        "IP Hash": ip_hash[:16] + "...",
        "Attempts": str(attempts),
        "Endpoint": endpoint,
        "Action": "Rate limited",
    }
    send_security_alert(SEVERITY_HIGH, "Brute Force Detected", details)


def alert_license_activation_anomaly(
    email: str,
    device_count: int,
    max_devices: int,
    ip_hash: str,
    anomaly_type: str,
):
    """Alert on suspicious license activation pattern."""
    details = {
        "Email": email,
        "Devices": f"{device_count}/{max_devices}",
        "IP Hash": ip_hash[:16] + "...",
        "Anomaly": anomaly_type,
    }
    send_security_alert(SEVERITY_HIGH, "License Activation Anomaly", details)


def alert_license_revoked(
    license_id: str,
    email: str,
    reason: str,
    revoked_by: str,
):
    """Alert on license revocation."""
    details = {
        "License": license_id[:12] + "...",
        "Email": email,
        "Reason": reason,
        "Revoked By": revoked_by,
    }
    send_security_alert(SEVERITY_MEDIUM, "License Revoked", details)


def alert_suspicious_api_pattern(
    ip_hash: str,
    pattern: str,
    request_count: int,
    time_window: str,
):
    """Alert on suspicious API usage pattern."""
    details = {
        "IP Hash": ip_hash[:16] + "...",
        "Pattern": pattern,
        "Requests": str(request_count),
        "Window": time_window,
    }
    send_security_alert(SEVERITY_HIGH, "Suspicious API Pattern", details)


def alert_impossible_travel(
    email: str,
    ip_hash_1: str,
    ip_hash_2: str,
    time_diff_minutes: int,
):
    """
    E2E-05: Alert on impossible travel (same user, different IPs, short time).
    
    If a user activates from IP_A and then from IP_B within minutes,
    and the geographic distance between IPs is >1000km, this is suspicious.
    """
    details = {
        "Email": email,
        "IP 1": ip_hash_1[:16] + "...",
        "IP 2": ip_hash_2[:16] + "...",
        "Time Diff": f"{time_diff_minutes} min",
        "Risk": "Possible credential sharing or theft",
    }
    send_security_alert(SEVERITY_CRITICAL, "Impossible Travel Detected", details)