"""Sprint 3.5 — Status-bar construction + stats handler.

Owns the bottom :class:`QStatusBar` (equity, position, trades, P&L,
win-rate, session-equity, equity sparkline) and the ``stats_updated``
signal handler that refreshes those widgets. ``MainWindow`` keeps a
thin ``_on_stats`` shim that delegates here so existing test wiring
continues to work.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QStatusBar

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.statusbar")


class StatusBarController:
    """Builds the bottom status bar and routes engine stats into it."""

    def __init__(self, host: MainWindow) -> None:
        self._host = host
        self.status_bar = QStatusBar()
        self._build_widgets()

    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        from gui.widgets.sparkline import SparklineWidget

        h = self._host
        sb = self.status_bar
        sb.setStyleSheet(sh.status_bar(get_theme()))
        h._status_bar = sb

        h.st_eq = QLabel("Equity: —")
        h.st_pos = QLabel("Position: None")
        h.st_tr = QLabel("Trades: 0")
        h.st_pnl = QLabel("PnL: —")
        h.st_wr = QLabel("WR: —")
        h.st_ses = QLabel("")

        t = get_theme()
        h.st_eq.setStyleSheet(sh.status_label(t, "accent_light"))
        h.st_pos.setStyleSheet(sh.status_label(t, "text2"))
        h.st_tr.setStyleSheet(sh.status_label(t, "text2"))
        h.st_pnl.setStyleSheet(sh.status_label(t, "text2"))
        h.st_wr.setStyleSheet(sh.status_label(t, "text2"))
        # The session label drops the trailing right border so the
        # status bar's right edge stays clean.
        h.st_ses.setStyleSheet(sh.status_label(t, "amber") + " border-right: none;")

        h.st_eq.setToolTip("Current equity = initial equity + total PnL (closed)")
        h.st_tr.setToolTip("Total number of completed trades")
        h.st_pnl.setToolTip("Total realised PnL since simulation start")
        h.st_wr.setToolTip("Win rate (closed trades only)")
        h.st_pos.setToolTip("Currently open position direction and lots")

        h.equity_sparkline = SparklineWidget(width=140, height=22)
        h.equity_sparkline.setToolTip("Live equity curve since the start of the simulation")

        sb.addWidget(h.st_eq)
        sb.addWidget(h.st_pos)
        sb.addWidget(h.st_tr)
        sb.addWidget(h.st_pnl)
        sb.addWidget(h.st_wr)
        sb.addPermanentWidget(h.equity_sparkline)
        sb.addPermanentWidget(h.st_ses)

    # ------------------------------------------------------------------
    # Signal handlers (was MainWindow._on_stats / on_data_loaded sparkline reset).
    # ------------------------------------------------------------------
    def on_stats(self, s: dict) -> None:
        h = self._host
        sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(s.get("currency", "$"), "$")
        eq = s["equity"]
        init = h.engine.initial_equity
        pnl = eq - init
        pnl_sign = "+" if pnl >= 0 else ""
        t = get_theme()
        pnl_color = sh.color_pnl_hex(t, pnl)

        h.st_eq.setText(f"  Eq: {sym}{eq:,.0f}")
        h.st_eq.setStyleSheet(sh.status_label_color(pnl_color, bold=True))
        h.st_pnl.setText(f"  PnL: {pnl_sign}{sym}{pnl:,.0f}")
        h.st_pnl.setStyleSheet(sh.status_label_color(pnl_color, bold=True))
        h.st_tr.setText(f"  T: {s['total_trades']}  DD: {s['max_drawdown']:.1f}%")

        wr = s.get("winrate", 0.0)
        wr_color = sh.color_winrate_hex(t, wr)
        h.st_wr.setText(f"  WR: {wr:.1f}%")
        h.st_wr.setStyleSheet(sh.status_label_color(wr_color, bold=True))

        pos = s.get("pos_status", "None")
        h.st_pos.setText(f"  {pos}")

        if hasattr(h, "equity_sparkline"):
            curve = getattr(h.engine, "equity_track", None)
            if curve is not None and len(curve) > 0:
                arr = list(curve)
                # Down-sample to a max of 200 points so the sparkline stays cheap.
                if len(arr) > 200:
                    step = max(1, len(arr) // 200)
                    arr = arr[::step]
                h.equity_sparkline.set_values(arr)

    def reset_sparkline(self) -> None:
        h = self._host
        if hasattr(h, "equity_sparkline"):
            h.equity_sparkline.set_values([h.engine.initial_equity])
