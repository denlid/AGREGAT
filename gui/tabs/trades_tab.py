"""
Trades Tab — Professional trade journal with filters, search, and advanced export.

CSV/Excel export emits ISO-8601 timestamps for `Entry Time` / `Exit Time` so
the output round-trips cleanly through pandas/Excel and is timezone-explicit.
"""

import csv
import logging

import pandas as pd
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener

logger = logging.getLogger("StrategyTester.gui.trades_tab")

COLUMNS = [
    "ID",
    "Direction",
    "Entry Time",
    "Exit Time",
    "Entry Price",
    "Exit Price",
    "PnL (pts)",
    "PnL ($)",
    "Volume",
    "Duration",
    "Exit Reason",
    "MAE",
    "MFE",
    "MAE ($)",
    "MFE ($)",
    "MAE%",
    "MFE%",
    "Session",
    "RR",
]

COLUMN_TOOLTIPS = {
    "ID": "Trade sequence number",
    "Direction": "LONG or SHORT position",
    "Entry Time": "Position entry timestamp",
    "Exit Time": "Position exit timestamp",
    "Entry Price": "Entry execution price",
    "Exit Price": "Exit execution price",
    "PnL (pts)": "Profit/Loss in points",
    "PnL ($)": "Profit/Loss in currency",
    "Volume": "Position size (lots)",
    "Duration": "Trade duration in minutes",
    "Exit Reason": "Why position was closed (SL/TP/EOD/MANUAL)",
    "MAE": "Max Adverse Excursion in points",
    "MFE": "Max Favorable Excursion in points",
    "MAE ($)": "Max Adverse Excursion in dollars (mae × volume × point_value)",
    "MFE ($)": "Max Favorable Excursion in dollars (mfe × volume × point_value)",
    "MAE%": "MAE as percentage of entry price",
    "MFE%": "MFE as percentage of entry price",
    "Session": "Trading session (Asian/London/NY/Overlap)",
    "RR": "Risk/Reward ratio (R-multiple)",
}


def _calc_rr(trade) -> float:
    """Calculate Risk/Reward ratio."""
    if trade.initial_sl and trade.initial_sl != 0:
        risk = abs(trade.entry_price - trade.initial_sl)
        if risk > 0:
            return trade.pnl_points / risk
    return 0.0


