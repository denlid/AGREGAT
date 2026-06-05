"""
GUI settings persistence — `saves/settings.json` (project-local).

Stores user preferences that survive across sessions.

The file is rewritten *atomically* (write tmp → rename) so a crash mid-save
never leaves a half-written config behind.

All setting keys & defaults are defined in
:mod:`gui.controllers.settings_registry`.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from gui.controllers.settings_registry import ALL_KEYS, DEFAULTS

logger = logging.getLogger("StrategyTester.settings_persist")


def config_path() -> str:
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(app_dir, "saves", "settings.json")


def load() -> Dict[str, Any]:
    """Return the persisted settings, falling back to DEFAULTS on missing/broken file."""
    path = config_path()
    if not os.path.exists(path):
        return dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        # Беремо тільки ті ключі з файлу, які є в реєстрі
        merged.update({k: v for k, v in data.items() if k in ALL_KEYS})
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
    # Оновлюємо тільки ті ключі, які є в реєстрі
    payload.update({k: settings.get(k, DEFAULTS[k]) for k in ALL_KEYS})
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
