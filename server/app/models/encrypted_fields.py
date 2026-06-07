"""
AGREGAT Server — Encrypted Database Fields

H-02 FIX: Automatic encryption for sensitive database columns.

Security model:
- Fernet (AES-128-CBC + HMAC-SHA256) encryption at rest
- Transparent encryption/decryption via SQLAlchemy TypeDecorator
- Key rotation support (multiple keys, newest for encryption)
- Encrypted fields are searchable via HMAC index (optional)
- GDPR compliance: PII encrypted, not just hashed

Usage:
    class AuditEvent(Base):
        payload = Column(EncryptedJSON, nullable=True)
        ip_hash = Column(EncryptedString(64), nullable=True)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import TypeDecorator, String, Text
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger("agregat.server.models.encrypted_fields")


# ===================================================================
# Key Management
# ===================================================================

def _get_fernet(keys: list[str] | str) -> Fernet | MultiFernet:
    """
    Create Fernet instance from key(s).
    
    Supports:
    - Single key: Fernet(key)
    - Multiple keys: MultiFernet([Fernet(k) for k in keys])
      MultiFernet encrypts with first key, decrypts with any key
      (enables key rotation)
    """
    if isinstance(keys, str):
        keys = [keys]
    
    if not keys:
        raise ValueError("No encryption keys configured")
    
    fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
    
    if len(fernets) == 1:
        return fernets[0]
    
    return MultiFernet(fernets)


# ===================================================================
# Encrypted String Type
# ===================================================================

class EncryptedString(TypeDecorator):
    """
    SQLAlchemy type that automatically encrypts string values.
    
    Stored as: Fernet token (base64-encoded, starts with 'gAAAAA')
    
    Args:
        max_length: Max length of PLAINTEXT value (ciphertext is ~2x)
        keys: Encryption key(s) — passed from settings
    """
    
    impl = Text
    cache_ok = True
    
    def __init__(
        self,
        max_length: int = 255,
        keys: Optional[list[str] | str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_length = max_length
        self._keys = keys
    
    def _get_keys(self) -> list[str] | str:
        """Get keys — lazy load from settings if not provided."""
        if self._keys:
            return self._keys
        
        try:
            from app.config import settings
            return settings.encryption_key
        except Exception:
            raise RuntimeError(
                "EncryptedString requires encryption_key in settings "
                "or explicit keys parameter"
            )
    
    def process_bind_param(self, value: Any, dialect) -> Optional[str]:
        """Encrypt value before storing in database."""
        if value is None:
            return None
        
        if not isinstance(value, str):
            value = str(value)
        
        # Already encrypted? (starts with Fernet prefix)
        if value.startswith("gAAAAA"):
            return value
        
        fernet = _get_fernet(self._get_keys())
        encrypted = fernet.encrypt(value.encode())
        return encrypted.decode()
    
    def process_result_value(self, value: Any, dialect) -> Optional[str]:
        """Decrypt value when reading from database."""
        if value is None:
            return None
        
        if not isinstance(value, str):
            return str(value)
        
        # Not encrypted? Return as-is (migration support)
        if not value.startswith("gAAAAA"):
            logger.debug("Plaintext value found in encrypted column — returning as-is")
            return value
        
        try:
            fernet = _get_fernet(self._get_keys())
            decrypted = fernet.decrypt(value.encode())
            return decrypted.decode()
        except InvalidToken:
            logger.error("Failed to decrypt value — key mismatch or corrupted data")
            return None
        except Exception as e:
            logger.error("Decryption error: %s", e)
            return None


# ===================================================================
# Encrypted JSON Type
# ===================================================================

class EncryptedJSON(TypeDecorator):
    """
    SQLAlchemy type that automatically encrypts JSON values.
    
    Stores JSON dict/list as encrypted string.
    Transparently serializes/deserializes JSON.
    
    Usage:
        class AuditEvent(Base):
            payload = Column(EncryptedJSON, nullable=True)
            
        # Usage:
        event.payload = {"email": "user@example.com", "ip": "1.2.3.4"}
        # Stored encrypted in DB
        # When read back: returns dict automatically
    """
    
    impl = Text
    cache_ok = True
    
    def __init__(
        self,
        keys: Optional[list[str] | str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._keys = keys
    
    def _get_keys(self) -> list[str] | str:
        if self._keys:
            return self._keys
        try:
            from app.config import settings
            return settings.encryption_key
        except Exception:
            raise RuntimeError(
                "EncryptedJSON requires encryption_key in settings"
            )
    
    def process_bind_param(self, value: Any, dialect) -> Optional[str]:
        """Serialize JSON and encrypt."""
        if value is None:
            return None
        
        # Already encrypted?
        if isinstance(value, str) and value.startswith("gAAAAA"):
            return value
        
        json_str = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        fernet = _get_fernet(self._get_keys())
        encrypted = fernet.encrypt(json_str.encode())
        return encrypted.decode()
    
    def process_result_value(self, value: Any, dialect) -> Any:
        """Decrypt and deserialize JSON."""
        if value is None:
            return None
        
        if not isinstance(value, str):
            return value
        
        # Not encrypted? Try parsing as JSON directly (migration)
        if not value.startswith("gAAAAA"):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        
        try:
            fernet = _get_fernet(self._get_keys())
            decrypted = fernet.decrypt(value.encode())
            return json.loads(decrypted.decode())
        except InvalidToken:
            logger.error("Failed to decrypt JSON — key mismatch")
            return None
        except json.JSONDecodeError:
            logger.error("Decrypted value is not valid JSON")
            return None
        except Exception as e:
            logger.error("JSON decryption error: %s", e)
            return None


# ===================================================================
# Searchable Encrypted Field (HMAC Index)
# ===================================================================

class SearchableEncryptedString(TypeDecorator):
    """
    Encrypted string that supports exact-match lookups.
    
    Stores two values:
    1. Encrypted value (for retrieval)
    2. HMAC hash (for indexing/searching)
    
    This allows: WHERE hmac_column = :search_hmac
    Without decrypting all rows.
    
    Note: Requires a separate HMAC column in the model.
    """
    
    impl = Text
    cache_ok = True
    
    def __init__(
        self,
        hmac_key: Optional[str] = None,
        keys: Optional[list[str] | str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._hmac_key = hmac_key
        self._keys = keys
    
    def _get_keys(self) -> list[str] | str:
        if self._keys:
            return self._keys
        try:
            from app.config import settings
            return settings.encryption_key
        except Exception:
            raise RuntimeError("SearchableEncryptedString requires encryption_key")
    
    @staticmethod
    def compute_hmac(value: str, key: str) -> str:
        """Compute HMAC-SHA256 for searchable index."""
        import hmac as hmac_mod
        import hashlib
        return hmac_mod.new(
            key.encode(),
            value.lower().encode(),
            hashlib.sha256,
        ).hexdigest()
    
    def process_bind_param(self, value: Any, dialect) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("gAAAAA"):
            return value
        fernet = _get_fernet(self._get_keys())
        return fernet.encrypt(str(value).encode()).decode()
    
    def process_result_value(self, value: Any, dialect) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith("gAAAAA"):
            return str(value) if value else None
        try:
            fernet = _get_fernet(self._get_keys())
            return fernet.decrypt(value.encode()).decode()
        except Exception:
            return None


# ===================================================================
# Migration Helper
# ===================================================================

def migrate_plaintext_to_encrypted(session, model_class, column_name: str, batch_size: int = 100):
    """
    Migrate existing plaintext values to encrypted.
    
    Usage:
        migrate_plaintext_to_encrypted(db_session, AuditEvent, 'payload')
    """
    offset = 0
    total = 0
    
    while True:
        rows = session.query(model_class).offset(offset).limit(batch_size).all()
        if not rows:
            break
        
        for row in rows:
            value = getattr(row, column_name)
            if value and isinstance(value, str) and not value.startswith("gAAAAA"):
                # Force re-encryption by setting the attribute
                setattr(row, column_name, value)
                total += 1
        
        session.commit()
        offset += batch_size
    
    logger.info("Migrated %d plaintext values to encrypted in %s.%s",
                total, model_class.__name__, column_name)
    return total


__all__ = [
    'EncryptedString',
    'EncryptedJSON',
    'SearchableEncryptedString',
    'migrate_plaintext_to_encrypted',
]