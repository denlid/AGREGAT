"""
L-02: Honeypot Module for Admin Login Protection

Security Model:
    A honeypot is a decoy system designed to detect and deflect automated
    attacks. This module implements multiple honeypot techniques for the
    admin login endpoint:

    1. Hidden form fields — bots fill them, humans don't see them
    2. Timing analysis — bots submit too fast or too predictably
    3. Decoy endpoints — fake admin URLs that trap scanners
    4. Behavioral fingerprinting — mouse/keyboard patterns (if JS enabled)
    5. IP reputation tracking — repeat offenders get blocked

    When a honeypot is triggered:
    - The request appears to succeed (200 OK with fake response)
    - The attacker's IP is logged and optionally blocked
    - Security team is alerted via logging/monitoring
    - No real authentication is attempted

Threat Model:
    - Automated credential stuffing attacks
    - Brute force login attempts
    - Vulnerability scanners probing admin endpoints
    - Bot networks targeting authentication

Defense Strategy:
    - Never reveal that a honeypot was triggered (silent failure)
    - Return plausible fake responses to waste attacker resources
    - Track and block repeat offenders automatically
    - Log all honeypot events for security analysis
"""

import hashlib
import logging
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, List, Set, Tuple
from ipaddress import ip_address, IPv4Address, IPv6Address

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

logger = logging.getLogger(__name__)


class HoneypotTrigger(Enum):
    """Reasons a honeypot was triggered."""
    HIDDEN_FIELD = "hidden_field"          # Bot filled hidden form field
    TIMING_TOO_FAST = "timing_too_fast"    # Form submitted impossibly fast
    TIMING_TOO_SLOW = "timing_too_slow"    # Form submitted after very long delay
    DECOY_ENDPOINT = "decoy_endpoint"      # Accessed decoy/fake endpoint
    MISSING_TOKEN = "missing_token"        # Missing CSRF-like honeypot token
    INVALID_TOKEN = "invalid_token"        # Tampered honeypot token
    REPEAT_OFFENDER = "repeat_offender"    # IP previously triggered honeypot
    SUSPICIOUS_UA = "suspicious_ua"        # Known bot user-agent
    HEADLESS_BROWSER = "headless_browser"  # Detected headless browser


@dataclass
class HoneypotEvent:
    """Record of a honeypot trigger."""
    timestamp: datetime
    trigger: HoneypotTrigger
    ip_address: str
    path: str
    user_agent: str
    details: Dict[str, str] = field(default_factory=dict)
    blocked: bool = False


@dataclass
class IPReputation:
    """Track IP reputation for honeypot decisions."""
    first_seen: datetime
    honeypot_triggers: int = 0
    last_trigger: Optional[datetime] = None
    blocked_until: Optional[datetime] = None
    triggers: List[HoneypotEvent] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        """Check if IP is currently blocked."""
        if self.blocked_until and datetime.now() < self.blocked_until:
            return True
        return False

    def add_trigger(self, event: HoneypotEvent, block_duration: timedelta) -> None:
        """Record a honeypot trigger and potentially block IP."""
        self.honeypot_triggers += 1
        self.last_trigger = event.timestamp
        self.triggers.append(event)

        # Progressive blocking: longer blocks for repeat offenders
        if self.honeypot_triggers >= 3:
            multiplier = min(self.honeypot_triggers - 2, 10)
            self.blocked_until = datetime.now() + (block_duration * multiplier)
            event.blocked = True
            logger.warning(
                "IP %s blocked until %s (trigger count: %d)",
                event.ip_address,
                self.blocked_until,
                self.honeypot_triggers,
            )


