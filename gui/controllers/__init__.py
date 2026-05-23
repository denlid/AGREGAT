"""Sprint 3.5 — extracted controllers from ``main_window.py``.

Each controller owns a single area of concern (SRP) and operates on
the host :class:`~gui.main_window.MainWindow` passed at construction
time. The host exposes its widgets and engine via public attributes.
"""

from gui.controllers.menu_controller import MenuController
from gui.controllers.session_lifecycle_controller import SessionLifecycleController
from gui.controllers.theme_controller import ThemeController
from gui.controllers.toolbar_controller import ToolbarController

__all__ = [
    "MenuController",
    "ToolbarController",
    "SessionLifecycleController",
    "ThemeController",
]
