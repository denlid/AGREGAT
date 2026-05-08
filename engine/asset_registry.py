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


__all__ = [
    "default_config_path",
    "load_assets",
    "list_asset_names",
    "get_asset",
    "save_asset",
]
