"""Extracted controller namespace.

Controller modules are imported lazily so importing one controller does not
eagerly pull in the whole GUI/engine stack.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "AssetController",
    "DrawingController",
    "MenuController",
    "ModeController",
    "ReplayController",
    "SessionLifecycleController",
    "SessionStateController",
    "SettingsController",
    "StatusBarController",
    "ThemeController",
    "ToolbarController",
]

_MODULE_BY_NAME = {
    "AssetController": "asset_controller",
    "DrawingController": "drawing_controller",
    "MenuController": "menu_controller",
    "ModeController": "mode_controller",
    "ReplayController": "replay_controller",
    "SessionLifecycleController": "session_lifecycle_controller",
    "SessionStateController": "session_state_controller",
    "SettingsController": "settings_controller",
    "StatusBarController": "status_bar_controller",
    "ThemeController": "theme_controller",
    "ToolbarController": "toolbar_controller",
}


def __getattr__(name: str) -> Any:
    if name not in _MODULE_BY_NAME:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{_MODULE_BY_NAME[name]}")
    value = getattr(module, name)
    globals()[name] = value
    return value
