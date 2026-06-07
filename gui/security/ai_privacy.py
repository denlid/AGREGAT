"""
H-03: AI Privacy & Code Sharing Consent Module

Security Model:
    When users share code/strategies with AI services (OpenAI, Anthropic, custom endpoints),
    sensitive information may leak: API keys, credentials, personal data, proprietary logic.

    This module provides:
    1. Explicit user consent workflow before any code is sent to AI
    2. Code sanitization that strips secrets, keys, and sensitive patterns
    3. Configurable privacy levels (strict/balanced/permissive)
    4. Audit logging of all AI data sharing events
    5. Local-only mode that blocks all external AI calls

Threat Model:
    - Accidental leakage of API keys in strategy code
    - Proprietary trading logic sent to third-party AI
    - User credentials embedded in comments or config
    - PII (personally identifiable information) in logs/data

Privacy Levels:
    STRICT:   Block all external AI, local models only
    BALANCED: Sanitize aggressively, require per-request consent
    PERMISSIVE: Basic sanitization, one-time consent per session
"""

import re
import logging
import hashlib
import json
from enum import Enum
from typing import Optional, List, Dict, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class PrivacyLevel(Enum):
    """AI privacy enforcement levels."""
    STRICT = "strict"         # No external AI, local only
    BALANCED = "balanced"     # Aggressive sanitization + per-request consent
    PERMISSIVE = "permissive" # Basic sanitization + session consent


@dataclass
class ConsentRecord:
    """Record of user consent for AI data sharing."""
    timestamp: datetime
    privacy_level: PrivacyLevel
    consent_hash: str  # Hash of what was consented to
    expires_at: Optional[datetime] = None
    revoked: bool = False

    def is_valid(self) -> bool:
        """Check if consent is still valid."""
        if self.revoked:
            return False
        if self.expires_at and datetime.now() > self.expires_at:
            return False
        return True


@dataclass
class SanitizationResult:
    """Result of code sanitization."""
    sanitized_code: str
    removed_patterns: List[str]
    risk_score: float  # 0.0 (safe) to 1.0 (high risk)
    warnings: List[str]
    blocked: bool = False


# Patterns that indicate sensitive data
SENSITIVE_PATTERNS = {
    # API Keys & Tokens
    "api_key": re.compile(
        r'(?:api[_-]?key|apikey)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{16,}["\']?',
        re.IGNORECASE
    ),
    "api_secret": re.compile(
        r'(?:api[_-]?secret|apisecret)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{16,}["\']?',
        re.IGNORECASE
    ),
    "bearer_token": re.compile(
        r'(?:bearer|token)\s*[=:]\s*["\']?[A-Za-z0-9_\-\.]{20,}["\']?',
        re.IGNORECASE
    ),
    "openai_key": re.compile(
        r'sk-[A-Za-z0-9]{20,}',
        re.IGNORECASE
    ),
    "anthropic_key": re.compile(
        r'sk-ant-[A-Za-z0-9]{20,}',
        re.IGNORECASE
    ),
    # Credentials
    "password": re.compile(
        r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\']{4,}["\']?',
        re.IGNORECASE
    ),
    "username_password": re.compile(
        r'(?:user(?:name)?|login)\s*[=:]\s*["\']?[^\s"\']+["\']?'
        r'.*?'
        r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\']+["\']?',
        re.IGNORECASE | re.DOTALL
    ),
    # Connection strings
    "database_url": re.compile(
        r'(?:postgresql|mysql|sqlite|mongodb|redis)://[^\s"\']+',
        re.IGNORECASE
    ),
    "connection_string": re.compile(
        r'(?:Server|Data Source|Host)=[^;]+;(?:.*?(?:Password|Pwd)=[^;]+)',
        re.IGNORECASE
    ),
    # Cloud credentials
    "aws_key": re.compile(
        r'AKIA[0-9A-Z]{16}',
    ),
    "aws_secret": re.compile(
        r'(?:aws[_-]?secret[_-]?access[_-]?key)\s*[=:]\s*["\']?[A-Za-z0-9/+=]{40}["\']?',
        re.IGNORECASE
    ),
    "azure_key": re.compile(
        r'(?:azure[_-]?key|account[_-]?key)\s*[=:]\s*["\']?[A-Za-z0-9+/=]{44,}["\']?',
        re.IGNORECASE
    ),
    "gcp_key": re.compile(
        r'"type"\s*:\s*"service_account"',
        re.IGNORECASE
    ),
    # Private keys
    "private_key": re.compile(
        r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
    ),
    "encrypted_key": re.compile(
        r'-----BEGIN ENCRYPTED PRIVATE KEY-----',
    ),
    # Personal data
    "email": re.compile(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    ),
    "phone": re.compile(
        r'(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}',
    ),
    "credit_card": re.compile(
        r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b',
    ),
    # File paths (may reveal system info)
    "absolute_path_windows": re.compile(
        r'[A-Z]:\\(?:[^\s"\':<>|*?]+\\)*[^\s"\':<>|*?]*',
    ),
    "absolute_path_unix": re.compile(
        r'/(?:home|root|Users)/[^\s"\']+',
    ),
    # Trading-specific sensitive data
    "account_id": re.compile(
        r'(?:account[_-]?id|account[_-]?number)\s*[=:]\s*["\']?[A-Za-z0-9]{6,}["\']?',
        re.IGNORECASE
    ),
    "broker_credentials": re.compile(
        r'(?:broker|exchange)[_-]?(?:api|key|secret|token)\s*[=:]\s*["\']?[^\s"\']+["\']?',
        re.IGNORECASE
    ),
}

