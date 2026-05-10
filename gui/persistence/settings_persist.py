"""
GUI settings persistence — `saves/settings.json` (project-local).

Stores user preferences that survive across sessions:
    theme, chart_scheme, font_size, initial_equity, risk_pct, max_lots_per_100k,
    timezone, render_window, batch_size, log_level, fast_mode,
    auto_save_logs, max_log_lines, show_grid, crosshair, show_volume,
    last_asset, exec profile + cost overrides.

The file is rewritten *atomically* (write tmp → rename) so a crash mid-save
never leaves a half-written config behind.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("StrategyTester.settings_persist")


def config_path() -> str:
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(app_dir, "saves", "settings.json")


# Defaults are intentionally identical to the Settings UI's reset values so
# `load()` on a fresh install reproduces the legacy out-of-the-box behaviour.
DEFAULTS: Dict[str, Any] = {
    "theme": "dark",
    "chart_scheme": "Classic",
    "font_size": 11,
    "initial_equity": 100_000,
    "risk_pct": 1.0,
    "max_lots_per_100k": 12,
    "timezone": 0,
    "render_window": 300,
    "cache_size": 1000,
    "batch_size": 5000,
    "log_level": "INFO",
    "fast_mode": False,
    "auto_save_logs": True,
    "max_log_lines": 5000,
    "show_grid": True,
    "crosshair": True,
    "show_volume": False,
    "last_asset": "DE40",
    "exec_profile": "Optimistic",
    "exec_spread_points": None,
    "exec_commission_per_lot": None,
    "exec_slippage_points": None,
    "ai_provider": "OpenAI (GPT-4o)",
    "ai_api_key": "",
}


def load() -> Dict[str, Any]:
    """Return the persisted settings, falling back to DEFAULTS on missing/broken file."""
    path = config_path()
    if not os.path.exists(path):
        return dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read {path}: {e!r}; using defaults.")
        return dict(DEFAULTS)


def save(settings: Dict[str, Any]) -> None:
    """Atomically write settings to the project-local saves/settings.json.

    IMPORTANT: preserve unrelated keys in the shared settings file.
    """
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    existing: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    payload = dict(existing)
    payload.update({k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS})
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"Could not write {path}: {e!r}")
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


__all__ = ["load", "save", "DEFAULTS", "config_path"]
