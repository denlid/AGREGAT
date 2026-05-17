"""Mini equity sparkline widget for the status bar.

A lightweight QWidget that draws a single-line price/equity curve. No external
deps beyond PySide6 — kept tiny so it fits in the status bar (typically
140×22 px). Updated incrementally via `set_values()` and tinted green/red
based on net delta.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class SparklineWidget(QWidget):
    """Renders a tiny line chart of recent equity values."""

    def __init__(self, width: int = 140, height: int = 22, parent: QWidget | None = None):
        super().__init__(parent)
        self._w = int(width)
        self._h = int(height)
        self.setFixedSize(self._w, self._h)
        self._values: list[float] = []
        self._green = QColor("#26A69A")
        self._red = QColor("#EF5350")
        self._neutral = QColor("#5A5A6E")

    def sizeHint(self) -> QSize:  # noqa: D401 — Qt API
        return QSize(self._w, self._h)

    def set_values(self, values: Sequence[float]) -> None:
        """Replace the displayed values. ``values`` may be empty."""
        self._values = [float(v) for v in values if v is not None]
        self.update()

    def clear(self) -> None:
        self._values = []
        self.update()

    def paintEvent(self, _ev) -> None:  # type: ignore[override]
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            n = len(self._values)
            if n < 2:
                p.setPen(QPen(self._neutral, 1))
                p.drawLine(0, self._h // 2, self._w, self._h // 2)
                return
            v0 = self._values[0]
            vN = self._values[-1]
            vmin = min(self._values)
            vmax = max(self._values)
            rng = vmax - vmin if vmax > vmin else 1.0
            color = self._green if vN >= v0 else self._red

            pad_y = 2
            inner_h = self._h - 2 * pad_y
            poly = QPolygonF()
            for i, v in enumerate(self._values):
                x = (i / (n - 1)) * (self._w - 1)
                y = self._h - pad_y - ((v - vmin) / rng) * inner_h
                poly.append(QPointF(x, y))

            pen = QPen(color, 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(poly)
        finally:
            p.end()
