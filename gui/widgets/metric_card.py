"""
MetricCard v2 — unified, animated KPI card.

Supersedes the per-tab `MetricCard` (gui.tabs.stats_tab) and `MiniCard`
(gui.tabs.analytics_tab) implementations. Adds:

* **Inline sparkline** — pass a short sequence of historical values and
  the card paints a mini trend in the bottom strip.
* **Number tween** — the displayed value animates from the previous to
  the new value over a few hundred ms; pleasant without being noisy.
* **Threshold ring** — when ``threshold`` is set, the right side gets a
  small G/A/R pill showing where the current value sits in the band.
* **Right-click context menu** — Copy value / Copy formula / Pin to top /
  Hide card / Explain.
* **Explain dialog** — modal that shows the metric formula, current
  value, an interpretation hint and an optional reference URL.
* **Size variants** — ``size='compact'|'regular'|'large'`` lets the same
  widget power dense dashboards or hero-tile headers.

Backwards compatibility: ``update(value, color=None, sub=None)`` keeps
the old API. Migrators can drop-in replace by importing this class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import (
    apply_drop_shadow,
    font_mono,
    get_theme,
    register_theme_listener,
    unregister_theme_listener,
)
from gui.widgets.sparkline import SparklineWidget

# Match a comma/space-grouped number (at least one grouping) OR a plain
# integer/decimal. Order matters — the grouped form requires at least one
# ``,xxx``/`` xxx`` group so a bare ``1234`` falls through to the plain form
# and the entire number is captured.
_NUMERIC_RE = re.compile(
    r"-?\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?"
)


def _try_parse_number(text: str) -> Optional[float]:
    """Extract the first numeric token from a formatted KPI string.

    Recognises common prefixes (``$``, ``-``, ``+``) and thousands
    separators (``,`` or thin space). Returns ``None`` if no number can
    be parsed.
    """
    if not text:
        return None
    m = _NUMERIC_RE.search(text)
    if m is None:
        return None
    tok = m.group(0).replace(",", "").replace(" ", "")
    # Pick up a leading sign that the regex skipped over a currency prefix.
    # e.g. ``"-$1,234.50"`` matches ``"1,234.50"`` and we apply the ``-`` here.
    if not tok.startswith(("-", "+")):
        start = m.start()
        for i in range(start - 1, -1, -1):
            ch = text[i]
            if ch in ("-", "+"):
                tok = ch + tok
                break
            if ch.isdigit() or ch in ".," or not (ch.isspace() or ch in "$€£¥"):
                break
    try:
        return float(tok)
    except ValueError:
        return None


@dataclass(frozen=True)
class Threshold:
    """G/A/R thresholds for a metric. Higher-is-better unless ``inverted``.

    Example for Sharpe (higher is better)::

        Threshold(good=1.5, amber=1.0)

    Example for Max DD % (lower is better, inverted)::

        Threshold(good=10.0, amber=20.0, inverted=True)
    """

    good: float
    amber: float
    inverted: bool = False

    def evaluate(self, value: float) -> str:
        """Return ``'green'`` / ``'amber'`` / ``'red'`` for ``value``."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "text"
        if self.inverted:
            if v <= self.good:
                return "green"
            if v <= self.amber:
                return "amber"
            return "red"
        else:
            if v >= self.good:
                return "green"
            if v >= self.amber:
                return "amber"
            return "red"


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------


_SIZES = {
    "compact": dict(min_h=58, padding=(10, 8, 10, 8), value_px=16, title_px=9),
    "regular": dict(min_h=72, padding=(12, 10, 12, 10), value_px=22, title_px=10),
    "large": dict(min_h=110, padding=(16, 14, 16, 14), value_px=32, title_px=11),
}


