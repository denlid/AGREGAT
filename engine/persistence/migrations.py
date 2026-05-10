"""Sprint 3.3 — Format-version migration framework for ``.agbk`` archives.

When the on-disk layout of ``meta.json`` or the data members changes we
bump ``AGBK_FORMAT_VERSION`` in :mod:`engine.persistence`. Old archives
that don't carry the new fields need a migration step so the load path
doesn't crash or silently drop data.

This module provides:

* A **migration registry** — each migration is a simple function that
  takes a meta dict + the archive's raw member bytes and returns the
  updated meta dict.  Migrations are registered by the version they
  *upgrade from* (e.g. ``migrate_v0_to_v1`` upgrades a legacy archive
  with no ``format_version`` to version 1).
* :func:`run_migrations` — given a ``meta`` dict at any version, applies
  every registered migration in order until the meta is at
  ``AGBK_FORMAT_VERSION``. Returns ``(updated_meta, was_migrated)``.

The framework is intentionally minimal — just a dict of callables — so
adding a new version in a future sprint is a one-function commit.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Tuple

logger = logging.getLogger("StrategyTester.engine.migrations")

# Current target version — must match ``engine.persistence.AGBK_FORMAT_VERSION``.
LATEST_FORMAT_VERSION = 1

# Type alias for a migration function.
MigrationFn = Callable[[Dict[str, Any]], Dict[str, Any]]

# Registry: ``{from_version: migration_fn}``.
_MIGRATIONS: Dict[int, MigrationFn] = {}


def _register(from_version: int) -> Callable[[MigrationFn], MigrationFn]:
    """Decorator that registers a migration function."""

    def decorator(fn: MigrationFn) -> MigrationFn:
        _MIGRATIONS[from_version] = fn
        return fn

    return decorator


# -----------------------------------------------------------------------
# v0 → v1 migration
# -----------------------------------------------------------------------
@_register(0)
def migrate_v0_to_v1(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade a legacy ``.agbk`` (no ``format_version``) to v1.

    Legacy archives written before Sprint 2 have ``version: 1`` but no
    ``format_version``, ``agbk_sha256``, ``input_csv_sha256``, or
    ``seeds``.  This migration stamps the version and fills in safe
    defaults so the rest of the load pipeline can assume v1 shape.
    """
    meta.setdefault("format_version", 1)
    meta.setdefault("created_at", meta.get("saved_at", ""))
    # We can't retroactively compute ``agbk_sha256`` because we don't
    # know what the original data members looked like before the caller
    # re-serialised them. Leave it absent — the integrity check in
    # ``load_session`` already handles the "no hash" case with a
    # warning.
    # ``seeds`` — legacy runs had no seed tracking.
    meta.setdefault("seeds", {})
    logger.info("Migrated .agbk from v0 (legacy, no format_version) to v1.")
    return meta


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------
def run_migrations(meta: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Bring ``meta`` up to ``LATEST_FORMAT_VERSION``.

    Returns ``(updated_meta, was_migrated)`` where ``was_migrated`` is
    ``True`` if at least one migration was applied (the caller should
    re-save the archive to persist the upgrade).
    """
    current = meta.get("format_version", 0)
    if current >= LATEST_FORMAT_VERSION:
        return meta, False

    migrated = False
    while current < LATEST_FORMAT_VERSION:
        fn = _MIGRATIONS.get(current)
        if fn is None:
            logger.warning(
                "No migration registered for format_version=%d → %d; stopping at %d.",
                current,
                current + 1,
                current,
            )
            break
        meta = fn(meta)
        current = meta.get("format_version", current + 1)
        migrated = True

    return meta, migrated
