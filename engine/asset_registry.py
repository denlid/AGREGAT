"""
Asset registry — single source of truth for ``config/assets.json``.

Both :class:`engine.backtest_engine.BacktestEngine` and the GUI need to know
which assets are available and read their parameters. Centralising the logic
here keeps the engine free of GUI hardcoded lists and lets the file be reloaded
on demand (e.g. after the user adds a new symbol).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("StrategyTester.engine.asset_registry")

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "assets.json",
)


def default_config_path() -> str:
    """Return the absolute path to the bundled ``config/assets.json``."""
    return _DEFAULT_PATH


def load_assets(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Read and parse the assets config. Returns ``{}`` on error.

    The returned dict is keyed by asset name (e.g. ``"DE40"``) and the values
    are the raw per-asset config dicts. A missing or malformed file is logged
    and yields an empty dict — callers should treat that as "no assets".
    """
    p = path or _DEFAULT_PATH
    if not os.path.exists(p):
        logger.warning("Asset config not found: %s", p)
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to read assets config %s: %s", p, e)
        return {}
    if not isinstance(data, dict):
        logger.error("Asset config %s did not contain a JSON object", p)
        return {}
    return data


def list_asset_names(path: Optional[str] = None) -> List[str]:
    """Return the sorted list of asset names defined in ``assets.json``.

    Sorting is alphabetical, case-insensitive, so the GUI ``QComboBox`` always
    presents the same ordering across runs (and across platforms with
    different default JSON ordering).
    """
    return sorted(load_assets(path).keys(), key=lambda s: s.lower())


def get_asset(name: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the raw per-asset config dict, or ``None`` if not present."""
    return load_assets(path).get(name)


def save_asset(
    name: str,
    config: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> None:
    """Persist a (possibly user-defined) asset to ``assets.json``.

    Used by the "Custom asset…" dialog to remember user-added symbols across
    sessions. Existing assets are overwritten on a name collision. The file is
    rewritten atomically — write to a sibling ``.tmp`` then ``os.replace``.
    """
    p = path or _DEFAULT_PATH
    data = load_assets(p)
    data[name] = dict(config)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
    logger.info("Saved custom asset %s to %s", name, p)


def detect_asset_from_filename(
    filepath: str,
    *,
    path: Optional[str] = None,
) -> Optional[str]:
    """Phase 2.4 (C4) — guess the asset name from a CSV filename.

    Tries to match a registered asset name against the filename's stem
    (case-insensitive, longest-prefix wins so ``"XAUUSD"`` beats ``"XAU"``
    on ``"XAUUSD_M1.csv"``). Returns the canonical asset name as it
    appears in :func:`list_asset_names`, or ``None`` if no asset
    matches.

    The detection is deliberately conservative: only registered assets
    are returned, so adding a new symbol to ``assets.json`` is enough to
    teach the GUI to recognise it. Callers are expected to keep the
    user's manual selection if this returns ``None``.
    """
    if not filepath:
        return None
    stem = os.path.basename(filepath)
    # Strip everything after the first non-alphanumeric run so e.g.
    # "EURUSD_1m.csv" / "EURUSD-2024.csv" / "EURUSD.parquet" all reduce
    # to "EURUSD" before matching. Underscores are explicit dataset
    # separators in MT5 dumps, so we always split on them too.
    head = stem.split(".", 1)[0]
    head = head.replace("-", "_").upper()
    candidates = [head, head.split("_", 1)[0]]
    names = list_asset_names(path)
    if not names:
        return None
    upper_names = {n.upper(): n for n in names}
    # Longest prefix wins so XAUUSD beats XAU on "XAUUSD_M1".
    for cand in candidates:
        # Try exact match first, then prefix match.
        if cand in upper_names:
            return upper_names[cand]
        prefix_hits = [n for n_up, n in upper_names.items() if cand.startswith(n_up)]
        if prefix_hits:
            return max(prefix_hits, key=len)
    return None


__all__ = [
    "default_config_path",
    "load_assets",
    "list_asset_names",
    "get_asset",
    "save_asset",
    "detect_asset_from_filename",
]
