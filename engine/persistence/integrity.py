"""Sprint 2.2 — Tamper-evidence helpers for ``.agbk`` archives.

This module concentrates the SHA-256 calculations used by
:mod:`engine.persistence` (over the canonical bytes of an ``.agbk``
archive's data files) and by :mod:`engine.data_loader` (over the
input CSV / Parquet that fed a session). It is intentionally tiny and
dependency-free so it can be imported from anywhere without dragging
in PySide6 or pandas.

Two helpers are exported:

* :func:`hash_file` — stream-hash a file on disk in 1 MiB chunks. Used
  for ``input_csv_sha256`` / ``input_parquet_sha256`` in ``meta.json``.
* :func:`hash_archive_payload` — hash the canonicalised concatenation
  of the ``.agbk`` data members (``trades.csv``, ``equity.csv``,
  ``drawings.json``, plus a stable subset of ``meta.json``). The
  result is what we store in ``meta['agbk_sha256']`` and re-verify on
  load to detect tampering.

Both helpers use SHA-256 — fast on modern CPUs, collision-resistant
enough for an integrity check, and reproducible across environments.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

# Members of the archive that participate in the integrity hash. The
# order is fixed so two correct saves of the same session produce
# bit-identical archive hashes regardless of dict-iteration order.
_HASHED_MEMBERS = ("trades.csv", "equity.csv", "drawings.json")

# Subset of `meta.json` fields baked into the archive hash. We
# intentionally exclude volatile fields like `saved_at` and
# `agbk_sha256` itself — including them would make the hash either
# self-referential or break determinism between two saves of the same
# session.
_HASHED_META_FIELDS = (
    "version",
    "format_version",
    "schema",
    "asset",
    "initial_equity",
    "equity",
    "max_equity",
    "max_drawdown_abs",
    "max_drawdown_pct",
    "current_idx",
    "risk_pct",
    "execution_model",
    "trade_count",
    "input_csv_sha256",
    "input_parquet_sha256",
    "seeds",
)

# Read 1 MiB at a time so a 200 MiB Parquet doesn't blow up RAM.
_CHUNK_BYTES = 1 << 20


def hash_file(path: str) -> str:
    """Stream the file at ``path`` through SHA-256 in 1 MiB chunks.

    Returns a 64-char lowercase hex digest. Raises ``FileNotFoundError``
    if ``path`` does not exist — the caller decides whether that's
    fatal or simply skips the ``input_*_sha256`` field on persistence.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_BYTES)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _canonical_json(value: Any) -> bytes:
    """Serialise ``value`` to canonical JSON bytes (sort keys, fixed sep).

    Hash inputs must be byte-stable across runs and machines; the JSON
    serialiser is the simplest stable encoder we already depend on.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def hash_archive_payload(
    *,
    meta: Dict[str, Any],
    trades_csv: str,
    equity_csv: str,
    drawings_json: str,
) -> str:
    """Hash the canonicalised payload of an ``.agbk`` archive.

    The hash is computed over a length-prefixed concatenation of:

    1. each archive member listed in :data:`_HASHED_MEMBERS`, in fixed
       order, encoded as UTF-8 bytes;
    2. the canonicalised JSON of the meta-field subset
       :data:`_HASHED_META_FIELDS`.

    Length-prefixing ensures distinct payloads can never collide even
    if one member's tail happens to look like the next member's head.
    """
    sha = hashlib.sha256()
    members = {
        "trades.csv": trades_csv,
        "equity.csv": equity_csv,
        "drawings.json": drawings_json,
    }
    for name in _HASHED_MEMBERS:
        body = members[name].encode("utf-8")
        sha.update(f"{name}:{len(body)}\n".encode("ascii"))
        sha.update(body)

    # Pull out the stable meta subset; missing fields are simply
    # absent from the canonical dict (they'd otherwise break replay
    # of legacy archives that didn't carry, e.g., `seeds`).
    meta_subset = {k: meta[k] for k in _HASHED_META_FIELDS if k in meta}
    canonical = _canonical_json(meta_subset)
    sha.update(f"meta:{len(canonical)}\n".encode("ascii"))
    sha.update(canonical)
    return sha.hexdigest()


def hash_meta_subset(meta: Dict[str, Any]) -> str:
    """Hash the meta subset alone — useful for unit tests and debugging."""
    return hashlib.sha256(
        _canonical_json({k: meta[k] for k in _HASHED_META_FIELDS if k in meta})
    ).hexdigest()


class IntegrityError(ValueError):
    """Raised when an ``.agbk`` archive's stored ``agbk_sha256`` does
    not match the freshly-computed payload hash on load.

    Distinct exception type so callers (GUI, CLI replay) can catch
    just integrity failures and surface them as a "file tampered or
    truncated" warning instead of a generic ``ValueError`` which could
    mean any number of things.
    """


def verify_archive(
    *,
    meta: Dict[str, Any],
    trades_csv: str,
    equity_csv: str,
    drawings_json: str,
) -> Optional[str]:
    """Re-derive the archive hash and compare with ``meta['agbk_sha256']``.

    Returns ``None`` if the archive has no stored hash (legacy
    ``.agbk`` files saved before Sprint 2.2 won't carry one and must
    continue to load best-effort). Returns the recomputed digest if
    the archive validates. Raises :class:`IntegrityError` if a stored
    hash is present but does not match.

    ``meta`` is the freshly loaded archive's meta dict — the function
    strips ``agbk_sha256`` from it internally before re-hashing so the
    caller doesn't have to (and so we can't accidentally include the
    digest in its own preimage).
    """
    stored = meta.get("agbk_sha256")
    if not stored:
        return None
    meta_for_hash = {k: v for k, v in meta.items() if k != "agbk_sha256"}
    actual = hash_archive_payload(
        meta=meta_for_hash,
        trades_csv=trades_csv,
        equity_csv=equity_csv,
        drawings_json=drawings_json,
    )
    if stored != actual:
        raise IntegrityError(
            f"AGBK archive integrity check failed: stored hash {stored!r} "
            f"does not match recomputed hash {actual!r}. The file may have "
            "been edited or truncated since it was saved."
        )
    return actual


__all__ = [
    "IntegrityError",
    "hash_archive_payload",
    "hash_file",
    "hash_meta_subset",
    "verify_archive",
]