class TradesTab(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(8)

        # --- Header ---
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("📝 Trade Journal")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)

        header.addStretch()

        # Search box
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search trades...")
        self.search_box.setFixedWidth(200)
        self.search_box.textChanged.connect(self._apply_filter)
        self.search_box.setToolTip("Search by ID, direction, exit reason, or session")
        header.addWidget(self.search_box)

        outer.addLayout(header)

        # --- Sub-tabs (Executed / Skipped) ---
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)
        executed_pane = QWidget()
        skipped_pane = QWidget()
        layout = QVBoxLayout(executed_pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # --- Filter bar ---
        filt = QHBoxLayout()
        filt.setSpacing(8)

        filt.addWidget(QLabel("Filter:"))
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Wins", "Losses", "BE", "Long", "Short", "Custom Date"])
        self.filter_cb.setFixedWidth(120)
        self.filter_cb.currentTextChanged.connect(self._on_filter)
        self.filter_cb.setToolTip("Filter trades by type")
        filt.addWidget(self.filter_cb)

        self.d_from = QDateEdit()
        self.d_from.setCalendarPopup(True)
        self.d_from.setDate(QDate(2021, 1, 1))
        self.d_from.setFixedWidth(110)
        self.d_from.hide()
        filt.addWidget(self.d_from)

        lbl_to = QLabel("to")
        lbl_to.hide()
        self.lbl_to = lbl_to
        filt.addWidget(lbl_to)

        self.d_to = QDateEdit()
        self.d_to.setCalendarPopup(True)
        self.d_to.setDate(QDate.currentDate())
        self.d_to.setFixedWidth(110)
        self.d_to.hide()
        filt.addWidget(self.d_to)

        self.btn_filt = QPushButton("Apply")
        self.btn_filt.setFixedWidth(60)
        self.btn_filt.hide()
        self.btn_filt.clicked.connect(self._apply_filter)
        filt.addWidget(self.btn_filt)

        filt.addStretch()

        # Summary label
        self.lbl_summary = QLabel("")
        self.lbl_summary.setStyleSheet(sh.summary_label(get_theme()))
        filt.addWidget(self.lbl_summary)

        # Export button — direct click exports CSV, dropdown arrow gives more options
        self.btn_export = QPushButton("📤 Export")
        self.btn_export.setFixedWidth(90)
        self.btn_export.clicked.connect(lambda: self.export_csv("csv"))
        filt.addWidget(self.btn_export)

        self.btn_export_menu = QPushButton("▾")
        self.btn_export_menu.setFixedWidth(24)
        self.btn_export_menu.setToolTip("More export options")
        export_menu = QMenu()
        export_menu.addAction("Export as CSV", lambda: self.export_csv("csv"))
        export_menu.addAction("Export as Excel", lambda: self.export_csv("excel"))
        export_menu.addAction(
            "Export Filtered Only", lambda: self.export_csv("csv", filtered_only=True)
        )
        self.btn_export_menu.setMenu(export_menu)
        filt.addWidget(self.btn_export_menu)

        layout.addLayout(filt)

        # --- Table ---
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)

        # Set column tooltips
        for i, col in enumerate(COLUMNS):
            item = self.table.horizontalHeaderItem(i)
            if item and col in COLUMN_TOOLTIPS:
                item.setToolTip(COLUMN_TOOLTIPS[col])

        # Column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        # Set specific widths for some columns
        self.table.setColumnWidth(0, 50)  # ID
        self.table.setColumnWidth(1, 80)  # Direction
        self.table.setColumnWidth(2, 140)  # Entry Time
        self.table.setColumnWidth(3, 140)  # Exit Time
        self.table.setColumnWidth(9, 80)  # Duration
        self.table.setColumnWidth(10, 100)  # Exit Reason

        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setStyleSheet(sh.trades_table(get_theme()))

        layout.addWidget(self.table)

        # --- Skipped Trades pane ---
        slay = QVBoxLayout(skipped_pane)
        slay.setContentsMargins(0, 0, 0, 0)
        slay.setSpacing(6)

        skipped_header = QHBoxLayout()
        self.lbl_skipped_count = QLabel("0 skipped")
        skipped_header.addWidget(self.lbl_skipped_count)
        skipped_header.addStretch()
        slay.addLayout(skipped_header)

        self._skipped_columns = [
            "Time",
            "Reason",
            "Calc lot",
            "Cap",
            "SL pts",
            "Equity",
        ]
        self.skipped_table = QTableWidget(0, len(self._skipped_columns))
        self.skipped_table.setHorizontalHeaderLabels(self._skipped_columns)
        self.skipped_table.setAlternatingRowColors(True)
        self.skipped_table.setSortingEnabled(True)
        self.skipped_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        sh_header = self.skipped_table.horizontalHeader()
        sh_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        sh_header.setStretchLastSection(True)
        self.skipped_table.setColumnWidth(0, 160)
        self.skipped_table.setColumnWidth(1, 160)
        self.skipped_table.setColumnWidth(2, 90)
        self.skipped_table.setColumnWidth(3, 90)
        self.skipped_table.setColumnWidth(4, 80)
        self.skipped_table.setStyleSheet(sh.trades_table(get_theme()))
        self.skipped_table.setToolTip(
            "Trades that the engine declined to open. Common reasons:\n"
            " • LOT_LIMIT — calculated lot exceeded asset.max_lots_per_100k (clamped)\n"
            " • LOT_LIMIT_STRICT — strict cap mode cancelled the trade\n"
            " • ABS_LOT_LIMIT(_STRICT) — absolute broker cap clamped/cancelled\n"
            " • INVALID_SL — sl_points was non-positive"
        )
        slay.addWidget(self.skipped_table)

        # Mount sub-tabs.
        self.sub_tabs.addTab(executed_pane, "Trades")
        self.sub_tabs.addTab(skipped_pane, "Skipped")
        outer.addWidget(self.sub_tabs)

        # Connect signals
        engine.trade_completed.connect(self._on_new_trade)
        engine.data_loaded.connect(lambda _: self._clear())

        # Debounce timer — avoid rebuilding table on every trade during fast simulation
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._apply_filter)

        # Skipped-trades refresh — driven by step_completed (cheap O(N))
        try:
            engine.step_completed.connect(lambda *a: self._refresh_skipped())
        except Exception:
            pass

        # Phase 3 — re-apply locally-overridden QSS on theme switch.
        register_theme_listener(self._apply_theme)

    def _apply_theme(self) -> None:
        t = get_theme()
        if hasattr(self, "lbl_summary"):
            self.lbl_summary.setStyleSheet(sh.summary_label(t))
        if hasattr(self, "table"):
            self.table.setStyleSheet(sh.trades_table(t))

    def _clear(self):
        """Clear table and summary."""
        self.table.setRowCount(0)
        self.lbl_summary.setText("")
        self.search_box.clear()
        if hasattr(self, "skipped_table"):
            self.skipped_table.setRowCount(0)
            self.lbl_skipped_count.setText("0 skipped")

    def _refresh_skipped(self) -> None:
        """Repopulate the Skipped table from ``engine.skipped_trades``.

        Called on every step-completed signal, but the operation is
        cheap because skipped lists are short (orders of magnitude
        smaller than executed trades) and we rebuild from scratch only
        when the count changed.
        """
        if not hasattr(self, "skipped_table"):
            return
        skipped = list(getattr(self.engine, "skipped_trades", []) or [])
        cur = self.skipped_table.rowCount()
        if cur == len(skipped):
            return
        # Disable sorting while filling to keep insertion order; re-enable.
        self.skipped_table.setSortingEnabled(False)
        self.skipped_table.setRowCount(len(skipped))
        for r, entry in enumerate(skipped):
            time_s = str(entry.get("time", ""))
            reason = str(entry.get("reason", ""))
            calc_lot = entry.get("calculated_lot")
            cap = entry.get("max_lot")
            if cap is None:
                cap = entry.get("absolute_max_lots")
            sl_pts = entry.get("sl_points")
            eq = entry.get("equity")
            row = [
                time_s,
                reason,
                "" if calc_lot is None else f"{float(calc_lot):.2f}",
                "" if cap is None else f"{float(cap):.2f}",
                "" if sl_pts is None else f"{float(sl_pts):.2f}",
                "" if eq is None else f"{float(eq):,.2f}",
            ]
            for c, val in enumerate(row):
                self.skipped_table.setItem(r, c, QTableWidgetItem(val))
        self.skipped_table.setSortingEnabled(True)
        self.lbl_skipped_count.setText(f"{len(skipped)} skipped")

    def _on_filter(self, f):
        """Handle filter selection change."""
        custom = f == "Custom Date"
        self.d_from.setVisible(custom)
        self.d_to.setVisible(custom)
        self.lbl_to.setVisible(custom)
        self.btn_filt.setVisible(custom)
        if not custom:
            self._apply_filter()

    def _apply_filter(self, force=False):
        """Apply current filter and search to table."""
        filter_type = self.filter_cb.currentText()
        search_text = self.search_box.text().lower()

        all_trades = list(self.engine.trades)
        
        # Check if we actually need to rebuild
        current_state = (
            len(all_trades),
            filter_type,
            search_text,
            self.d_from.date().toPython() if filter_type == "Custom Date" else None,
            self.d_to.date().toPython() if filter_type == "Custom Date" else None,
        )
        if not force and getattr(self, "_last_filter_state", None) == current_state:
            return
        self._last_filter_state = current_state

        filtered = []

        # Apply type filter
        for t in all_trades:
            if filter_type == "Wins" and t.pnl <= 0:
                continue
            if filter_type == "Losses" and t.pnl >= 0:
                continue
            if filter_type == "BE" and t.pnl != 0:
                continue
            if filter_type == "Long" and t.direction != "long":
                continue
            if filter_type == "Short" and t.direction != "short":
                continue
            if filter_type == "Custom Date":
                try:
                    start = pd.Timestamp(self.d_from.date().toPython()).to_datetime64()
                    end = pd.Timestamp(self.d_to.date().toPython()).to_datetime64()
                    if t.entry_time_np is None or not (start <= t.entry_time_np <= end):
                        continue
                except Exception:
                    continue

            # Apply search filter
            if search_text:
                searchable = f"{t.id} {t.direction} {t.exit_reason} {t.session}".lower()
                if search_text not in searchable:
                    continue

            filtered.append(t)

        # Update table
        self._populate_table(filtered)

        # Update summary
        if filtered:
            wins = sum(1 for t in filtered if t.pnl > 0)
            losses = sum(1 for t in filtered if t.pnl < 0)
            total_pnl = sum(t.pnl for t in filtered)
            wr = (wins / len(filtered) * 100) if filtered else 0

            summary = f"Showing {len(filtered)} trades | Wins: {wins} | Losses: {losses} | WR: {wr:.1f}% | Total P&L: ${total_pnl:,.2f}"
            self.lbl_summary.setText(summary)
        else:
            self.lbl_summary.setText("No trades match filter")

    def _on_new_trade(self, trade):
        """Handle new trade completion — debounced to avoid UI freeze during fast simulation."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    # Alias used by main_window signal reconnect
    on_trade = _on_new_trade

    def _populate_table(self, trades):
        """Populate table with trades."""
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(trades))
        t_theme = get_theme()

        # Pre-cache QColor objects — avoids hex-string parsing per cell
        c_long = QColor(sh.c(t_theme, "accent_light"))
        c_short = QColor(sh.c(t_theme, "amber"))
        c_green = QColor(sh.c(t_theme, "green_bright"))
        c_red = QColor(sh.c(t_theme, "red_bright"))
        c_amber = QColor(sh.c(t_theme, "amber"))
        c_text3 = QColor(sh.c(t_theme, "text3"))
        # Pre-compute read-only + centered flags
        base_flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        align = Qt.AlignmentFlag.AlignCenter

        for row, t in enumerate(trades):
            rr = _calc_rr(t)

            mae_pct = (t.mae / t.entry_price * 100) if t.entry_price > 0 else 0
            mfe_pct = (t.mfe / t.entry_price * 100) if t.entry_price > 0 else 0

            duration_str = f"{t.duration_minutes} min"
            if t.duration_minutes >= 60:
                duration_str = f"{t.duration_minutes / 60:.1f}h"

            mae_d = getattr(t, "mae_dollars", 0.0)
            mfe_d = getattr(t, "mfe_dollars", 0.0)
            values = [
                str(t.id),
                t.direction.upper(),
                str(t.entry_time),
                str(t.exit_time),
                f"{t.entry_price:.2f}",
                f"{t.exit_price:.2f}",
                f"{t.pnl_points:+.2f}",
                f"${t.pnl:+,.2f}",
                f"{t.volume:.2f}",
                duration_str,
                t.exit_reason,
                f"{t.mae:.2f}",
                f"{t.mfe:.2f}",
                f"${mae_d:,.2f}",
                f"${mfe_d:,.2f}",
                f"{mae_pct:.2f}%",
                f"{mfe_pct:.2f}%",
                t.session,
                f"{rr:+.2f}R" if rr != 0 else "N/A",
            ]

            # Determine per-row colors once
            pnl_color = c_green if t.pnl > 0 else c_red if t.pnl < 0 else c_amber
            dir_color = c_long if t.direction == "long" else c_short
            if t.exit_reason in ("TP", "TP_2RR", "LOCK"):
                exit_color = c_green
            elif t.exit_reason in ("SL", "BE", "GAP_SL"):
                exit_color = c_red
            else:
                exit_color = c_amber
            mae_color = c_red if mae_pct > 1.0 else c_amber if mae_pct > 0.5 else c_text3
            mfe_color = c_green if mfe_pct > 1.0 else c_long if mfe_pct > 0.5 else c_text3
            if rr != 0:
                rr_color = c_green if rr > 1.0 else c_amber if rr > 0 else c_red
            else:
                rr_color = None

            for col, val in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(val)
                    item.setFlags(base_flags)
                    item.setTextAlignment(align)
                    self.table.setItem(row, col, item)
                else:
                    item.setText(val)

                if col == 1:
                    item.setForeground(dir_color)
                elif col == 6 or col == 7:
                    item.setForeground(pnl_color)
                elif col == 10:
                    item.setForeground(exit_color)
                elif col == 15:
                    item.setForeground(mae_color)
                elif col == 16:
                    item.setForeground(mfe_color)
                elif col == 18 and rr_color is not None:
                    item.setForeground(rr_color)
                else:
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)

        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)

    def export_csv(self, format="csv", filtered_only=False):
        """Export trades to CSV or Excel."""
        if filtered_only:
            # Get currently visible trades
            trades = []
            for row in range(self.table.rowCount()):
                trade_id = int(self.table.item(row, 0).text())
                trade = next((t for t in self.engine.trades if t.id == trade_id), None)
                if trade:
                    trades.append(trade)
        else:
            trades = list(self.engine.trades)

        if not trades:
            logger.warning("⚠️ No trades to export")
            return

        # File dialog
        if format == "excel":
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Trades", "trades.xlsx", "Excel Files (*.xlsx);;All Files (*)"
            )
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Trades", "trades.csv", "CSV Files (*.csv);;All Files (*)"
            )

        if not path:
            return

        try:
            if format == "excel":
                # Export to Excel with formatting
                try:
                    import openpyxl  # noqa: F401
                except ImportError:
                    logger.error("❌ openpyxl not installed. Run: pip install openpyxl")
                    from PySide6.QtWidgets import QMessageBox

                    QMessageBox.warning(
                        self,
                        "Missing Dependency",
                        "openpyxl is required for Excel export.\n\nRun: pip install openpyxl",
                    )
                    return
                df = pd.DataFrame(
                    [
                        {
                            "ID": t.id,
                            "Direction": t.direction.upper(),
                            "Entry Time": pd.Timestamp(t.entry_time).isoformat(),
                            "Exit Time": pd.Timestamp(t.exit_time).isoformat(),
                            "Entry Price": t.entry_price,
                            "Exit Price": t.exit_price,
                            "PnL (pts)": t.pnl_points,
                            "PnL ($)": t.pnl,
                            "Volume": t.volume,
                            "Duration (min)": t.duration_minutes,
                            "Exit Reason": t.exit_reason,
                            "MAE": t.mae,
                            "MFE": t.mfe,
                            "MAE ($)": getattr(t, "mae_dollars", 0.0),
                            "MFE ($)": getattr(t, "mfe_dollars", 0.0),
                            "MAE%": (t.mae / t.entry_price * 100) if t.entry_price > 0 else 0,
                            "MFE%": (t.mfe / t.entry_price * 100) if t.entry_price > 0 else 0,
                            "Session": t.session,
                            "RR": _calc_rr(t),
                        }
                        for t in trades
                    ]
                )

                df.to_excel(path, index=False, engine="openpyxl")
            else:
                # Export to CSV
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(COLUMNS)

                    for t in trades:
                        rr = _calc_rr(t)
                        mae_pct = (t.mae / t.entry_price * 100) if t.entry_price > 0 else 0
                        mfe_pct = (t.mfe / t.entry_price * 100) if t.entry_price > 0 else 0
                        try:
                            entry_iso = pd.Timestamp(t.entry_time).isoformat()
                            exit_iso = pd.Timestamp(t.exit_time).isoformat()
                        except Exception:
                            entry_iso, exit_iso = str(t.entry_time), str(t.exit_time)
                        writer.writerow(
                            [
                                t.id,
                                t.direction.upper(),
                                entry_iso,
                                exit_iso,
                                t.entry_price,
                                t.exit_price,
                                t.pnl_points,
                                t.pnl,
                                t.volume,
                                t.duration_minutes,
                                t.exit_reason,
                                t.mae,
                                t.mfe,
                                f"{getattr(t, 'mae_dollars', 0.0):.2f}",
                                f"{getattr(t, 'mfe_dollars', 0.0):.2f}",
                                f"{mae_pct:.2f}%",
                                f"{mfe_pct:.2f}%",
                                t.session,
                                f"{rr:.2f}R" if rr != 0 else "N/A",
                            ]
                        )

            logger.info(f"✅ Exported {len(trades)} trades to: {path}")
        except Exception as e:
            logger.error(f"❌ Export failed: {str(e)}", exc_info=True)
