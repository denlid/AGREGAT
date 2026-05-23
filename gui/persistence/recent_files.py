"""Phase 2.6 (C9) — recent-files persistence (LRU, capped at 8).

Backs the GUI's "Recent" sub-menu by tracking up to ``MAX_ENTRIES`` of the
most recently loaded data files. Stored alongside the rest of the user
config at ``~/.agregat/recent_files.json`` to keep the schema isolated
from :pymod:`gui.persistence.settings_persist` (so adding fields here
doesn't migrate the main settings dict).

The on-disk schema is intentionally trivial — a single JSON list of
absolute paths in MRU order — so it's robust to partial writes and easy
to inspect by hand.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List

logger = logging.getLogger("StrategyTester.recent_files")

MAX_ENTRIES = 8


def config_path() -> str:
    """Path to the recent-files JSON. Sibling of ``config.json``."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".agregat", "recent_files.json")


def load() -> List[str]:
    """Return the persisted MRU file list (oldest entries pruned).

    Silent on missing or malformed files — callers treat ``[]`` as
    "no history yet". We also drop entries that no longer exist on
    disk so the menu only ever shows clickable items.
    """
    path = config_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Could not read %s: %r; treating as empty.", path, e)
        return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, str) and os.path.exists(p)][:MAX_ENTRIES]


def push(filepath: str) -> List[str]:
    """Insert ``filepath`` at the head of the MRU list and persist.

    De-duplicates so the same file never appears twice. Returns the
    updated list so callers can refresh menus without an extra disk
    read. Failure to write is non-fatal (logged at DEBUG).
    """
    if not filepath:
        return load()
    abspath = os.path.abspath(filepath)
    current = load()
    # Remove any existing entry (case-insensitive on Windows; we keep
    # the original casing of the *new* entry on collision).
    norm = abspath.lower() if os.name == "nt" else abspath
    deduped = [p for p in current if (p.lower() if os.name == "nt" else p) != norm]
    updated = [abspath] + deduped
    updated = updated[:MAX_ENTRIES]
    _save(updated)
    return updated


def clear() -> None:
    """Empty the recent-files list. Used by Settings → 'Clear recent'."""
    _save([])


def _save(entries: List[str]) -> None:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.debug("Could not write %s: %r", path, e)
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


__all__ = ["MAX_ENTRIES", "config_path", "load", "push", "clear"]