class HoneypotConfig:
    """Configuration for honeypot behavior."""

    def __init__(
        self,
        # Hidden field settings
        hidden_field_name: str = "website_url",
        hidden_field_value: str = "",
        # Timing settings (seconds)
        min_submit_time: float = 1.5,
        max_submit_time: float = 3600.0,
        # Block settings
        initial_block_duration: timedelta = timedelta(hours=1),
        max_triggers_before_block: int = 3,
        # Decoy endpoints
        decoy_paths: Optional[List[str]] = None,
        # Token settings
        token_secret: Optional[str] = None,
        token_ttl: timedelta = timedelta(hours=2),
        # User-agent blocking
        blocked_user_agents: Optional[List[str]] = None,
        # Cleanup
        cleanup_interval: timedelta = timedelta(hours=24),
        max_ip_records: int = 10000,
    ):
        self.hidden_field_name = hidden_field_name
        self.hidden_field_value = hidden_field_value
        self.min_submit_time = min_submit_time
        self.max_submit_time = max_submit_time
        self.initial_block_duration = initial_block_duration
        self.max_triggers_before_block = max_triggers_before_block
        self.decoy_paths = decoy_paths or [
            "/admin/login",
            "/administrator",
            "/wp-admin",
            "/wp-login.php",
            "/phpmyadmin",
            "/admin.php",
            "/manager/html",
            "/console",
            "/.env",
            "/api/v1/admin",
        ]
        self.token_secret = token_secret or secrets.token_hex(32)
        self.token_ttl = token_ttl
        self.blocked_user_agents = blocked_user_agents or [
            "sqlmap",
            "nikto",
            "nmap",
            "masscan",
            "dirbuster",
            "gobuster",
            "wfuzz",
            "hydra",
            "medusa",
            "burpsuite",
        ]
        self.cleanup_interval = cleanup_interval
        self.max_ip_records = max_ip_records


