"""Sprint 3.5 — Bar-replay & date-range coordination.

Owns the engine-stepping logic (single step, visual-candle step,
visual-candle step-back) plus the chart-toolbar date-range handling
(``_on_data_loaded`` initialisation, ``_on_range_changed``, full reset).
``MainWindow`` keeps thin shims (``on_step``, ``step_visual_candle``,
``step_back_visual_candle``, ``on_reset`` …) that delegate here so
existing tests / signal connections continue to work unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from PySide6.QtCore import QDate

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.replay")


# Pandas-offset strings for the timeframe-aware visual stepper.
_TF_OFFSETS = {
    "1m": None,
    "2m": "2min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "60min",
    "4h": "240min",
}
_TF_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


class ReplayController:
    """Coordinates engine stepping + chart-side date range."""

    def __init__(self, host: MainWindow) -> None:
        self._host = host

    # ------------------------------------------------------------------
    # Engine stepping
    # ------------------------------------------------------------------
    def on_reset(self) -> None:
        h = self._host
        h.bar_replay.stop_playback()
        h.engine.reset()
        # Reset doesn't emit signals; ensure UI caches are invalidated.
        try:
            cache = getattr(h.engine, "_shared_cache", None)
            if cache is not None:
                cache.invalidate()
        except Exception:
            pass
        h.trades_tab._clear()
        h.analytics_tab.refresh_all(force=True)
        h.stats_tab._recalc()
        # Quant tab was removed/refactored from MainWindow tab wiring.
        # Keep reset flow backward-compatible by only refreshing it when present.
        quant_tab = getattr(h, "quant_tab", None)
        if quant_tab is not None:
            try:
                quant_tab.refresh_all()
            except Exception:
                logger.exception("quant_tab.refresh_all failed on reset")
        h.progress.setValue(0)
        h.bar_replay._update_state()

    def on_step(self, idx: int, c: dict) -> None:
        h = self._host
        if h.engine.df is not None:
            h.progress.setValue(idx + 1)
        price = c.get("close", "-")
        if isinstance(price, float):
            price = f"{price:.2f}"
        sn = h.engine.strategy.__class__.__name__
        if sn == "_Empty":
            sn = "Browse"
        ts = ""
        t = c.get("time")
        if t is not None:
            try:
                # Fast path: t is np.datetime64 — avoid pd.to_datetime overhead
                t_ts = pd.Timestamp(t)
                ts = f"{t_ts.hour:02d}:{t_ts.minute:02d}"
            except Exception:
                pass
            try:
                from engine.models.models import get_session

                h.st_ses.setText(get_session(t))
            except Exception:
                pass
        h.lbl_info.setText(f"{ts}  {price}  #{idx}  [{sn}]")

    def step_visual_candle(self) -> None:
        import time as _time
        t_total = _time.perf_counter()

        h = self._host
        eng = h.engine
        if eng.df is None or eng.current_idx >= eng.total_bars - 1:
            return
        tf = h.chart_tab.visual_tf_cb.currentText().lower()
        pt = _TF_OFFSETS.get(tf)
        if not pt:
            eng.step()
            return
        ni = eng.current_idx + 1
        if ni >= eng.total_bars:
            return

        t0 = _time.perf_counter()
        try:
            nt = pd.Timestamp(eng._time[ni])
            be = nt.floor(pt) + pd.Timedelta(pt)
            ti = int(np.searchsorted(eng._time, np.datetime64(be), side="left"))
            steps = max(1, (ti - 1) - eng.current_idx)
        except Exception:
            steps = 1
        t_calc = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        if steps > 1:
            eng.step_n(steps)
        else:
            eng.step()
            logger.info(
                f"⏱ step_visual: calc={t_calc*1000:.1f}ms step=instant "
                f"(1m, total={(_time.perf_counter()-t_total)*1000:.1f}ms)"
            )
            return
        t_step = _time.perf_counter() - t0

        t_all = _time.perf_counter() - t_total
        logger.info(
            f"⏱ step_visual: calc={t_calc*1000:.1f}ms "
            f"step_n({steps})={t_step*1000:.1f}ms "
            f"TOTAL={t_all*1000:.1f}ms"
        )

    def step_back_visual_candle(self) -> None:
        h = self._host
        if h.engine.df is None or h.engine.current_idx <= 0:
            return
        tf_min = _TF_MINUTES.get(h.chart_tab.visual_tf_cb.currentText().lower(), 1)
        tgt = max(0, h.engine.current_idx - tf_min)
        if tgt == h.engine.current_idx:
            return
        h.chart_tab._cache_key = None
        h.chart_tab._cached_snapshot = None
        h.chart_tab._cached_chart_arrays_key = None
        h.chart_tab._cached_chart_arrays = None
        if h.chart_tab_2 is not None and h.chart_tab_2.isVisible():
            h.chart_tab_2._cache_key = None
            h.chart_tab_2._cached_snapshot = None
            h.chart_tab_2._cached_chart_arrays_key = None
            h.chart_tab_2._cached_chart_arrays = None
        h.engine.reset()
        # Suppress autorange — we want to keep the user's zoom (_user_zoom_width)
        h.chart_tab._need_autorange = False
        if h.chart_tab_2 is not None and h.chart_tab_2.isVisible():
            h.chart_tab_2._need_autorange = False
        if tgt > 0:
            h.engine.step_n(tgt)
        if h.engine.current_idx >= 0:
            h.engine.step_completed.emit(
                h.engine.current_idx,
                h.engine.candle_at(h.engine.current_idx),
            )
            h.engine.update_stats()

    # ------------------------------------------------------------------
    # Date range — picker + handler
    # ------------------------------------------------------------------
    def on_data_loaded(self, total: int) -> None:
        h = self._host
        h.progress.setMaximum(total)
        h.progress.setValue(0)
        h.bar_replay._update_state()

        df = getattr(h.engine, "df", None)
        if df is not None and len(df) > 0:
            try:
                t0 = pd.to_datetime(df["time"].iloc[0]).date()
                t1 = pd.to_datetime(df["time"].iloc[-1]).date()
                qd0 = QDate(t0.year, t0.month, t0.day)
                qd1 = QDate(t1.year, t1.month, t1.day)
                for w in (h.range_from, h.range_to):
                    w.blockSignals(True)
                    w.setEnabled(True)
                h.range_from.setDateRange(qd0, qd1)
                h.range_from.setDate(qd0)
                h.range_to.setDateRange(qd0, qd1)
                h.range_to.setDate(qd1)
                for w in (h.range_from, h.range_to):
                    w.blockSignals(False)
                h.btn_date_range.setEnabled(True)
            except Exception:
                logger.exception("date-range picker init failed")

        if hasattr(h, "equity_sparkline"):
            h.equity_sparkline.set_values([h.engine.initial_equity])

    def on_range_changed(self, _qd: QDate) -> None:
        h = self._host
        if not h.range_from.isEnabled():
            return
        try:
            d0 = h.range_from.date().toPython()
            d1 = h.range_to.date().toPython()
            if d1 < d0:
                # Self-correct so end is never before start.
                h.range_to.blockSignals(True)
                h.range_to.setDate(h.range_from.date())
                h.range_to.blockSignals(False)
                d1 = d0
            ts0 = pd.Timestamp(d0)
            ts1 = pd.Timestamp(d1) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            h.engine.set_date_range(ts0, ts1)
            self.on_reset()
            h.chart_tab.redraw_chart()
        except Exception:
            logger.exception("apply date range failed")

    def reset_date_range_to_full(self) -> None:
        h = self._host
        df = getattr(h.engine, "df_full", None) or getattr(h.engine, "df", None)
        if df is None or len(df) == 0:
            return
        t0 = pd.to_datetime(df["time"].iloc[0]).date()
        t1 = pd.to_datetime(df["time"].iloc[-1]).date()
        for w in (h.range_from, h.range_to):
            w.blockSignals(True)
        h.range_from.setDate(QDate(t0.year, t0.month, t0.day))
        h.range_to.setDate(QDate(t1.year, t1.month, t1.day))
        for w in (h.range_from, h.range_to):
            w.blockSignals(False)
        h.engine.set_date_range(None, None)
        self.on_reset()
        h.chart_tab.redraw_chart()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def on_simulation_finished(self) -> None:
        h = self._host
        if not getattr(h.engine, "trades", None):
            return
        n = len(h.engine.trades)
        pnl = sum(t.pnl for t in h.engine.trades)
        color = sh.color_pnl_hex(get_theme(), pnl)
        h.lbl_info.setText(f"🏁 Finished — {n} trades, PnL: ${pnl:+,.2f}")
        h.lbl_info.setStyleSheet(f"color: {color}; font-weight: bold; background: transparent;")
        logger.info(f"🏁 Backtest finished: {n} trades, PnL=${pnl:+,.2f}")

    # ------------------------------------------------------------------
    # User drawing undo / redo (Ctrl+Z / Ctrl+Shift+Z).
    # ------------------------------------------------------------------
    def undo_drawing(self) -> None:
        ct = self._host.chart_tab
        if ct.user_drawings_history:
            ct.user_drawings_redo.append(list(ct.user_drawings))
            ct.user_drawings = ct.user_drawings_history.pop()
            ct._draw_user_items(ct.res_times)
            logger.info("↶ Drawing undo")

    def redo_drawing(self) -> None:
        ct = self._host.chart_tab
        if ct.user_drawings_redo:
            ct.user_drawings_history.append(list(ct.user_drawings))
            ct.user_drawings = ct.user_drawings_redo.pop()
            ct._draw_user_items(ct.res_times)
            logger.info("↷ Drawing redo")
