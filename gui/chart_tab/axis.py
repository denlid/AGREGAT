"""Time axis with adaptive tick density."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt


class TimeAxisItem(pg.AxisItem):
    """Time-axis that renders pandas timestamps via integer-index lookup.

    The candle picture uses bar indices on x; this axis caches the parallel
    `times` vector and looks up the date/time string per tick.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._times = np.array([])
        self.tz_offset = 0
        self.setStyle(tickLength=-6, showValues=True)
        self.setHeight(36)

    # --- Convention: drag flips primary/secondary mouse buttons ---
    def mouseDragEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if hasattr(ev, "button"):
            btn = ev.button()
            if btn == Qt.MouseButton.LeftButton:
                ev.button = lambda: Qt.MouseButton.RightButton
            elif btn == Qt.MouseButton.RightButton:
                ev.button = lambda: Qt.MouseButton.LeftButton
        super().mouseDragEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.MouseButton.LeftButton and self.linkedView():
            self.linkedView().enableAutoRange(x=True)
            ev.accept()
        else:
            super().mouseDoubleClickEvent(ev)

    def set_times(self, times) -> None:  # type: ignore[no-untyped-def]
        self._times = times

    def tickSpacing(self, minVal, maxVal, size):  # type: ignore[no-untyped-def]
        rng = max(1, maxVal - minVal)
        ideal = rng / 8.0
        nices = [1, 2, 5, 10, 15, 20, 30, 60, 120, 240, 480, 960]
        spacing = nices[-1]
        for n in nices:
            if n >= ideal:
                spacing = n
                break
        return [(spacing, 0)]

    def tickStrings(self, values, scale, spacing):  # type: ignore[no-untyped-def]
        result = []
        prev_date = None
        for v in values:
            idx = int(round(v))
            if 0 <= idx < len(self._times):
                try:
                    t = pd.Timestamp(self._times[idx])
                    if self.tz_offset != 0:
                        t = t + pd.Timedelta(hours=self.tz_offset)
                    d = t.date()
                    if prev_date is None or d != prev_date:
                        result.append(t.strftime("%d %b  %H:%M"))
                        prev_date = d
                    else:
                        result.append(t.strftime("%H:%M"))
                except Exception:
                    result.append("")
            else:
                result.append("")
        return result


__all__ = ["TimeAxisItem"]
