"""Theming package — Sprint 2.8 skeleton.

Exposes :class:`ThemeService`, the eventual single source of truth for
"what colour should this widget paint?" The current GUI still reads
:data:`gui.theme.THEMES` directly (see ``gui/style_helpers.py``); the
roadmap calls for a phased migration to ``ThemeService.color(role)``
across Sprints 3–4.

The ``light.qss`` / ``dark.qss`` / ``quant.qss`` siblings in this
directory are *overlay* stylesheets: structural tweaks (radii,
padding) that complement the palette computed by
:func:`gui.theme.apply_theme`. ``ThemeService.qss(name)`` returns
their text content; an empty string is returned if the file is
missing so callers can wire it in unconditionally.
"""

from gui.themes.service import ThemeService

__all__ = ["ThemeService"]
