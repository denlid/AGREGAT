"""ThemeService — Sprint 2.8 skeleton.

Single façade for theme-aware GUI code. Today it wraps the
existing :mod:`gui.theme` module (palette dict + broadcast signal);
Sprints 3–4 will migrate every call site of
``apply_theme`` / ``register_theme_listener`` to go through this class
and forbid inline ``setStyleSheet`` colour literals.

The skeleton intentionally stays small:

* ``ThemeService.color(role)`` — palette lookup.
* ``ThemeService.set_theme(name)`` — switch palette + broadcast.
* ``ThemeService.qss(name)`` — load the overlay stylesheet.
* ``ThemeService.available_themes()`` — what shows in the Settings dropdown.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar, Iterable, Optional

logger = logging.getLogger("StrategyTester.gui.themes")

_QSS_DIR = Path(__file__).resolve().parent


class ThemeService:
    """Stateless wrapper around :mod:`gui.theme` — Sprint 2.8 skeleton.

    Methods are class-level because there's nothing per-instance worth
    storing today; we'll promote to a real singleton (with a Qt
    parent) in Sprint 3 when the AI-Writer GUI lands and needs to
    parameterise themes per session.
    """

    # Sprint 2.8 ships these three out of the box. ``available_themes``
    # discovers any extras dropped under ``gui/themes/<name>.qss`` so
    # custom themes don't require code changes.
    SHIPPED_THEMES: ClassVar[tuple[str, ...]] = ("dark", "light", "quant")

    @classmethod
    def color(cls, role: str, theme: Optional[str] = None) -> str:
        """Return the palette colour for ``role`` in ``theme``.

        Defaults to the currently active theme when ``theme=None``.
        Raises ``KeyError`` for unknown roles so call sites get a
        loud failure instead of a silent black-on-black bug.
        """
        from gui.styling.theme import THEMES, get_theme

        if theme is None:
            return get_theme()[role]
        return THEMES[theme][role]

    @classmethod
    def set_theme(cls, name: str, app: Optional[Any] = None) -> None:
        """Activate ``name``. ``app`` is the running ``QApplication`` —
        if omitted we fetch ``QApplication.instance()``.

        Wraps :func:`gui.theme.apply_theme` so today the cut-over is
        a no-op; future versions will route palette changes through
        the service and may emit additional structured-logging events.
        """
        from gui.styling.theme import THEMES, apply_theme

        if name not in THEMES:
            raise ValueError(f"Unknown theme {name!r}. Available: {tuple(THEMES.keys())!r}")
        if app is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
        apply_theme(app, name)

    @classmethod
    def qss(cls, name: str) -> str:
        """Return the overlay QSS for ``name`` or ``""`` if absent.

        Files live under ``gui/themes/<name>.qss``. Reads from disk
        so Sprint 3+ can hot-swap stylesheets without re-importing.
        """
        path = _QSS_DIR / f"{name}.qss"
        if not path.is_file():
            logger.debug("ThemeService.qss(%r): no overlay file at %s", name, path)
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("ThemeService.qss(%r): failed to read %s", name, path)
            return ""

    @classmethod
    def available_themes(cls) -> tuple[str, ...]:
        """Return every theme that has both a palette entry *and* a
        stylesheet overlay on disk.

        The Settings dropdown wires straight to this list so dropping
        a new ``custom.qss`` next to a ``THEMES["custom"]`` palette
        is enough to register it.
        """
        from gui.styling.theme import THEMES

        on_disk = {p.stem for p in _QSS_DIR.glob("*.qss")}
        # Preserve a stable order so the dropdown doesn't shuffle on
        # python version churn.
        ordered: list[str] = []
        for shipped in cls.SHIPPED_THEMES:
            if shipped in on_disk and shipped in THEMES:
                ordered.append(shipped)
        # Append any later additions deterministically.
        for extra in sorted(on_disk - set(cls.SHIPPED_THEMES)):
            if extra in THEMES:
                ordered.append(extra)
        return tuple(ordered)

    @classmethod
    def palette(cls, theme: Optional[str] = None) -> dict[str, str]:
        """Return the full palette dict for ``theme`` (or the active one)."""
        from gui.styling.theme import THEMES, get_theme

        if theme is None:
            return dict(get_theme())
        return dict(THEMES[theme])

    @classmethod
    def roles(cls, theme: Optional[str] = None) -> Iterable[str]:
        """Iterate over every role name in ``theme`` — used by the
        forthcoming theme-editor GUI in Sprint 4.
        """
        return iter(cls.palette(theme).keys())


__all__ = ["ThemeService"]
