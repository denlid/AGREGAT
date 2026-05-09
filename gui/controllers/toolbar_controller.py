"""Sprint 3.5 — Toolbar construction.

Builds the entire ``Chart`` page toolbar that ``MainWindow.__init__``
used to assemble inline. Widgets are attached to *host* (the
``MainWindow``) so the rest of the codebase can keep referencing them
through ``self.range_from`` / ``self.btn_reset`` etc. without changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.toolbar")


class ToolbarController:
    """Builds the chart-page toolbar and binds it to ``host``.

    The original layout (asset selector → date range → TF / mode →
    dual-view → markers / lines → drawing tools → fit → playback →
    progress → info → export → reset) is preserved bit-for-bit.
    """

    def __init__(self, host: MainWindow) -> None:
        self._host = host
        self.separators: List[QFrame] = []

    # ------------------------------------------------------------------
    # Small helpers — local to this controller so they don't leak into
    # ``MainWindow`` namespace.
    # ------------------------------------------------------------------
    def _sep(self) -> QFrame:
        s = QFrame()
        s.setFrameShape(QFrame.Shape.VLine)
        s.setFixedWidth(1)
        s.setFixedHeight(20)
        s.setStyleSheet(sh.vline(get_theme()))
        self.separators.append(s)
        return s

    @staticmethod
    def _icon_btn(text: str, tooltip: str, fixed_w: int = 32) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(fixed_w, 28)
        b.setToolTip(tooltip)
        return b

    # ------------------------------------------------------------------
    def build_toolbar_widget(self) -> QWidget:
        """Return the assembled toolbar widget, attaching widgets to *host*."""
        h = self._host
        t = get_theme()

        tb_widget = QWidget()
        tb_widget.setObjectName("toolbar")
        tb_widget.setMinimumWidth(1000)
        tb_widget.setStyleSheet(sh.main_toolbar(t))
        h._tb_widget = tb_widget
        h._toolbar_separators = self.separators

        tb = QHBoxLayout(tb_widget)
        tb.setContentsMargins(6, 2, 6, 2)
        tb.setSpacing(2)

        self._add_asset_section(tb)
        tb.addWidget(self._sep())
        self._add_date_range_section(tb)
        tb.addWidget(self._sep())
        self._add_tf_mode_section(tb)
        tb.addWidget(self._sep())
        self._add_chart_options_section(tb)
        tb.addWidget(self._sep())
        self._add_drawing_section(tb)
        tb.addWidget(self._sep())
        self._add_navigation_section(tb)
        tb.addWidget(self._sep())
        self._add_playback_section(tb)
        tb.addWidget(self._sep())
        self._add_progress_info_section(tb)
        tb.addWidget(self._sep())
        self._add_action_buttons(tb)

        return tb_widget

    # ------------------------------------------------------------------
    # Sections — kept tiny so the layout reads top-down.
    # ------------------------------------------------------------------
    def _add_asset_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        h.asset_cb = QComboBox()
        h.asset_cb.setFixedWidth(96)
        h.asset_cb.setToolTip("Trading instrument (loaded from config/assets.json)")
        h._asset_ctrl.reload_asset_list(initial=True)
        h.asset_cb.currentTextChanged.connect(h._asset_ctrl.on_asset_changed)
        tb.addWidget(h.asset_cb)

        h.btn_add_asset = QToolButton()
        h.btn_add_asset.setText("+")
        h.btn_add_asset.setFixedSize(22, 28)
        h.btn_add_asset.setToolTip("Add a custom asset (point value, tick size, …)")
        h.btn_add_asset.clicked.connect(h._asset_ctrl.on_add_custom_asset)
        tb.addWidget(h.btn_add_asset)

    def _add_date_range_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        h.range_from = QDateEdit()
        h.range_from.setCalendarPopup(True)
        h.range_from.setDisplayFormat("yyyy-MM-dd")
        h.range_from.setEnabled(False)
        h.range_from.dateChanged.connect(h._replay_ctrl.on_range_changed)

        h.range_to = QDateEdit()
        h.range_to.setCalendarPopup(True)
        h.range_to.setDisplayFormat("yyyy-MM-dd")
        h.range_to.setEnabled(False)
        h.range_to.dateChanged.connect(h._replay_ctrl.on_range_changed)

        h.btn_date_range = QToolButton()
        h.btn_date_range.setText("📅 Range")
        h.btn_date_range.setToolTip("Restrict backtest to a date range")
        h.btn_date_range.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        h.btn_date_range.setFixedHeight(28)

        range_popup = QWidget()
        rp = QVBoxLayout(range_popup)
        rp.setContentsMargins(8, 8, 8, 8)
        rp.setSpacing(6)
        for lbl_text, w in (("From", h.range_from), ("To", h.range_to)):
            row = QHBoxLayout()
            row.setSpacing(6)
            cap = QLabel(lbl_text)
            cap.setFixedWidth(36)
            cap.setStyleSheet(f"color: {sh.c(get_theme(), 'text2')}; font-size: 11px;")
            row.addWidget(cap)
            w.setFixedWidth(120)
            row.addWidget(w)
            rp.addLayout(row)
        btn_reset_range = QPushButton("Reset to full")
        btn_reset_range.clicked.connect(h._replay_ctrl.reset_date_range_to_full)
        rp.addWidget(btn_reset_range)
        wa = QWidgetAction(h.btn_date_range)
        wa.setDefaultWidget(range_popup)
        range_menu = QMenu(h.btn_date_range)
        range_menu.addAction(wa)
        h.btn_date_range.setMenu(range_menu)
        # Older callers used ``btn_range_full``; keep the alias.
        h.btn_range_full = h.btn_date_range
        tb.addWidget(h.btn_date_range)

    def _add_tf_mode_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        # Embed the TF / Mode combos owned by ChartTab.
        tb.addWidget(h.chart_tab.visual_tf_cb)
        tb.addWidget(h.chart_tab.chart_mode_cb)

        # Hidden placeholders for the second chart's combos — they are
        # swapped in lazily once Dual View is enabled.
        h.tf2_cb = QComboBox()
        h.tf2_cb.hide()
        tb.addWidget(h.tf2_cb)
        h.mode2_cb = QComboBox()
        h.mode2_cb.hide()
        tb.addWidget(h.mode2_cb)

        h.btn_dual_view = QToolButton()
        h.btn_dual_view.setText("✨")
        h.btn_dual_view.setToolTip("Dual View")
        h.btn_dual_view.setCheckable(True)
        h.btn_dual_view.setFixedSize(28, 28)
        h.btn_dual_view.clicked.connect(self.toggle_dual_view)
        tb.addWidget(h.btn_dual_view)

    def _add_chart_options_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        # Hide the original (Settings-tab) checkboxes — the toolbar now
        # exposes them as compact icon toggles.
        h.chart_tab.show_markers_cb.hide()
        h.chart_tab.show_connections_cb.hide()

        h.btn_markers = QToolButton()
        h.btn_markers.setText("📍")
        h.btn_markers.setToolTip("Toggle trade markers")
        h.btn_markers.setCheckable(True)
        h.btn_markers.setChecked(True)
        h.btn_markers.setFixedSize(28, 28)
        h.btn_markers.toggled.connect(
            lambda c: (
                h.chart_tab.show_markers_cb.setChecked(c),
                h.chart_tab.on_markers_toggled(c),
            )
        )
        tb.addWidget(h.btn_markers)

        h.btn_lines = QToolButton()
        h.btn_lines.setText("🔗")
        h.btn_lines.setToolTip("Toggle trade connection lines")
        h.btn_lines.setCheckable(True)
        h.btn_lines.setChecked(True)
        h.btn_lines.setFixedSize(28, 28)
        h.btn_lines.toggled.connect(
            lambda c: (
                h.chart_tab.show_connections_cb.setChecked(c),
                h.chart_tab.redraw_chart(),
            )
        )
        tb.addWidget(h.btn_lines)

        tb.addWidget(h.chart_tab.layers_btn)

    def _add_drawing_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        tb.addWidget(h.chart_tab.draw_btn)
        tb.addWidget(h.chart_tab.cancel_btn)

    def _add_navigation_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        h.chart_tab.fit_btn.setToolTip("Zoom to Fit (Home)")
        tb.addWidget(h.chart_tab.fit_btn)

    def _add_playback_section(self, tb: QHBoxLayout) -> None:
        from gui.widgets.bar_replay import BarReplayControls

        h = self._host
        h.bar_replay = BarReplayControls(
            h.engine, h._replay_ctrl.step_visual_candle, h._replay_ctrl.step_back_visual_candle, h
        )
        tb.addWidget(h.bar_replay)

    def _add_progress_info_section(self, tb: QHBoxLayout) -> None:
        h = self._host
        h.progress = QProgressBar()
        h.progress.setFixedSize(120, 14)
        h.progress.setTextVisible(False)
        h.progress.setToolTip("Simulation progress")
        h.progress.setStyleSheet(sh.main_progress_bar(get_theme()))
        tb.addWidget(h.progress)

        tb.addStretch()

        h.lbl_info = QLabel("")
        h.lbl_info.setStyleSheet(sh.info_label(get_theme()))
        tb.addWidget(h.lbl_info)

    def _add_action_buttons(self, tb: QHBoxLayout) -> None:
        h = self._host
        h.btn_export_report = self._icon_btn(
            "📤", "Export full report (trades / stats / equity / analytics)", 28
        )
        h.btn_export_report.clicked.connect(h.on_export_report)  # kept on host (Qt-aware)
        tb.addWidget(h.btn_export_report)

        h.btn_reset = self._icon_btn("↺", "Reset simulation (Ctrl+R)", 28)
        h.btn_reset.clicked.connect(h._replay_ctrl.on_reset)
        h.btn_reset.setStyleSheet(sh.reset_button(get_theme()))
        tb.addWidget(h.btn_reset)

    # ------------------------------------------------------------------
    # Dual-view (Phase 3c) — kept here because it touches the toolbar
    # layout we just built and the chart splitter on ``host``.
    # ------------------------------------------------------------------
    def ensure_chart_tab_2(self) -> None:
        from gui.chart_tab import ChartTab

        h = self._host
        if h.chart_tab_2 is not None:
            return
        h.chart_tab_2 = ChartTab(h.engine)
        h.chart_tab.sigTimeRangeChanged.connect(h.chart_tab_2.sync_to_time_range)
        h.chart_tab_2.sigTimeRangeChanged.connect(h.chart_tab.sync_to_time_range)
        tb_layout = h.tf2_cb.parent().layout()
        idx_tf = tb_layout.indexOf(h.tf2_cb)
        idx_mode = tb_layout.indexOf(h.mode2_cb)
        tb_layout.removeWidget(h.tf2_cb)
        h.tf2_cb.deleteLater()
        tb_layout.removeWidget(h.mode2_cb)
        h.mode2_cb.deleteLater()
        h.tf2_cb = h.chart_tab_2.visual_tf_cb
        h.mode2_cb = h.chart_tab_2.chart_mode_cb
        tb_layout.insertWidget(idx_tf, h.tf2_cb)
        tb_layout.insertWidget(idx_mode, h.mode2_cb)
        h.chart_splitter.addWidget(h.chart_tab_2)

    def toggle_dual_view(self) -> None:
        h = self._host
        is_dual = h.btn_dual_view.isChecked()
        if is_dual:
            self.ensure_chart_tab_2()
        if h.chart_tab_2 is not None:
            h.chart_tab_2.setVisible(is_dual)
        h.tf2_cb.setVisible(is_dual)
        h.mode2_cb.setVisible(is_dual)
        if is_dual and h.chart_tab_2 is not None:
            h.chart_tab_2.redraw_chart()
            vb = h.chart_tab.plot_widget.getViewBox()
            h.chart_tab._on_my_range_changed(vb, vb.viewRange())

    def jump_to_end(self) -> None:
        h = self._host
        charts = [h.chart_tab]
        if h.chart_tab_2 is not None:
            charts.append(h.chart_tab_2)
        for ct in charts:
            if not ct.isVisible() or len(ct.res_times) == 0:
                continue
            vb = ct.plot_widget.getViewBox()
            vr = vb.viewRect()
            cur_x = len(ct.res_times) - 1
            vb.setRange(xRange=[cur_x - vr.width() + 5, cur_x + 5], padding=0)


# Local alias so ``Qt.FocusPolicy.NoFocus`` retains the original
# import that older callers expected through this module. The
# original toolbar didn't actually use it, but keeping the symbol
# imported avoids surprise breakage if someone re-imports it from
# here.
_ = Qt.FocusPolicy.NoFocus
