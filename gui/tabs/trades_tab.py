"""
Trades Tab — Professional trade journal with filters, search, and advanced export.

Phase 2.2 — :class:`QTableWidget` was replaced with a
``QTableView`` + :class:`gui.tabs.trades_table_model.TradesTableModel`
pair. The model holds trades by reference and formats per-cell data
on demand, eliminating the previous 1.14 M-widget allocation on a
60 k-trade run (5–10 s freeze, ~120 MB resident → near-instant).

CSV/Excel export emits ISO-8601 timestamps for ``Entry Time`` /
``Exit Time`` so the output round-trips cleanly through pandas/Excel
and is timezone-explicit.
"""

import csv
import logging

import pandas as pd
from PySide6.QtCore import QDate, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener
from gui.tabs.trades_table_model import (
    COLUMNS,
    TradesFilterProxy,
    TradesTableModel,
    _calc_rr,
)

logger = logging.getLogger("StrategyTester.gui.trades_tab")


class TradesTab(QWidget):
    """Trade journal — :class:`QTableView` over :class:`TradesTableModel`.

    Emits :py:attr:`trade_selected` whenever the user picks a row, so a
    future right-side detail panel (Phase 2.2 stretch / Phase 4 widget)
    can subscribe without coupling to the underlying view.
    """

    trade_selected = Signal(object)

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
        # Phase 6.2: debounce typing so a user typing "long" no longer
        # fires four sequential filter rebuilds. The timer is started
        # in :py:meth:`_on_search_text_changed` and only fires once the
        # user stops typing for 200 ms.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(200)
        self._search_debounce.timeout.connect(self._apply_filter)
        self.search_box.textChanged.connect(self._on_search_text_changed)
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
        self.filter_cb.addItems(
            ["All", "Wins", "Losses", "BE", "Long", "Short", "Custom Date"]
        )
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

        # --- Table (model/view) ---
        # Phase 2.2 — bind a single TradesTableModel for the lifetime of
        # the tab; subsequent backtest runs swap the underlying list via
        # ``set_trades`` so per-cell allocation is amortised at zero.
        self._model = TradesTableModel(self)
        self._proxy = TradesFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self.table = QTableView()
        self.table.setModel(self._proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(sh.trades_table(get_theme()))

        # Column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 50)  # ID
        self.table.setColumnWidth(1, 80)  # Direction
        self.table.setColumnWidth(2, 140)  # Entry Time
        self.table.setColumnWidth(3, 140)  # Exit Time
        self.table.setColumnWidth(9, 80)  # Duration
        self.table.setColumnWidth(10, 100)  # Exit Reason

        # Re-emit current-row changes as ``trade_selected(Trade)``.
        sel = self.table.selectionModel()
        if sel is not None:
            sel.currentRowChanged.connect(self._on_current_row_changed)

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
        # Trades use cached QColor instances per theme — refresh them
        # so the new palette applies without rebuilding the table.
        if hasattr(self, "_model"):
            self._model.refresh_theme()

    def _clear(self):
        """Clear table and summary."""
        self._model.set_trades([])
        self._last_filter_state = None
        self.lbl_summary.setText("")
        self.search_box.clear()
        if hasattr(self, "skipped_table"):
            self.skipped_table.setRowCount(0)
            self.lbl_skipped_count.setText("0 skipped")

    def _on_current_row_changed(self, current, _previous) -> None:
        """Map the proxy row → source row → :class:`Trade` and re-emit."""
        if not current.isValid():
            self.trade_selected.emit(None)
            return
        source_index = self._proxy.mapToSource(current)
        trade = self._model.trade_at(source_index.row())
        self.trade_selected.emit(trade)

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

    def _on_search_text_changed(self, _text: str) -> None:
        """Debounce search-box keystrokes (Phase 6.2).

        Without this, every keystroke fired a full filter rebuild —
        4 round-trips for "long". With a 200 ms timer, only the final
        text triggers a rebuild, which is essentially free now (5 ms
        on 50 k trades) but the debounce still keeps the typing
        experience absolutely smooth.
        """
        self._search_debounce.start()

    def _apply_filter(self, force=False):
        """Push the current filter / search / date-range into the proxy.

        Phase 2.2 — actual row filtering happens inside Qt's
        :class:`QSortFilterProxyModel` in C++; this method only sets
        the proxy's parameters and refreshes the visible-trades summary.
        """
        filter_type = self.filter_cb.currentText()
        search_text = self.search_box.text()

        # Memo'd state — avoid touching the proxy when nothing changed
        # (e.g. the debounce timer fires while the filters are static).
        current_state = (
            len(self.engine.trades),
            filter_type,
            search_text.lower(),
            self.d_from.date().toPython() if filter_type == "Custom Date" else None,
            self.d_to.date().toPython() if filter_type == "Custom Date" else None,
        )
        prev = getattr(self, "_last_filter_state", None)
        if not force and prev == current_state:
            return
        self._last_filter_state = current_state

        # Re-bind the source list — cheap when the count hasn't changed
        # because the model just re-emits modelReset; the proxy then
        # re-runs filterAcceptsRow on each source row.
        prev_count = prev[0] if prev else None
        if prev_count != len(self.engine.trades):
            self._model.set_trades(list(self.engine.trades))

        # Push filter params; each setter invalidates the proxy once.
        self._proxy.set_filter_type(filter_type)
        self._proxy.set_search_text(search_text)
        if filter_type == "Custom Date":
            self._proxy.set_date_range(self.d_from.date(), self.d_to.date())
        else:
            self._proxy.set_date_range(None, None)

        self._update_summary()

    def _update_summary(self) -> None:
        """Refresh the summary line over currently-visible (filtered)
        trades. Iterates the proxy because that's the post-filter set;
        the loop is over visible row count, not the full ``engine.trades``."""
        n = self._proxy.rowCount()
        if n == 0:
            self.lbl_summary.setText("No trades match filter")
            return

        wins = 0
        losses = 0
        total_pnl = 0.0
        for r in range(n):
            src = self._proxy.mapToSource(self._proxy.index(r, 0))
            t = self._model.trade_at(src.row())
            if t is None:
                continue
            if t.pnl > 0:
                wins += 1
            elif t.pnl < 0:
                losses += 1
            total_pnl += t.pnl
        wr = (wins / n * 100) if n else 0
        self.lbl_summary.setText(
            f"Showing {n} trades | Wins: {wins} | Losses: {losses} "
            f"| WR: {wr:.1f}% | Total P&L: ${total_pnl:,.2f}"
        )

    def _on_new_trade(self, trade):
        """Handle new trade completion — debounced to avoid UI freeze
        during fast simulation."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    # Alias used by main_window signal reconnect
    on_trade = _on_new_trade

    def export_csv(self, format="csv", filtered_only=False):
        """Export trades to CSV or Excel."""
        if filtered_only:
            # Walk the proxy in display order so the export reflects the
            # currently-visible (filtered + sorted) set.
            trades = []
            for r in range(self._proxy.rowCount()):
                src = self._proxy.mapToSource(self._proxy.index(r, 0))
                t = self._model.trade_at(src.row())
                if t is not None:
                    trades.append(t)
        else:
            trades = list(self.engine.trades)

        if not trades:
            logger.warning("⚠️ No trades to export")
            return

        # File dialog
        if format == "excel":
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Trades",
                "trades.xlsx",
                "Excel Files (*.xlsx);;All Files (*)",
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
                            "MAE%": (t.mae / t.entry_price * 100)
                            if t.entry_price > 0
                            else 0,
                            "MFE%": (t.mfe / t.entry_price * 100)
                            if t.entry_price > 0
                            else 0,
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
                        mae_pct = (
                            (t.mae / t.entry_price * 100) if t.entry_price > 0 else 0
                        )
                        mfe_pct = (
                            (t.mfe / t.entry_price * 100) if t.entry_price > 0 else 0
                        )
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