# Patterns to always block regardless of privacy level
CRITICAL_PATTERNS = {
    "private_key",
    "openai_key",
    "anthropic_key",
    "aws_key",
    "password",
    "credit_card",
}


class AIPrivacyManager:
    """
    Manages AI privacy consent and code sanitization.

    Usage:
        manager = AIPrivacyManager()
        manager.set_privacy_level(PrivacyLevel.BALANCED)

        # Check consent before sending to AI
        if manager.check_consent():
            result = manager.sanitize_code(strategy_code)
            if not result.blocked:
                send_to_ai(result.sanitized_code)
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        consent_ttl_hours: int = 8,
    ):
        """
        Initialize AI Privacy Manager.

        Args:
            config_path: Path to persist consent records (JSON)
            consent_ttl_hours: Hours until consent expires (default: 8h session)
        """
        self._privacy_level = PrivacyLevel.BALANCED  # Default to balanced
        self._consent_records: List[ConsentRecord] = []
        self._consent_ttl = timedelta(hours=consent_ttl_hours)
        self._config_path = config_path or Path.home() / ".agregat" / "ai_privacy.json"
        self._session_consent_given = False
        self._local_only_endpoints: Set[str] = {
            "localhost",
            "127.0.0.1",
            "::1",
        }

        self._load_config()
        logger.info(
            "AIPrivacyManager initialized: level=%s",
            self._privacy_level.value,
        )

    @property
    def privacy_level(self) -> PrivacyLevel:
        """Current privacy level."""
        return self._privacy_level

    def set_privacy_level(self, level: PrivacyLevel) -> None:
        """
        Set privacy level and invalidate existing consent if level changes.

        Args:
            level: New privacy level
        """
        if level != self._privacy_level:
            # Revoke existing consent on level change
            for record in self._consent_records:
                record.revoked = True
            self._session_consent_given = False
            logger.info("Privacy level changed to %s, consent revoked", level.value)

        self._privacy_level = level
        self._save_config()

    def grant_consent(
        self,
        code_hash: Optional[str] = None,
        duration_hours: Optional[int] = None,
    ) -> ConsentRecord:
        """
        Grant user consent for AI data sharing.

        Args:
            code_hash: Hash of specific code being consented (optional)
            duration_hours: Override TTL for this consent

        Returns:
            ConsentRecord with timestamp and expiry
        """
        ttl = timedelta(hours=duration_hours) if duration_hours else self._consent_ttl
        now = datetime.now()

        record = ConsentRecord(
            timestamp=now,
            privacy_level=self._privacy_level,
            consent_hash=code_hash or "general",
            expires_at=now + ttl if self._privacy_level != PrivacyLevel.STRICT else None,
        )

        self._consent_records.append(record)
        self._session_consent_given = True
        self._save_config()

        logger.info(
            "Consent granted: level=%s, expires=%s",
            self._privacy_level.value,
            record.expires_at,
        )

        return record

    def revoke_consent(self) -> None:
        """Revoke all existing consent."""
        for record in self._consent_records:
            record.revoked = True
        self._session_consent_given = False
        self._save_config()
        logger.info("All consent revoked")

    def check_consent(self, code_hash: Optional[str] = None) -> bool:
        """
        Check if valid consent exists.

        Args:
            code_hash: Hash of code to check specific consent

        Returns:
            True if consent is valid, False otherwise
        """
        if self._privacy_level == PrivacyLevel.STRICT:
            logger.debug("STRICT mode: no external AI consent possible")
            return False

        # Check session consent for permissive mode
        if self._privacy_level == PrivacyLevel.PERMISSIVE and self._session_consent_given:
            return True

        # Check for valid consent records
        for record in reversed(self._consent_records):
            if not record.is_valid():
                continue
            if record.privacy_level != self._privacy_level:
                continue
            if code_hash and record.consent_hash not in (code_hash, "general"):
                continue
            return True

        logger.debug("No valid consent found for level=%s", self._privacy_level.value)
        return False

    def sanitize_code(self, code: str, aggressive: Optional[bool] = None) -> SanitizationResult:
        """
        Sanitize code before sending to AI.

        Args:
            code: Source code to sanitize
            aggressive: Override aggressive mode (derived from privacy level if None)

        Returns:
            SanitizationResult with sanitized code and metadata
        """
        if aggressive is None:
            aggressive = self._privacy_level in (PrivacyLevel.STRICT, PrivacyLevel.BALANCED)

        sanitized = code
        removed_patterns: List[str] = []
        warnings: List[str] = []
        risk_score = 0.0

        # Check each sensitive pattern
        for pattern_name, pattern_regex in SENSITIVE_PATTERNS.items():
            matches = pattern_regex.findall(sanitized)
            if not matches:
                continue

            is_critical = pattern_name in CRITICAL_PATTERNS
            should_remove = aggressive or is_critical

            for match in matches:
                removed_patterns.append(f"{pattern_name}: {self._mask_value(match)}")

                if should_remove:
                    # Replace with placeholder
                    placeholder = f"<{pattern_name.upper()}_REMOVED>"
                    sanitized = sanitized.replace(match, placeholder)
                    warnings.append(f"Removed {pattern_name}")
                else:
                    # Just warn in permissive mode for non-critical
                    warnings.append(f"Found {pattern_name} (not removed in permissive mode)")

                # Increase risk score
                risk_increment = 0.15 if is_critical else 0.05
                risk_score = min(1.0, risk_score + risk_increment)

        # Additional sanitization for strict mode
        if self._privacy_level == PrivacyLevel.STRICT:
            # Remove all comments (may contain sensitive notes)
            sanitized = re.sub(r'#.*$', '', sanitized, flags=re.MULTILINE)
            sanitized = re.sub(r'""".*?"""', '', sanitized, flags=re.DOTALL)
            sanitized = re.sub(r"'''.*?'''", '', sanitized, flags=re.DOTALL)
            warnings.append("All comments removed (strict mode)")

        # Block if risk too high or strict mode with critical findings
        blocked = False
        if self._privacy_level == PrivacyLevel.STRICT and risk_score > 0:
            blocked = True
            warnings.append("BLOCKED: Strict mode with sensitive data detected")
        elif risk_score >= 0.8:
            blocked = True
            warnings.append("BLOCKED: Risk score too high (>=0.8)")

        result = SanitizationResult(
            sanitized_code=sanitized,
            removed_patterns=removed_patterns,
            risk_score=risk_score,
            warnings=warnings,
            blocked=blocked,
        )

        logger.info(
            "Code sanitized: risk=%.2f, patterns_removed=%d, blocked=%s",
            risk_score,
            len(removed_patterns),
            blocked,
        )

        return result

    def is_endpoint_allowed(self, endpoint_url: str) -> bool:
        """
        Check if AI endpoint URL is allowed under current privacy level.

        Args:
            endpoint_url: URL of the AI service

        Returns:
            True if endpoint is allowed
        """
        if self._privacy_level == PrivacyLevel.STRICT:
            # Only local endpoints allowed
            for local in self._local_only_endpoints:
                if local in endpoint_url:
                    return True
            logger.warning("Endpoint blocked (strict mode): %s", endpoint_url)
            return False

        return True

    def get_audit_log(self) -> List[Dict]:
        """Get audit log of all consent events."""
        return [
            {
                "timestamp": r.timestamp.isoformat(),
                "level": r.privacy_level.value,
                "hash": r.consent_hash,
                "expires": r.expires_at.isoformat() if r.expires_at else None,
                "revoked": r.revoked,
            }
            for r in self._consent_records
        ]

    def compute_code_hash(self, code: str) -> str:
        """Compute SHA-256 hash of code for consent tracking."""
        return hashlib.sha256(code.encode()).hexdigest()[:16]

    @staticmethod
    def _mask_value(value: str, visible_chars: int = 4) -> str:
        """Mask sensitive value for logging."""
        if len(value) <= visible_chars:
            return "***"
        return value[:visible_chars] + "***"

    def _load_config(self) -> None:
        """Load persisted privacy config."""
        try:
            if self._config_path.exists():
                data = json.loads(self._config_path.read_text())
                level_str = data.get("privacy_level", "balanced")
                self._privacy_level = PrivacyLevel(level_str)
                logger.info("Loaded privacy config: level=%s", self._privacy_level.value)
        except Exception as e:
            logger.warning("Failed to load privacy config: %s", e)

    def _save_config(self) -> None:
        """Persist privacy config."""
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "privacy_level": self._privacy_level.value,
                "updated_at": datetime.now().isoformat(),
            }
            self._config_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("Failed to save privacy config: %s", e)


def create_privacy_dialog_message(privacy_level: PrivacyLevel) -> str:
    """
    Create user-facing consent dialog message.

    Args:
        privacy_level: Current privacy level

    Returns:
        Formatted message for consent dialog
    """
    messages = {
        PrivacyLevel.STRICT: (
            "🔒 STRICT Privacy Mode\n\n"
            "External AI services are BLOCKED.\n"
            "Only local AI models are allowed.\n\n"
            "No code or data will be sent to external services."
        ),
        PrivacyLevel.BALANCED: (
            "⚖️ BALANCED Privacy Mode\n\n"
            "Your code will be sanitized before sending to AI.\n"
            "API keys, passwords, and sensitive data will be removed.\n\n"
            "Do you consent to share sanitized code with AI services?\n"
            "(Consent expires after 8 hours)"
        ),
        PrivacyLevel.PERMISSIVE: (
            "🔓 PERMISSIVE Privacy Mode\n\n"
            "Basic sanitization will be applied to your code.\n"
            "Critical secrets (private keys, passwords) will be removed.\n\n"
            "Do you consent to share code with AI services?\n"
            "(Consent valid for this session)"
        ),
    }
    return messages.get(privacy_level, "Unknown privacy level")


__all__ = [
    "AIPrivacyManager",
    "PrivacyLevel",
    "SanitizationResult",
    "ConsentRecord",
    "SENSITIVE_PATTERNS",
    "CRITICAL_PATTERNS",
    "create_privacy_dialog_message",
]