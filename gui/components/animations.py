"""Sprint 4.3 — Reusable Qt animation helpers.

Tiny, dependency-free wrappers around ``QPropertyAnimation`` and
``QGraphicsOpacityEffect`` so callers don't have to repeat the same
``effect = QGraphicsOpacityEffect(...); anim = QPropertyAnimation(effect, ...)``
incantation every time they want to fade a widget in or out.

The helpers are intentionally **minimal**: they return the
``QPropertyAnimation`` object so the caller can connect to ``finished``
or chain animations. They never start the animation themselves —
explicit ``start()`` keeps deterministic test behaviour.

Public API
----------
:func:`fade_in`
    Fade a widget from 0.0 to 1.0 opacity.
:func:`fade_out`
    Fade a widget from 1.0 to 0.0 opacity.
:func:`cross_fade`
    Fade ``out_widget`` away while ``in_widget`` fades in.
:class:`HoverFader`
    Attach to a widget to give it a 120 ms opacity bump on hover.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# Roadmap-specified durations (ms).
TAB_CROSS_FADE_MS = 180
HOVER_FADE_MS = 120
EQUITY_SMOOTH_MS = 220


def _attach_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    """Return the widget's existing ``QGraphicsOpacityEffect`` or attach one."""
    eff = widget.graphicsEffect()
    if isinstance(eff, QGraphicsOpacityEffect):
        return eff
    new_eff = QGraphicsOpacityEffect(widget)
    new_eff.setOpacity(1.0)
    widget.setGraphicsEffect(new_eff)
    return new_eff


def fade_in(
    widget: QWidget,
    duration_ms: int = TAB_CROSS_FADE_MS,
    *,
    parent: Optional[QObject] = None,
) -> QPropertyAnimation:
    """Animate ``widget``'s opacity 0.0 → 1.0 over ``duration_ms``."""
    eff = _attach_effect(widget)
    anim = QPropertyAnimation(eff, b"opacity", parent or widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    return anim


def fade_out(
    widget: QWidget,
    duration_ms: int = TAB_CROSS_FADE_MS,
    *,
    parent: Optional[QObject] = None,
) -> QPropertyAnimation:
    """Animate ``widget``'s opacity 1.0 → 0.0 over ``duration_ms``."""
    eff = _attach_effect(widget)
    anim = QPropertyAnimation(eff, b"opacity", parent or widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)
    return anim


def cross_fade(
    out_widget: QWidget,
    in_widget: QWidget,
    duration_ms: int = TAB_CROSS_FADE_MS,
) -> QParallelAnimationGroup:
    """Fade ``out_widget`` to 0 while ``in_widget`` fades to 1."""
    grp = QParallelAnimationGroup(in_widget)
    grp.addAnimation(fade_out(out_widget, duration_ms, parent=grp))
    grp.addAnimation(fade_in(in_widget, duration_ms, parent=grp))
    return grp


class HoverFader(QObject):
    """Event filter that bumps a widget's opacity on hover.

    Use ::

        self._hover = HoverFader(button, hover=1.0, idle=0.85)

    The fader installs itself on ``widget`` and keeps the widget alive
    via Qt's parent ownership.
    """

    def __init__(
        self,
        widget: QWidget,
        *,
        hover: float = 1.0,
        idle: float = 0.85,
        duration_ms: int = HOVER_FADE_MS,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._hover = hover
        self._idle = idle
        self._duration = duration_ms
        self._effect = _attach_effect(widget)
        self._effect.setOpacity(idle)
        widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        widget.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: D401
        if obj is self._widget:
            t = event.type()
            if t == QEvent.Type.HoverEnter or t == QEvent.Type.Enter:
                self._animate_to(self._hover)
            elif t == QEvent.Type.HoverLeave or t == QEvent.Type.Leave:
                self._animate_to(self._idle)
        return super().eventFilter(obj, event)

    def _animate_to(self, target: float) -> None:
        anim = QPropertyAnimation(self._effect, b"opacity", self)
        anim.setDuration(self._duration)
        anim.setStartValue(self._effect.opacity())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


__all__ = [
    "TAB_CROSS_FADE_MS",
    "HOVER_FADE_MS",
    "EQUITY_SMOOTH_MS",
    "HoverFader",
    "cross_fade",
    "fade_in",
    "fade_out",
]