class HoneypotManager:
    """
    Manages honeypot detection and IP reputation tracking.

    Usage:
        config = HoneypotConfig()
        manager = HoneypotManager(config)

        # Check if request triggers honeypot
        trigger = manager.check_request(request)
        if trigger:
            return manager.get_decoy_response(trigger)

        # Generate honeypot token for forms
        token = manager.generate_token(client_ip)
    """

    def __init__(self, config: Optional[HoneypotConfig] = None):
        self.config = config or HoneypotConfig()
        self._ip_reputation: Dict[str, IPReputation] = {}
        self._events: List[HoneypotEvent] = []
        self._last_cleanup = datetime.now()

        logger.info(
            "HoneypotManager initialized: %d decoy paths, block after %d triggers",
            len(self.config.decoy_paths),
            self.config.max_triggers_before_block,
        )

    def check_request(self, request: Request, form_data: Optional[Dict] = None) -> Optional[HoneypotEvent]:
        """
        Check if a request triggers any honeypot.

        Args:
            request: Starlette Request object
            form_data: Optional parsed form data

        Returns:
            HoneypotEvent if triggered, None if request appears legitimate
        """
        client_ip = self._get_client_ip(request)
        path = request.url.path.lower()
        user_agent = request.headers.get("user-agent", "").lower()

        # 1. Check if IP is already blocked
        if self._is_ip_blocked(client_ip):
            return HoneypotEvent(
                timestamp=datetime.now(),
                trigger=HoneypotTrigger.REPEAT_OFFENDER,
                ip_address=client_ip,
                path=path,
                user_agent=user_agent,
                details={"reason": "IP previously blocked"},
                blocked=True,
            )

        # 2. Check decoy endpoints
        if path in [p.lower() for p in self.config.decoy_paths]:
            return self._create_event(
                HoneypotTrigger.DECOY_ENDPOINT,
                client_ip, path, user_agent,
                {"decoy_path": path},
            )

        # 3. Check suspicious user-agents
        for blocked_ua in self.config.blocked_user_agents:
            if blocked_ua.lower() in user_agent:
                return self._create_event(
                    HoneypotTrigger.SUSPICIOUS_UA,
                    client_ip, path, user_agent,
                    {"matched_pattern": blocked_ua},
                )

        # 4. Check form data if provided
        if form_data:
            trigger = self._check_form_honeypot(form_data, client_ip, path, user_agent)
            if trigger:
                return trigger

        # 5. Check timing (if token present with timestamp)
        token = request.query_params.get("ht") or (form_data or {}).get("ht")
        if token:
            trigger = self._check_timing(token, client_ip, path, user_agent)
            if trigger:
                return trigger

        return None

    def generate_token(self, client_ip: str) -> str:
        """
        Generate a honeypot token with embedded timestamp.

        Token format: timestamp_hex:hmac(timestamp + ip)
        This allows verification and timing analysis.
        """
        timestamp = int(time.time())
        timestamp_hex = hex(timestamp)[2:]

        # Create HMAC-like signature using secret
        payload = f"{timestamp}:{client_ip}"
        signature = hashlib.sha256(
            f"{self.config.token_secret}:{payload}".encode()
        ).hexdigest()[:16]

        return f"{timestamp_hex}:{signature}"

    def verify_token(self, token: str, client_ip: str) -> Tuple[bool, Optional[float]]:
        """
        Verify honeypot token and return (valid, age_seconds).

        Returns:
            Tuple of (is_valid, token_age_seconds)
        """
        try:
            parts = token.split(":")
            if len(parts) != 2:
                return False, None

            timestamp_hex, signature = parts
            timestamp = int(timestamp_hex, 16)
            age = time.time() - timestamp

            # Verify signature
            payload = f"{timestamp}:{client_ip}"
            expected_sig = hashlib.sha256(
                f"{self.config.token_secret}:{payload}".encode()
            ).hexdigest()[:16]

            if signature != expected_sig:
                return False, None

            return True, age

        except (ValueError, TypeError):
            return False, None

    def get_decoy_response(self, event: HoneypotEvent) -> Response:
        """
        Generate a decoy response that appears legitimate to the attacker.

        The response should:
        - Return 200 OK (not 403, which reveals blocking)
        - Include plausible but fake content
        - Not reveal that a honeypot was triggered
        """
        # Record event
        self._record_event(event)

        # Fake login response (appears to succeed but does nothing)
        fake_responses = [
            JSONResponse(
                content={
                    "status": "success",
                    "message": "Login successful. Redirecting...",
                    "redirect": "/dashboard",
                    "token": secrets.token_hex(32),  # Fake token
                },
                status_code=200,
            ),
            JSONResponse(
                content={
                    "status": "pending",
                    "message": "Authentication in progress. Please wait...",
                    "requires_2fa": True,
                },
                status_code=200,
            ),
            JSONResponse(
                content={
                    "status": "error",
                    "message": "Invalid credentials. Please try again.",
                    "attempts_remaining": 3,
                },
                status_code=200,  # Still 200 to not reveal honeypot
            ),
        ]

        # Deterministic selection based on IP (consistent per-IP)
        idx = int(hashlib.md5(event.ip_address.encode()).hexdigest(), 16) % len(fake_responses)
        return fake_responses[idx]

    def get_events(self, limit: int = 100) -> List[Dict]:
        """Get recent honeypot events for monitoring."""
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "trigger": e.trigger.value,
                "ip": e.ip_address,
                "path": e.path,
                "user_agent": e.user_agent[:100],
                "blocked": e.blocked,
                "details": e.details,
            }
            for e in self._events[-limit:]
        ]

    def get_stats(self) -> Dict:
        """Get honeypot statistics."""
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        return {
            "total_events": len(self._events),
            "events_last_24h": sum(1 for e in self._events if e.timestamp > yesterday),
            "events_last_7d": sum(1 for e in self._events if e.timestamp > week_ago),
            "blocked_ips": sum(1 for ip in self._ip_reputation.values() if ip.is_blocked),
            "total_tracked_ips": len(self._ip_reputation),
            "trigger_breakdown": self._get_trigger_breakdown(),
        }

    def cleanup(self) -> int:
        """Clean up old records. Returns number of records removed."""
        now = datetime.now()
        cutoff = now - timedelta(days=7)
        removed = 0

        ips_to_remove = []
        for ip, rep in self._ip_reputation.items():
            if rep.last_seen and rep.last_seen < cutoff and not rep.is_blocked:
                ips_to_remove.append(ip)

        for ip in ips_to_remove:
            del self._ip_reputation[ip]
            removed += 1

        # Clean old events
        original_count = len(self._events)
        self._events = [e for e in self._events if e.timestamp > cutoff]
        removed += original_count - len(self._events)

        self._last_cleanup = now

        if removed > 0:
            logger.info("Honeypot cleanup: removed %d records", removed)

        return removed

    def _check_form_honeypot(
        self, form_data: Dict, client_ip: str, path: str, user_agent: str
    ) -> Optional[HoneypotEvent]:
        """Check form data for honeypot triggers."""
        # Check hidden field
        if self.config.hidden_field_name in form_data:
            value = form_data[self.config.hidden_field_name]
            if value != self.config.hidden_field_value:
                return self._create_event(
                    HoneypotTrigger.HIDDEN_FIELD,
                    client_ip, path, user_agent,
                    {
                        "field_name": self.config.hidden_field_name,
                        "field_value": value[:50],
                    },
                )

        # Check honeypot token
        token = form_data.get("ht")
        if token:
            valid, age = self.verify_token(token, client_ip)
            if not valid:
                return self._create_event(
                    HoneypotTrigger.INVALID_TOKEN,
                    client_ip, path, user_agent,
                    {"reason": "Token verification failed"},
                )

        return None

    def _check_timing(
        self, token: str, client_ip: str, path: str, user_agent: str
    ) -> Optional[HoneypotEvent]:
        """Check submission timing via token age."""
        valid, age = self.verify_token(token, client_ip)
        if not valid:
            return self._create_event(
                HoneypotTrigger.INVALID_TOKEN,
                client_ip, path, user_agent,
            )

        if age is not None:
            if age < self.config.min_submit_time:
                return self._create_event(
                    HoneypotTrigger.TIMING_TOO_FAST,
                    client_ip, path, user_agent,
                    {"submit_time": f"{age:.2f}s", "min": f"{self.config.min_submit_time}s"},
                )
            if age > self.config.max_submit_time:
                return self._create_event(
                    HoneypotTrigger.TIMING_TOO_SLOW,
                    client_ip, path, user_agent,
                    {"submit_time": f"{age:.2f}s", "max": f"{self.config.max_submit_time}s"},
                )

        return None

    def _create_event(
        self,
        trigger: HoneypotTrigger,
        ip: str,
        path: str,
        user_agent: str,
        details: Optional[Dict] = None,
    ) -> HoneypotEvent:
        """Create and record a honeypot event."""
        event = HoneypotEvent(
            timestamp=datetime.now(),
            trigger=trigger,
            ip_address=ip,
            path=path,
            user_agent=user_agent,
            details=details or {},
        )
        self._record_event(event)
        return event

    def _record_event(self, event: HoneypotEvent) -> None:
        """Record honeypot event and update IP reputation."""
        self._events.append(event)

        # Update IP reputation
        if event.ip_address not in self._ip_reputation:
            if len(self._ip_reputation) >= self.config.max_ip_records:
                self.cleanup()

            self._ip_reputation[event.ip_address] = IPReputation(
                first_seen=event.timestamp,
            )

        rep = self._ip_reputation[event.ip_address]
        rep.add_trigger(event, self.config.initial_block_duration)

        # Log event
        logger.warning(
            "HONEYPOT TRIGGERED: %s from %s on %s (UA: %s) %s",
            event.trigger.value,
            event.ip_address,
            event.path,
            event.user_agent[:80],
            "[BLOCKED]" if event.blocked else "",
        )

        # Periodic cleanup
        if datetime.now() - self._last_cleanup > self.config.cleanup_interval:
            self.cleanup()

    def _is_ip_blocked(self, ip: str) -> bool:
        """Check if IP is currently blocked."""
        rep = self._ip_reputation.get(ip)
        return rep.is_blocked if rep else False

    def _get_trigger_breakdown(self) -> Dict[str, int]:
        """Get count of events by trigger type."""
        breakdown: Dict[str, int] = defaultdict(int)
        for event in self._events:
            breakdown[event.trigger.value] += 1
        return dict(breakdown)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP from request, handling proxies."""
        # Check X-Forwarded-For header (behind proxy/load balancer)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # First IP is the original client
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        # Fall back to direct connection
        if request.client:
            return request.client.host

        return "unknown"


class HoneypotMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that applies honeypot checks to all requests.

    Usage:
        from fastapi import FastAPI
        from server.app.security.honeypot import HoneypotMiddleware, HoneypotConfig

        app = FastAPI()
        config = HoneypotConfig()
        app.add_middleware(HoneypotMiddleware, config=config)
    """

    def __init__(self, app, config: Optional[HoneypotConfig] = None):
        super().__init__(app)
        self.manager = HoneypotManager(config)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Check request against honeypots before processing."""
        # Quick check: blocked IPs get decoy response immediately
        client_ip = HoneypotManager._get_client_ip(request)
        if self.manager._is_ip_blocked(client_ip):
            event = HoneypotEvent(
                timestamp=datetime.now(),
                trigger=HoneypotTrigger.REPEAT_OFFENDER,
                ip_address=client_ip,
                path=request.url.path,
                user_agent=request.headers.get("user-agent", ""),
                blocked=True,
            )
            return self.manager.get_decoy_response(event)

        # Check decoy endpoints and user-agents (no body needed)
        event = self.manager.check_request(request)
        if event:
            return self.manager.get_decoy_response(event)

        # Process normally
        return await call_next(request)


__all__ = [
    "HoneypotManager",
    "HoneypotMiddleware",
    "HoneypotConfig",
    "HoneypotEvent",
    "HoneypotTrigger",
    "IPReputation",
]