"""Custom pyqtgraph items used by the chart tab — candles and filled ROIs."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPicture


class CandlestickItem(pg.GraphicsObject):
    """High-performance candlestick renderer.

    Redraws on every viewRange change so candle width always matches zoom level.
    """

    def __init__(self) -> None:
        super().__init__()
        self.data = []  # list of (idx, open, close, low, high)
        self._ohlc_map: dict = {}
        self.picture = QPicture()
        self.bull_color = "#26A69A"
        self.bear_color = "#EF5350"
        self._last_w = 0.35  # cached candle half-width in data coords
        self._view = None

    def set_view(self, vb) -> None:  # type: ignore[no-untyped-def]
        """Link to ViewBox so we can query current x-range for dynamic width."""
        self._view = vb
        vb.sigRangeChanged.connect(self._on_range_changed)

    def _on_range_changed(self, vb, ranges) -> None:  # type: ignore[no-untyped-def]
        if self.data:
            self._update_width()
            self._gen()
            self.update()

    def _update_width(self) -> None:
        """Compute candle half-width as ~40% of the per-candle pixel slot."""
        if self._view is None or not self.data:
            self._last_w = 0.35
            return
        xr = self._view.viewRange()[0]
        visible = max(1, xr[1] - xr[0])
        self._last_w = max(0.05, min(0.45, 0.40))
        if visible < 20:
            self._last_w = 0.42
        elif visible < 60:
            self._last_w = 0.40
        elif visible < 150:
            self._last_w = 0.38
        else:
            self._last_w = 0.35

    def set_data(self, data) -> None:  # type: ignore[no-untyped-def]
        self.data = data
        self._ohlc_map = {int(round(t)): (o, hi, lo, c) for (t, o, c, lo, hi) in data}
        self._update_width()
        self._gen()
        self.informViewBoundsChanged()
        self.update()

    def get_ohlc_at(self, idx):  # type: ignore[no-untyped-def]
        return self._ohlc_map.get(int(idx))

    def _gen(self) -> None:
        self.picture = QPicture()
        if not self.data:
            return
        p = QPainter(self.picture)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self._last_w
        bull = QColor(self.bull_color)
        bear = QColor(self.bear_color)
        bull_pen = pg.mkPen(bull, width=1)
        bear_pen = pg.mkPen(bear, width=1)
        bull_brush = pg.mkBrush(bull)
        bear_brush = pg.mkBrush(bear)

        for t, o, c, lo, hi in self.data:
            is_bull = c >= o
            col_pen = bull_pen if is_bull else bear_pen
            col_brush = bull_brush if is_bull else bear_brush

            # Wick
            p.setPen(col_pen)
            p.setBrush(pg.mkBrush(None))
            p.drawLine(QPointF(t, lo), QPointF(t, hi))

            # Body
            bh = abs(c - o)
            if bh < 1e-9:
                # Doji
                p.setPen(pg.mkPen(QColor(200, 200, 200), width=1))
                p.drawLine(QPointF(t - w, o), QPointF(t + w, o))
            else:
                by = min(o, c)
                p.setPen(col_pen)
                p.setBrush(col_brush)
                p.drawRect(QRectF(t - w, by, w * 2, bh))
        p.end()

    def set_colors(self, bull, bear) -> None:  # type: ignore[no-untyped-def]
        self.bull_color = bull
        self.bear_color = bear
        if self.data:
            self._gen()
            self.update()

    def paint(self, p, *args) -> None:  # type: ignore[no-untyped-def]
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):  # type: ignore[no-untyped-def]
        return QRectF(self.picture.boundingRect())


class FilledRectROI(pg.RectROI):
    """RectROI that paints a filled brush behind the standard handles."""

    def __init__(self, *args, **kwargs) -> None:
        self.brush = kwargs.pop("brush", None)
        super().__init__(*args, **kwargs)

    def paint(self, p, opt, widget) -> None:  # type: ignore[no-untyped-def]
        if self.brush:
            p.setBrush(self.brush)
            p.setPen(pg.mkPen(None))
            p.drawRect(self.boundingRect())
        super().paint(p, opt, widget)


__all__ = ["CandlestickItem", "FilledRectROI"]
