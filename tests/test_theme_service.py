"""Sprint 2.8 — ThemeService skeleton tests.

Asserts the public surface of :class:`gui.themes.ThemeService` —
palette lookup, QSS overlay loading, theme discovery, and the
existence of all three shipped themes (``dark``, ``light``, ``quant``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from gui.themes import ThemeService  # noqa: E402

THEMES_DIR = Path(__file__).resolve().parent.parent / "gui" / "themes"


def test_three_shipped_themes_have_qss_files() -> None:
    """Sprint 2.8 DoD: light/dark/quant themes selectable."""
    for name in ThemeService.SHIPPED_THEMES:
        assert (THEMES_DIR / f"{name}.qss").is_file(), name


def test_available_themes_lists_all_three() -> None:
    available = ThemeService.available_themes()
    assert "dark" in available
    assert "light" in available
    assert "quant" in available


def test_color_lookup_returns_hex_string() -> None:
    """``color(role)`` returns the palette entry for the active theme."""
    bg = ThemeService.color("bg", theme="dark")
    assert bg.startswith("#") and len(bg) == 7
    accent = ThemeService.color("accent", theme="quant")
    assert accent.startswith("#")


def test_color_lookup_raises_for_unknown_role() -> None:
    with pytest.raises(KeyError):
        ThemeService.color("totally_made_up_role", theme="dark")


def test_qss_returns_text_for_shipped_themes() -> None:
    for name in ("dark", "light", "quant"):
        text = ThemeService.qss(name)
        assert text  # non-empty
        assert "QToolTip" in text


def test_qss_returns_empty_for_missing_theme() -> None:
    assert ThemeService.qss("does_not_exist") == ""


def test_palette_round_trip() -> None:
    pal = ThemeService.palette("light")
    # Sanity: every role required by gui.theme.apply_theme must exist.
    for role in ("bg", "surface", "text", "accent", "border", "chart_grid"):
        assert role in pal


def test_set_theme_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown theme"):
        ThemeService.set_theme("nope")