class MetricCard(QFrame):
    """Single-metric KPI card with sparkline, tween, threshold pill.

    Emits :pyattr:`clicked` and :pyattr:`drilldown_requested` so containers
    can wire navigation (e.g. clicking the Sharpe card opens the rolling
    Sharpe chart).
    """

    clicked = Signal()
    drilldown_requested = Signal()
    pin_toggled = Signal(bool)
    hide_requested = Signal()
    TWEEN_DURATION_MS = 280
    FADE_DURATION_MS = 200

    def __init__(
        self,
        title: str,
        value: str = "—",
        color: str = "text",
        sub: str = "",
        *,
        size: str = "regular",
        threshold: Optional[Threshold] = None,
        unit: str = "",
        tooltip: str = "",
        formula: str = "",
        explanation: str = "",
        reference: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self._size_key = size if size in _SIZES else "regular"
        spec = _SIZES[self._size_key]
        self.setMinimumHeight(spec["min_h"])
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._color = color
        self._threshold = threshold
        self._unit = unit
        self._formula = formula
        self._explanation = explanation
        self._reference = reference
        self._pinned: bool = False
        self._raw_value: Optional[float] = None
        self._tween_value: float = 0.0
        self._tween_anim: Optional[QPropertyAnimation] = None
        self._fade_anim: Optional[QPropertyAnimation] = None
        if tooltip:
            self.setToolTip(tooltip)

        # Layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*spec["padding"])
        outer.setSpacing(3)

        # Title row (title + pill on the right)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self._title_lbl = QLabel(title.upper())
        title_row.addWidget(self._title_lbl, 1)
        # Confidence badge (Phase 2 EPIC 2.4) — small "?" next to the title
        # when the metric was computed on a sub-threshold sample.
        self._confidence_lbl = QLabel("")
        self._confidence_lbl.setVisible(False)
        title_row.addWidget(self._confidence_lbl, 0, Qt.AlignmentFlag.AlignRight)
        self._pill_lbl = QLabel("")
        self._pill_lbl.setVisible(False)
        title_row.addWidget(self._pill_lbl, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(title_row)

        # Value
        self._value_lbl = QLabel(value)
        outer.addWidget(self._value_lbl)

        # Sub-text
        self._sub_lbl = QLabel(sub) if sub else None
        if self._sub_lbl is not None:
            outer.addWidget(self._sub_lbl)

        # Sparkline (lazy — only visible if set_sparkline called)
        self._spark = SparklineWidget(width=spec["min_h"] * 2, height=14, parent=self)
        self._spark.setVisible(False)
        outer.addWidget(self._spark)

        self._spec = spec
        self._apply_theme()
        register_theme_listener(self._apply_theme)
        apply_drop_shadow(self, blur=14, y_offset=2, alpha=70)
        self._theme_registered = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, e):  # type: ignore[override]
        self._teardown_theme()
        super().closeEvent(e)

    def destroy(self, destroyWindow=True, destroySubWindows=True):  # type: ignore[override]
        self._teardown_theme()
        super().destroy(destroyWindow, destroySubWindows)

    def _teardown_theme(self) -> None:
        if getattr(self, "_theme_registered", False):
            unregister_theme_listener(self._apply_theme)
            self._theme_registered = False

    def _flash_border(self, flash_color: str = "#4CAF50") -> None:
        """Briefly flash the card border, then fade back to normal."""
        t = get_theme()
        flash = (
            "QFrame#metricCard {"
            f"  background: {sh.c(t, 'surface')};"
            f"  border: 2px solid {flash_color};"
            "  border-radius: 10px;"
            "}"
            "QFrame#metricCard:hover {"
            f"  border-color: {sh.c(t, 'accent')};"
            f"  background: {sh.c(t, 'elevated')};"
            "}"
        )
        self.setStyleSheet(flash)
        QTimer.singleShot(400, self._apply_theme)

    def mousePressEvent(self, ev):  # type: ignore[override]
        if ev.button() == Qt.MouseButton.LeftButton:
            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.drilldown_requested.emit()
            else:
                self.clicked.emit()
        super().mousePressEvent(ev)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_color(color: str) -> str:
        t = get_theme()
        if isinstance(color, str) and color and not color.startswith("#"):
            return sh.c(t, color)
        return color

    def _apply_theme(self) -> None:
        t = get_theme()
        spec = self._spec
        self.setStyleSheet(
            "QFrame#metricCard {"
            f"  background: {sh.c(t, 'surface')};"
            f"  border: 1px solid {sh.c(t, 'border2')};"
            "  border-radius: 10px;"
            "}"
            "QFrame#metricCard:hover {"
            f"  border-color: {sh.c(t, 'accent')};"
            f"  background: {sh.c(t, 'elevated')};"
            "}"
        )
        self._title_lbl.setStyleSheet(
            f"color: {sh.c(t, 'text3')}; font-size: {spec['title_px']}px;"
            " font-weight: 600; background: transparent; letter-spacing: 0.5px;"
        )
        resolved_color = self._resolve_color(self._color)
        self._value_lbl.setStyleSheet(
            f"color: {resolved_color}; font-size: {spec['value_px']}px;"
            f" font-weight: 600; font-family: {font_mono()};"
            " background: transparent;"
        )
        if self._sub_lbl is not None:
            self._sub_lbl.setStyleSheet(
                f"color: {sh.c(t, 'text_dis')}; font-size: 10px; background: transparent;"
            )
        # Pill restyle — color matches current threshold state if any
        if self._pill_lbl.isVisible():
            self._restyle_pill()

    def _restyle_pill(self) -> None:
        t = get_theme()
        col = self._resolve_color(self._color)
        self._pill_lbl.setStyleSheet(
            f"QLabel {{ color: {col}; border: 1px solid {col};"
            f" border-radius: 6px; padding: 1px 6px; font-size: 9px;"
            f" font-weight: 700; background: {sh.c(t, 'bg')}; }}"
        )

    # ------------------------------------------------------------------
    # Public API — value updates
    # ------------------------------------------------------------------

    def update(self, value: str, color: Optional[str] = None, sub: Optional[str] = None):  # type: ignore[override]
        """Legacy update entry-point. Sets value text directly."""
        prev_text = self._value_lbl.text()
        self._value_lbl.setText(value)
        if color:
            self._color = color
        if sub is not None and self._sub_lbl is not None:
            self._sub_lbl.setText(sub)
        self._apply_theme()
        if prev_text != value:
            flash_col = "#4CAF50" if color in ("g", "green", "gain") else "#F44336" if color in ("r", "red", "loss") else "#888888"
            self._flash_border(flash_col)

    def set_metric(
        self,
        raw_value: Optional[float],
        *,
        formatted: Optional[str] = None,
        color: Optional[str] = None,
        sub: Optional[str] = None,
        animate: bool = True,
    ) -> None:
        """Preferred entry-point for numeric metrics.

        ``raw_value`` is the canonical float; ``formatted`` is the display
        string. If a Threshold was attached at construction the colour is
        chosen automatically, otherwise an explicit ``color`` (theme key or
        hex) overrides the current color.
        """
        prev_raw = self._raw_value
        self._raw_value = raw_value

        if self._threshold is not None and raw_value is not None:
            self._color = self._threshold.evaluate(raw_value)
            self._pill_lbl.setText(self._color.upper()[:1])
            self._pill_lbl.setVisible(True)
            self._restyle_pill()
        else:
            if color is not None:
                self._color = color
            self._pill_lbl.setVisible(False)

        if formatted is None and raw_value is not None:
            formatted = f"{raw_value:,.2f}{self._unit}"
        if formatted is not None:
            self._formatted_template = formatted
        if sub is not None and self._sub_lbl is not None:
            self._sub_lbl.setText(sub)

        self._apply_theme()

        should_tween = (
            animate
            and prev_raw is not None
            and raw_value is not None
            and prev_raw != raw_value
            and abs(prev_raw) < 1e12
            and abs(raw_value) < 1e12
        )
        if should_tween:
            self._animate_to(prev_raw, raw_value, formatted)
        elif formatted is not None:
            self._value_lbl.setText(formatted)
        if animate and prev_raw is not None and prev_raw != raw_value:
            flash_col = "#4CAF50" if raw_value is not None and raw_value > prev_raw else "#F44336" if raw_value is not None and raw_value < prev_raw else "#888888"
            self._flash_border(flash_col)

    # Backwards-compatible alias used by older call-sites (e.g. ``MiniCard``).
    def set_value(self, value: str, color: Optional[str] = None, sub: Optional[str] = None) -> None:
        """Legacy alias for :meth:`set_value_text` (positional, no animate flag)."""
        self.set_value_text(value, color=color, sub=sub)

    def set_value_text(
        self,
        text: str,
        *,
        color: Optional[str] = None,
        sub: Optional[str] = None,
        animate: bool = True,
    ) -> None:
        """Update by formatted text, inferring the raw value when possible.

        Migration-friendly helper for callers that only have the formatted
        string. If a numeric token can be parsed (e.g. ``"+$1,234.50"``)
        the tween animation is used; otherwise a 200 ms fade is applied.
        """
        parsed = _try_parse_number(text)
        if parsed is not None:
            self.set_metric(
                parsed,
                formatted=text,
                color=color,
                sub=sub,
                animate=animate,
            )
            return
        # Non-numeric — e.g. "12W" streak label or "—".
        prev_text = self._value_lbl.text()
        if color is not None:
            self._color = color
        if sub is not None and self._sub_lbl is not None:
            self._sub_lbl.setText(sub)
        self._apply_theme()
        if animate and prev_text != text:
            self._fade_to(text)
        else:
            self._value_lbl.setText(text)
        if animate and prev_text != text:
            flash_col = "#888888"
            if color in ("g", "green", "gain", "profit") or (color and "green" in str(color).lower()):
                flash_col = "#4CAF50"
            elif color in ("r", "red", "loss", "danger") or (color and "red" in str(color).lower()):
                flash_col = "#F44336"
            self._flash_border(flash_col)

    # Qt property used by the tween animation.
    def _get_tween(self) -> float:
        return self._tween_value

    def _set_tween(self, v: float) -> None:
        self._tween_value = v
        tmpl = getattr(self, "_formatted_template", "")
        unit = self._unit
        rendered = self._format_tween_value(v, tmpl, unit)
        self._value_lbl.setText(rendered)

    tween = Property(float, _get_tween, _set_tween)  # type: ignore[arg-type]

    @staticmethod
    def _format_tween_value(value: float, template: str, unit: str) -> str:
        """Render an intermediate tween value using the final template.

        Tries to preserve the prefix (``$``, ``-``, ``+``) and the suffix
        (``%``, ``R``, ``M``, ``k``) from ``template``. Falls back to a
        plain comma-grouped float when no template was supplied.
        """
        if not template:
            return f"{value:,.2f}{unit}"
        match = _NUMERIC_RE.search(template)
        if match is None:
            return f"{value:,.2f}{unit}"
        prefix = template[: match.start()]
        suffix = template[match.end() :]
        # Detect decimal precision from the matched token.
        tok = match.group(0)
        if "." in tok:
            decimals = len(tok.split(".")[-1])
        else:
            decimals = 0
        # Sign handling — if template starts with ``-``/``+`` and value is
        # positive, emit the sign explicitly for symmetry.
        body = f"{abs(value):,.{decimals}f}"
        if value < 0:
            body = "-" + body
        elif prefix.startswith(("+", "-")):
            body = "+" + body
        # Strip pre-existing sign from the prefix (since we just added one).
        cleaned_prefix = prefix.lstrip("+-") if prefix.startswith(("+", "-")) else prefix
        return f"{cleaned_prefix}{body}{suffix}"

    def _animate_to(self, start: float, end: float, final_text: Optional[str]) -> None:
        anim = QPropertyAnimation(self, b"tween", self)
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setDuration(self.TWEEN_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _on_finished() -> None:
            if final_text is not None:
                self._value_lbl.setText(final_text)

        anim.finished.connect(_on_finished)
        if self._tween_anim is not None:
            try:
                self._tween_anim.stop()
            except Exception:
                pass
        self._tween_anim = anim
        anim.start()

    # ------------------------------------------------------------------
    # Fade (non-numeric transitions)
    # ------------------------------------------------------------------

    def _fade_to(self, new_text: str) -> None:
        eff = self._value_lbl.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(self._value_lbl)
            eff.setOpacity(1.0)
            self._value_lbl.setGraphicsEffect(eff)

        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(self.FADE_DURATION_MS)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def _swap() -> None:
            self._value_lbl.setText(new_text)

        # Swap text at the midpoint so the user sees the change behind the
        # fade. ``QPropertyAnimation.valueChanged`` fires at every tick;
        # we tap into it once.
        swapped: list[bool] = []

        def _on_tick(v: float) -> None:
            if not swapped and v <= 0.01:
                swapped.append(True)
                _swap()

        anim.valueChanged.connect(_on_tick)
        if self._fade_anim is not None:
            try:
                self._fade_anim.stop()
            except Exception:
                pass
        self._fade_anim = anim
        anim.start()

    # ------------------------------------------------------------------
    # Sparkline
    # ------------------------------------------------------------------

    def set_sparkline(self, values: Optional[Sequence[float]]) -> None:
        if not values:
            self._spark.clear()
            self._spark.setVisible(False)
            return
        self._spark.set_values(list(values))
        self._spark.setVisible(True)

    # ------------------------------------------------------------------
    # Metadata / pin / hide
    # ------------------------------------------------------------------

    def set_formula(
        self,
        formula: str,
        *,
        explanation: str = "",
        reference: str = "",
    ) -> None:
        """Attach formula / interpretation text used by the Explain menu."""
        self._formula = formula
        if explanation:
            self._explanation = explanation
        if reference:
            self._reference = reference

    def set_confidence(
        self,
        n_samples: int,
        *,
        minimum: int = 30,
        kind: str = "daily returns",
    ) -> None:
        """Show a "?" badge when the metric was computed on too few samples.

        Phase 2 EPIC 2.4. Pass ``n_samples=0`` (the default ``minimum`` is
        never reached) to hide the badge.
        """
        try:
            n = int(n_samples)
        except (TypeError, ValueError):
            n = 0
        if n <= 0 or n >= int(minimum):
            self._confidence_lbl.setVisible(False)
            self._confidence_lbl.setText("")
            self._confidence_lbl.setToolTip("")
            return
        self._confidence_lbl.setText("?")
        self._confidence_lbl.setVisible(True)
        self._confidence_lbl.setToolTip(
            f"Computed on {n} {kind} (< {minimum} minimum). "
            "Treat the value as indicative."
        )
        t = get_theme()
        self._confidence_lbl.setStyleSheet(
            f"QLabel {{"
            f"  color: {sh.c(t, 'amber')};"
            f"  background: transparent;"
            f"  border: 1px solid {sh.c(t, 'amber')};"
            f"  border-radius: 7px;"
            f"  padding: 0 4px;"
            f"  font-size: 9px;"
            f"  font-weight: 600;"
            f"  min-width: 8px;"
            f"}}"
        )

    def is_pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, pinned: bool) -> None:
        if pinned == self._pinned:
            return
        self._pinned = bool(pinned)
        if self._pinned:
            self._pill_lbl.setText("★")
            self._pill_lbl.setVisible(True)
            self._restyle_pill()
        else:
            self._pill_lbl.setVisible(self._threshold is not None and self._raw_value is not None)
            if self._threshold is not None and self._raw_value is not None:
                self._pill_lbl.setText(self._color.upper()[:1])
                self._restyle_pill()
        self.pin_toggled.emit(self._pinned)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)

        copy_act = QAction("Copy value", menu)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(self._copy_value)
        menu.addAction(copy_act)

        copy_csv = QAction("Copy as CSV", menu)
        copy_csv.triggered.connect(self._copy_csv)
        menu.addAction(copy_csv)

        copy_formula = QAction("Copy formula", menu)
        copy_formula.setEnabled(bool(self._formula))
        copy_formula.triggered.connect(self._copy_formula)
        menu.addAction(copy_formula)

        menu.addSeparator()

        pin_act = QAction("Unpin from top" if self._pinned else "Pin to top", menu)
        pin_act.triggered.connect(lambda: self.set_pinned(not self._pinned))
        menu.addAction(pin_act)

        hide_act = QAction("Hide card", menu)
        hide_act.triggered.connect(self.hide_requested.emit)
        menu.addAction(hide_act)

        menu.addSeparator()

        explain_act = QAction("Explain…", menu)
        explain_act.setEnabled(bool(self._formula or self._explanation))
        explain_act.triggered.connect(self._show_explain_dialog)
        menu.addAction(explain_act)

        drill = QAction("Open detail…", menu)
        drill.triggered.connect(self.drilldown_requested.emit)
        menu.addAction(drill)
        menu.exec(self.mapToGlobal(pos))

    def keyPressEvent(self, ev):  # type: ignore[override]
        if ev.matches(QKeySequence.StandardKey.Copy):
            self._copy_value()
            return
        super().keyPressEvent(ev)

    def _copy_value(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self._value_lbl.text())

    def _copy_csv(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(f"{self._title_lbl.text()},{self._value_lbl.text()}")

    def _copy_formula(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None and self._formula:
            cb.setText(self._formula)

    def _show_explain_dialog(self) -> None:
        dlg = _ExplainDialog(
            title=self._title_lbl.text(),
            value=self._value_lbl.text(),
            formula=self._formula,
            explanation=self._explanation or self.toolTip(),
            reference=self._reference,
            parent=self,
        )
        dlg.exec()


class _ExplainDialog(QDialog):
    """Simple modal that surfaces a metric's formula + interpretation."""

    def __init__(
        self,
        *,
        title: str,
        value: str,
        formula: str,
        explanation: str,
        reference: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Explain \u2014 {title}")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        header = QLabel(f"<b>{title}</b>")
        lay.addWidget(header)

        value_lbl = QLabel(f"Current value: <code>{value}</code>")
        value_lbl.setTextFormat(Qt.TextFormat.RichText)
        value_lbl.setWordWrap(True)
        lay.addWidget(value_lbl)

        if formula:
            f_lbl = QLabel(f"<b>Formula</b><br><code>{formula}</code>")
            f_lbl.setTextFormat(Qt.TextFormat.RichText)
            f_lbl.setWordWrap(True)
            f_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(f_lbl)

        if explanation:
            e_lbl = QLabel(explanation)
            e_lbl.setWordWrap(True)
            e_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(e_lbl)

        if reference:
            r_lbl = QLabel(f"<a href=\"{reference}\">{reference}</a>")
            r_lbl.setOpenExternalLinks(True)
            r_lbl.setWordWrap(True)
            lay.addWidget(r_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        # QDialogButtonBox.Close emits rejected by default; map it to accept too.
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        lay.addWidget(buttons)


__all__ = ["MetricCard", "Threshold"]
