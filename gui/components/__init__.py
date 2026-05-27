"""Sprint 4 reusable Qt components.

Currently exposes:

- :class:`gui.components.toast.Toast`,
  :class:`gui.components.toast.ToastManager` — FluentUI-style
  notification toasts.
- :mod:`gui.components.animations` — fade / cross-fade helpers.
- :class:`gui.components.error_boundary.ErrorBoundary` — tab crash fallback.
"""

from gui.components.animations import (
    HOVER_FADE_MS,
    TAB_CROSS_FADE_MS,
    HoverFader,
    cross_fade,
    fade_in,
    fade_out,
)
from gui.components.error_boundary import ErrorBoundary
from gui.components.toast import Toast, ToastLevel, ToastManager

__all__ = [
    "ErrorBoundary",
    "HOVER_FADE_MS",
    "HoverFader",
    "TAB_CROSS_FADE_MS",
    "Toast",
    "ToastLevel",
    "ToastManager",
    "cross_fade",
    "fade_in",
    "fade_out",
]
