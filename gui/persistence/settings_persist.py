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
from copy import deepcopy
from typing import Any, Dict

from gui.controllers.settings_registry import (
    DEFAULTS,
    PREFERENCE_KEYS,
    PREFERENCES_DEFAULTS,
    SESSION_KEYS,
    SESSION_STATE_DEFAULTS,
)

logger = logging.getLogger("StrategyTester.settings_persist")


def config_path() -> str:
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(app_dir, "saves", "settings.json")


def _read_raw() -> Dict[str, Any]:
    path = config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read {path}: {e!r}; using defaults.")
        return {}


def _split_payload(raw: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Return ``(preferences, session, extra)`` from structured or flat JSON."""
    preferences = deepcopy(PREFERENCES_DEFAULTS)
    session = deepcopy(SESSION_STATE_DEFAULTS)
    extra: Dict[str, Any] = {}

    if isinstance(raw.get("preferences"), dict) or isinstance(raw.get("session"), dict):
        preferences.update(raw.get("preferences") or {})
        session.update(raw.get("session") or {})
        extra = {
            k: v for k, v in raw.items()
            if k not in {"preferences", "session"}
        }
    else:
        for key, value in raw.items():
            normalized_key = "chart_scheme" if key == "chart_colors" else key
            if normalized_key in PREFERENCE_KEYS:
                preferences[normalized_key] = value
            elif normalized_key in SESSION_KEYS:
                session[normalized_key] = value
            else:
                extra[key] = value

    if raw.get("chart_colors") and not raw.get("chart_scheme"):
        preferences["chart_scheme"] = raw["chart_colors"]
        session["chart_scheme"] = raw["chart_colors"]
    return preferences, session, extra


def _write_payload(payload: Dict[str, Any]) -> None:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
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


def load_all() -> Dict[str, Any]:
    """Return the structured settings payload with migrated defaults."""
    raw = _read_raw()
    preferences, session, extra = _split_payload(raw)
    return {
        **extra,
        "preferences": preferences,
        "session": session,
    }


def load_preferences() -> Dict[str, Any]:
    return deepcopy(load_all()["preferences"])


def load_session_state() -> Dict[str, Any]:
    return deepcopy(load_all()["session"])


def load() -> Dict[str, Any]:
    """Return a flat compatibility snapshot for older call sites."""
    payload = load_all()
    flat = deepcopy(DEFAULTS)
    flat.update(payload["session"])
    flat.update(payload["preferences"])
    # Legacy alias for callers still reading chart_colors.
    flat["chart_colors"] = flat.get("chart_scheme", "Classic")
    return flat


def save(settings: Dict[str, Any]) -> None:
    """Merge a flat compatibility snapshot into the structured file."""
    payload = load_all()
    preferences = dict(payload["preferences"])
    session = dict(payload["session"])
    extra = {
        k: v for k, v in payload.items()
        if k not in {"preferences", "session"}
    }
    for key, value in settings.items():
        normalized_key = "chart_scheme" if key == "chart_colors" else key
        if normalized_key in PREFERENCE_KEYS:
            preferences[normalized_key] = value
        if normalized_key in SESSION_KEYS:
            session[normalized_key] = value
        if normalized_key not in PREFERENCE_KEYS and normalized_key not in SESSION_KEYS:
            extra[key] = value
    _write_payload({
        **extra,
        "preferences": preferences,
        "session": session,
    })


def save_preferences(preferences: Dict[str, Any]) -> None:
    payload = load_all()
    merged = dict(payload["preferences"])
    for key, value in preferences.items():
        normalized_key = "chart_scheme" if key == "chart_colors" else key
        if normalized_key in PREFERENCE_KEYS:
            merged[normalized_key] = value
    payload["preferences"] = merged
    _write_payload(payload)


def save_session_state(session: Dict[str, Any]) -> None:
    payload = load_all()
    merged = dict(payload["session"])
    for key, value in session.items():
        normalized_key = "chart_scheme" if key == "chart_colors" else key
        if normalized_key in SESSION_KEYS:
            merged[normalized_key] = value
    payload["session"] = merged
    _write_payload(payload)


__all__ = [
    "load",
    "save",
    "load_all",
    "load_preferences",
    "load_session_state",
    "save_preferences",
    "save_session_state",
    "DEFAULTS",
    "config_path",
]
