"""Phase 2.2 — Trades Tab model layer.

The original ``TradesTab`` populated a :class:`QTableWidget` with one
:class:`QTableWidgetItem` per cell. On 60 000 trades × 19 columns that
is **1.14 million heap-allocated widgets** — a 5–10 s freeze and
~120 MB of resident memory before the user can interact with anything.

This module replaces the widget-per-cell model with a Qt
model/view pair:

* :class:`TradesTableModel` — a :class:`QAbstractTableModel` that
  holds the trades by reference and formats per-cell values lazily on
  demand from ``data(index, role)``. Memory cost is one Python list
  pointer per trade, not one widget per cell.
* :class:`TradesFilterProxy` — a custom :class:`QAbstractTableModel`
  proxy that carries the type-filter (Wins / Losses / Long / Short /
  Custom Date), the search-box query and the active sort column. It
  maintains its own ``_visible`` permutation in NumPy so filter and
  sort each cost a single ``np.argsort`` / boolean-mask pass instead
  of ``n log n`` Python ``lessThan`` callbacks. On 50 000 trades this
  drops a sort-by-PnL click from ~6 s to ~6 ms (Phase 6.2).

The public ``TradesTab`` widget switches from
:class:`QTableWidget` to :class:`QTableView` and binds a single
``TradesTableModel`` for its lifetime; subsequent backtest runs only
need to call :py:meth:`TradesTableModel.set_trades` to swap in the
new list — no per-cell work, no widget allocation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QModelIndex,
    QObject,
    Qt,
)
from PySide6.QtGui import QColor

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme

SKIPPED_COLUMNS = [
    "Time", "Reason", "Calc lot", "Cap", "SL pts", "Equity",
]


class SkippedTradesModel(QAbstractTableModel):
    """Model for the skipped trades table (engine.skipped_trades list-of-dicts)."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._data: List[Dict[str, Any]] = []

    def set_skipped(self, data: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._data = list(data)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._data)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(SKIPPED_COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        entry = self._data[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return str(entry.get("time", ""))
            if col == 1:
                return str(entry.get("reason", ""))
            keys = ("calculated_lot", "max_lot", "sl_points", "equity")
            v = entry.get(keys[col - 2])
            return "" if v is None else f"{float(v):.2f}"
        if role == Qt.ItemDataRole.UserRole:
            if col == 0:
                return str(entry.get("time", ""))
            if col == 1:
                return str(entry.get("reason", ""))
            keys = ("calculated_lot", "max_lot", "sl_points", "equity")
            v = entry.get(keys[col - 2])
            return float(v) if v is not None else 0.0
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return SKIPPED_COLUMNS[section] if section < len(SKIPPED_COLUMNS) else ""
        return None


COLUMNS: List[str] = [
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

# Column indices — kept as named constants so the model and any
# external column-aware code (e.g. delegate, exporter) reference one
# source of truth.
COL_ID = 0
COL_DIRECTION = 1
COL_ENTRY_TIME = 2
COL_EXIT_TIME = 3
COL_ENTRY_PRICE = 4
COL_EXIT_PRICE = 5
COL_PNL_POINTS = 6
COL_PNL_DOLLARS = 7
COL_VOLUME = 8
COL_DURATION = 9
COL_EXIT_REASON = 10
COL_MAE = 11
COL_MFE = 12
COL_MAE_DOLLARS = 13
COL_MFE_DOLLARS = 14
COL_MAE_PCT = 15
COL_MFE_PCT = 16
COL_SESSION = 17
COL_RR = 18


def _calc_rr(trade: Any) -> float:
    """Risk/Reward ratio (R-multiple). 0 when SL was zero / unknown."""
    # Phase 1 Stabilization Step 5 — prioritise pre-computed RR from engine
    rr = getattr(trade, "rr", None)
    if rr is not None:
        try:
            return float(rr)
        except (TypeError, ValueError):
            pass

    try:
        initial_sl = getattr(trade, "initial_sl", None)
        if initial_sl is None or initial_sl == 0:
            return 0.0
        entry_price = float(getattr(trade, "entry_price", 0.0) or 0.0)
        risk = abs(entry_price - float(initial_sl))
        if risk > 0:
            pnl_points = float(getattr(trade, "pnl_points", 0.0) or 0.0)
            return pnl_points / risk
    except (TypeError, ValueError, AttributeError):
        pass
    return 0.0


def _format_duration(minutes: int) -> str:
    if minutes >= 60:
        return f"{minutes / 60:.1f}h"
    return f"{minutes} min"


def _safe_float(trade: Any, attr: str, default: float = 0.0) -> float:
    try:
        value = getattr(trade, attr, default)
        return float(value if value is not None else default)
    except (TypeError, ValueError, AttributeError):
        return default


def _safe_int(trade: Any, attr: str, default: int = 0) -> int:
    try:
        value = getattr(trade, attr, default)
        return int(value if value is not None else default)
    except (TypeError, ValueError, AttributeError):
        return default


def _safe_str(trade: Any, attr: str, default: str = "") -> str:
    try:
        value = getattr(trade, attr, default)
        return str(value if value is not None else default)
    except AttributeError:
        return default


# Custom Qt roles — proxy uses ``RawValueRole`` for sort comparisons
# and the type-filter checks pull through ``Qt.UserRole``.
RawValueRole = Qt.ItemDataRole.UserRole + 1


class TradesTableModel(QAbstractTableModel):
    """Read-only view of a list of :class:`engine.models.models.Trade`.

    The underlying list is held by reference; calling
    :py:meth:`set_trades` re-bases the model on a new list with a
    single ``modelReset`` notification (no per-cell signalling).
    """

    # Columns whose sort key is best held as a NumPy float array (O(1)
    # comparison via index — vital for snappy sort on 50k+ trades).
    _NUMERIC_SORT_COLS = (
        COL_ID,
        COL_ENTRY_PRICE,
        COL_EXIT_PRICE,
        COL_PNL_POINTS,
        COL_PNL_DOLLARS,
        COL_VOLUME,
        COL_DURATION,
        COL_MAE,
        COL_MFE,
        COL_MAE_DOLLARS,
        COL_MFE_DOLLARS,
        COL_MAE_PCT,
        COL_MFE_PCT,
        COL_RR,
    )

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._trades: List[Any] = []
        # Phase 6.2 perf caches — populated lazily inside ``set_trades``.
        # Sort comparisons read directly from these NumPy arrays via
        # :py:meth:`raw_sort_key` so the proxy's ``lessThan`` avoids the
        # ~1 µs/call ``data(role=RawValueRole)`` round trip; on 50k
        # trades that drops a "Sort by PnL" click from ~9.6 s to <0.5 s.
        self._numeric_keys: Dict[int, np.ndarray] = {}
        self._string_keys: Dict[int, List[str]] = {}
        self._datetime_keys: Dict[int, np.ndarray] = {}
        self._search_blobs: List[str] = []
        # Phase 2 EPIC 2.3 — anomaly cache (populated by _build_caches).
        self._anomaly_kind: np.ndarray = np.zeros(0, dtype=np.int8)
        self._anomaly_text: List[str] = []
        self._refresh_theme_colors()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_trades(self, trades: List[Any]) -> None:
        """Replace the underlying list with ``trades``. Triggers exactly
        one ``modelReset`` so the proxy / view re-sort once.

        Phase 6.2 — also precomputes the per-column sort-key arrays and
        the lowercased search blobs. This is one O(N) walk over the
        trade list; subsequent sort/filter operations are O(N log N) on
        primitives instead of O(N log N) of Python attribute access.
        """
        self.beginResetModel()
        self._trades = list(trades)
        self._build_caches()
        self.endResetModel()

    def append_trades(self, new_trades: List[Any]) -> None:
        """Append trades without a full model reset. Emits rowsInserted.
        Skips full cache rebuild — only appends to caches.
        """
        if not new_trades:
            return
        old_n = len(self._trades)
        self.beginInsertRows(QModelIndex(), old_n, old_n + len(new_trades) - 1)
        self._trades.extend(new_trades)
        self._extend_caches(new_trades)
        self.endInsertRows()

    def _extend_caches(self, new_trades: List[Any]) -> None:
        if not new_trades:
            return
        n = len(new_trades)
        ts = new_trades
        def _arr(getter):
            return np.fromiter(getter, dtype=float, count=n)
        for col, arr in self._numeric_keys.items():
            if col == COL_ID:
                ext = _arr(_safe_int(t, "id") for t in ts)
            elif col == COL_ENTRY_PRICE:
                ext = _arr(_safe_float(t, "entry_price") for t in ts)
            elif col == COL_EXIT_PRICE:
                ext = _arr(_safe_float(t, "exit_price") for t in ts)
            elif col == COL_PNL_POINTS:
                ext = _arr(_safe_float(t, "pnl_points") for t in ts)
            elif col == COL_PNL_DOLLARS:
                ext = _arr(_safe_float(t, "pnl") for t in ts)
            elif col == COL_VOLUME:
                ext = _arr(_safe_float(t, "volume") for t in ts)
            elif col == COL_DURATION:
                ext = _arr(_safe_int(t, "duration_minutes") for t in ts)
            elif col == COL_MAE:
                ext = _arr(_safe_float(t, "mae") for t in ts)
            elif col == COL_MFE:
                ext = _arr(_safe_float(t, "mfe") for t in ts)
            elif col == COL_MAE_DOLLARS:
                ext = _arr(_safe_float(t, "mae_dollars") for t in ts)
            elif col == COL_MFE_DOLLARS:
                ext = _arr(_safe_float(t, "mfe_dollars") for t in ts)
            elif col == COL_MAE_PCT:
                ext = _arr(self._mae_pct(t) for t in ts)
            elif col == COL_MFE_PCT:
                ext = _arr(self._mfe_pct(t) for t in ts)
            elif col == COL_RR:
                ext = _arr(_calc_rr(t) for t in ts)
            else:
                continue
            self._numeric_keys[col] = np.concatenate([arr, ext])
        self._string_keys[COL_DIRECTION].extend(_safe_str(t, "direction").upper() for t in ts)
        self._string_keys[COL_EXIT_REASON].extend(_safe_str(t, "exit_reason") for t in ts)
        self._string_keys[COL_SESSION].extend(_safe_str(t, "session") for t in ts)
        et_np = [getattr(t, "entry_time_np", None) for t in ts]
        xt_np = [getattr(t, "exit_time_np", None) for t in ts]
        for key, items in [(COL_ENTRY_TIME, et_np), (COL_EXIT_TIME, xt_np)]:
            arr = self._datetime_keys[key]
            ext = np.empty(len(items), dtype="datetime64[ns]")
            for i, v in enumerate(items):
                ext[i] = v if v is not None else np.datetime64("NaT")
            self._datetime_keys[key] = np.concatenate([arr, ext])
        self._search_blobs.extend(
            (
                f"{_safe_str(t, 'id')} {_safe_str(t, 'direction')} "
                f"{_safe_str(t, 'exit_reason')} {_safe_str(t, 'session')} "
                f"{_safe_str(t, 'entry_time')} {_safe_str(t, 'exit_time')}"
            ).lower()
            for t in ts
        )
        if hasattr(self, "_drill_hour"):
            try:
                et_np_arr = None
                if et_np and all(v is not None for v in et_np):
                    et_np_arr = np.asarray(et_np, dtype="datetime64[ns]")
                if et_np_arr is None:
                    et_np_arr = pd.to_datetime([t.entry_time for t in ts]).to_numpy(dtype="datetime64[ns]")
                dt = pd.DatetimeIndex(et_np_arr)
                self._drill_hour = np.concatenate([self._drill_hour, dt.hour.to_numpy(dtype=np.int8)])
                self._drill_weekday = np.concatenate([self._drill_weekday, dt.weekday.to_numpy(dtype=np.int8)])
                self._drill_year = np.concatenate([self._drill_year, dt.year.to_numpy(dtype=np.int16)])
                self._drill_month = np.concatenate([self._drill_month, dt.month.to_numpy(dtype=np.int8)])
            except Exception:
                pad_n = len(ts)
                self._drill_hour = np.concatenate([self._drill_hour, np.zeros(pad_n, dtype=np.int8)])
                self._drill_weekday = np.concatenate([self._drill_weekday, np.zeros(pad_n, dtype=np.int8)])
                self._drill_year = np.concatenate([self._drill_year, np.zeros(pad_n, dtype=np.int16)])
                self._drill_month = np.concatenate([self._drill_month, np.zeros(pad_n, dtype=np.int8)])
        dir_ext = [_safe_str(t, "direction").upper() for t in ts]
        if dir_ext:
            dir_arr = np.asarray(dir_ext, dtype=object)
            self._is_long = np.concatenate([self._is_long, dir_arr == "LONG"])
            self._is_short = np.concatenate([self._is_short, dir_arr == "SHORT"])

        # Phase 1 stabilization — incremental anomaly pass (AI-10)
        self._compute_anomalies(start_idx=old_n)

    # ------------------------------------------------------------------
    # Phase 6.2 — sort-key + search-blob caches
    # ------------------------------------------------------------------
    def _build_caches(self) -> None:
        n = len(self._trades)
        if n == 0:
            self._numeric_keys = {}
            self._string_keys = {}
            self._datetime_keys = {}
            self._search_blobs = []
            return

        ts = self._trades

        # Numeric columns — one NumPy float array per sortable column.
        # Use list comprehensions over the trade list rather than
        # ``np.fromiter`` because fromiter can't guard against missing
        # attrs the same way ``getattr(..., default)`` can.
        def _arr(getter):
            return np.fromiter(getter, dtype=float, count=n)

        self._numeric_keys = {
            COL_ID: _arr(_safe_int(t, "id") for t in ts),
            COL_ENTRY_PRICE: _arr(_safe_float(t, "entry_price") for t in ts),
            COL_EXIT_PRICE: _arr(_safe_float(t, "exit_price") for t in ts),
            COL_PNL_POINTS: _arr(_safe_float(t, "pnl_points") for t in ts),
            COL_PNL_DOLLARS: _arr(_safe_float(t, "pnl") for t in ts),
            COL_VOLUME: _arr(_safe_float(t, "volume") for t in ts),
            COL_DURATION: _arr(_safe_int(t, "duration_minutes") for t in ts),
            COL_MAE: _arr(_safe_float(t, "mae") for t in ts),
            COL_MFE: _arr(_safe_float(t, "mfe") for t in ts),
            COL_MAE_DOLLARS: _arr(_safe_float(t, "mae_dollars") for t in ts),
            COL_MFE_DOLLARS: _arr(_safe_float(t, "mfe_dollars") for t in ts),
            COL_MAE_PCT: _arr(self._mae_pct(t) for t in ts),
            COL_MFE_PCT: _arr(self._mfe_pct(t) for t in ts),
            COL_RR: _arr(_calc_rr(t) for t in ts),
        }

        # String columns
        self._string_keys = {
            COL_DIRECTION: [_safe_str(t, "direction").upper() for t in ts],
            COL_EXIT_REASON: [_safe_str(t, "exit_reason") for t in ts],
            COL_SESSION: [_safe_str(t, "session") for t in ts],
        }

        # Datetime columns — use partial availability (NaT for missing).
        et_np = [getattr(t, "entry_time_np", None) for t in ts]
        xt_np = [getattr(t, "exit_time_np", None) for t in ts]

        def _to_dt64_or_nat(items):
            arr = np.empty(len(items), dtype="datetime64[ns]")
            for i, v in enumerate(items):
                arr[i] = v if v is not None else np.datetime64("NaT")
            return arr

        self._datetime_keys[COL_ENTRY_TIME] = _to_dt64_or_nat(et_np)
        self._datetime_keys[COL_EXIT_TIME] = _to_dt64_or_nat(xt_np)

        # Lowercased search blob per row — includes entry/exit time strings.
        self._search_blobs = [
            (
                f"{_safe_str(t, 'id')} {_safe_str(t, 'direction')} "
                f"{_safe_str(t, 'exit_reason')} {_safe_str(t, 'session')} "
                f"{_safe_str(t, 'entry_time')} {_safe_str(t, 'exit_time')}"
            ).lower()
            for t in ts
        ]

        # Phase 6.3 — drill-down cache arrays for heatmap filtering
        # Entry hour (0-23), weekday (0=Mon..6=Sun), year, month
        try:
            et_np_arr = None
            if et_np:
                try:
                    et_np_arr = np.asarray(et_np, dtype="datetime64[ns]")
                except Exception:
                    et_np_arr = None
            if et_np_arr is None or not np.any(et_np_arr):
                et_np_arr = pd.to_datetime([t.entry_time for t in ts]).to_numpy(dtype="datetime64[ns]")

            dt = pd.DatetimeIndex(et_np_arr)
            self._drill_hour = dt.hour.to_numpy(dtype=np.int8)
            self._drill_weekday = dt.weekday.to_numpy(dtype=np.int8)
            self._drill_year = dt.year.to_numpy(dtype=np.int16)
            self._drill_month = dt.month.to_numpy(dtype=np.int8)
        except Exception:
            # Fallback if entry_time can't be parsed
            self._drill_hour = np.zeros(n, dtype=np.int8)
            self._drill_weekday = np.zeros(n, dtype=np.int8)
            self._drill_year = np.zeros(n, dtype=np.int16)
            self._drill_month = np.zeros(n, dtype=np.int8)

        # Phase 6.3 — precompute direction booleans for fast filter mask (🟡16)
        directions = self._string_keys.get(COL_DIRECTION, [])
        if directions:
            dir_arr = np.asarray(directions, dtype=object)
            self._is_long = dir_arr == "LONG"
            self._is_short = dir_arr == "SHORT"
        else:
            self._is_long = np.zeros(n, dtype=bool)
            self._is_short = np.zeros(n, dtype=bool)

        self._compute_anomalies(start_idx=0)

    def _compute_anomalies(self, start_idx: int = 0) -> None:
        """Phase 2 EPIC 2.3 — anomaly stripe detection (AI-10).

        If start_idx > 0, we are performing an incremental pass on new trades.
        """
        n = len(self._trades)
        if n == 0:
            self._anomaly_kind = np.zeros(0, dtype=np.int8)
            self._anomaly_text = []
            return

        if start_idx == 0:
            self._anomaly_kind = np.zeros(n, dtype=np.int8)
            self._anomaly_text = [""] * n
        else:
            # Incremental: extend arrays if needed
            if self._anomaly_kind.size < n:
                ext_n = n - self._anomaly_kind.size
                self._anomaly_kind = np.concatenate(
                    [self._anomaly_kind, np.zeros(ext_n, dtype=np.int8)]
                )
                self._anomaly_text.extend([""] * ext_n)

        try:
            pnl_arr = self._numeric_keys[COL_PNL_DOLLARS]
            dur_arr = self._numeric_keys[COL_DURATION]

            # Re-calculate thresholds on the full set for accuracy
            abs_pnl = np.abs(pnl_arr)
            pnl_sd = float(abs_pnl.std(ddof=0))
            dur_sd = float(dur_arr.std(ddof=0))
            pnl_thr = float(abs_pnl.mean() + 3.0 * pnl_sd) if pnl_sd > 0 else np.inf
            dur_thr = float(dur_arr.mean() + 3.0 * dur_sd) if dur_sd > 0 else np.inf

            pnl_mask = (pnl_sd > 0) & (abs_pnl > pnl_thr)
            dur_mask = (dur_sd > 0) & (dur_arr > dur_thr) & ~pnl_mask
            win_mask = pnl_mask & (pnl_arr > 0)
            loss_mask = pnl_mask & (pnl_arr <= 0)

            # Evaluate only the new slice [start_idx:]
            idx_to_check = np.arange(start_idx, n)
            self._anomaly_kind[idx_to_check] = 0
            self._anomaly_kind[idx_to_check[win_mask[idx_to_check]]] = 2
            self._anomaly_kind[idx_to_check[loss_mask[idx_to_check]]] = 3
            self._anomaly_kind[idx_to_check[dur_mask[idx_to_check]]] = 1

            for i in idx_to_check:
                if win_mask[i]:
                    self._anomaly_text[i] = (
                        f"Outlier win: ${pnl_arr[i]:+,.0f} (> μ+3σ = ${pnl_thr:,.0f})."
                    )
                elif loss_mask[i]:
                    self._anomaly_text[i] = (
                        f"Outlier loss: ${pnl_arr[i]:+,.0f} "
                        f"(> μ+3σ = ${pnl_thr:,.0f} in magnitude)."
                    )
                elif dur_mask[i]:
                    self._anomaly_text[i] = (
                        f"Outlier duration: {int(dur_arr[i])} min "
                        f"(> μ+3σ = {dur_thr:,.0f} min)."
                    )
        except Exception:
            pass

    def search_blob(self, source_row: int) -> str:
        """Return the precomputed lowercased search blob for ``source_row``."""
        if 0 <= source_row < len(self._search_blobs):
            return self._search_blobs[source_row]
        return ""

    def numeric_sort_key(self, source_row: int, col: int) -> Optional[float]:
        """O(1) numeric sort-key lookup. Returns ``None`` if the column
        is not in the numeric cache."""
        arr = self._numeric_keys.get(col)
        if arr is None or not (0 <= source_row < arr.size):
            return None
        return float(arr[source_row])

    def string_sort_key(self, source_row: int, col: int) -> Optional[str]:
        keys = self._string_keys.get(col)
        if keys is None or not (0 <= source_row < len(keys)):
            return None
        return keys[source_row]

    def datetime_sort_key(self, source_row: int, col: int) -> Any:
        arr = self._datetime_keys.get(col)
        if arr is None or not (0 <= source_row < arr.size):
            return None
        return arr[source_row]

    def trade_at(self, source_row: int) -> Optional[Any]:
        """Return the :class:`Trade` for the given *source* row index
        (i.e. before any proxy filtering / sorting). Used by the view
        to map ``currentChanged`` selections back to a Trade object."""
        if 0 <= source_row < len(self._trades):
            return self._trades[source_row]
        return None

    def refresh_theme(self) -> None:
        """Recompute cached :class:`QColor` instances after a theme
        switch and emit ``dataChanged`` for the entire visible range
        so the new colours apply without rebuilding the view."""
        self._refresh_theme_colors()
        if self.rowCount() > 0:
            top = self.index(0, 0)
            bot = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bot, [Qt.ItemDataRole.ForegroundRole])

    # ------------------------------------------------------------------
    # QAbstractTableModel implementation
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._trades)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(COLUMNS):
                return COLUMNS[section]
            if role == Qt.ItemDataRole.ToolTipRole and 0 <= section < len(COLUMNS):
                return COLUMN_TOOLTIPS.get(COLUMNS[section], "")
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._trades)):
            return None
        col = index.column()
        trade = self._trades[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(trade, col)
        if role == RawValueRole:
            return self._raw(trade, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground(trade, col)
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._background_or_anomaly(row, trade, col)
        if role == Qt.ItemDataRole.ToolTipRole:
            # Phase 2 EPIC 2.3 — surface the outlier reason on the COL_ID cell.
            if col == COL_ID and 0 <= row < len(self._anomaly_text):
                tip = self._anomaly_text[row]
                if tip:
                    return tip
            # Cell-level tooltip mirrors the header tooltip — handy for
            # narrow columns that get elided.
            return COLUMN_TOOLTIPS.get(COLUMNS[col], "")
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _refresh_theme_colors(self) -> None:
        t = get_theme()
        self._c_long = QColor(sh.c(t, "accent_light"))
        self._c_short = QColor(sh.c(t, "amber"))
        self._c_green = QColor(sh.c(t, "green_bright"))
        self._c_red = QColor(sh.c(t, "red_bright"))
        self._c_amber = QColor(sh.c(t, "amber"))
        self._c_text3 = QColor(sh.c(t, "text3"))
        # Phase 6.3 — semi-transparent background colors for badges (alpha=38, ~15%)
        self._bg_long = QColor(sh.c(t, "accent_light"))
        self._bg_long.setAlpha(38)
        self._bg_short = QColor(sh.c(t, "amber"))
        self._bg_short.setAlpha(38)
        self._bg_green = QColor(sh.c(t, "green_bright"))
        self._bg_green.setAlpha(38)
        self._bg_red = QColor(sh.c(t, "red_bright"))
        self._bg_red.setAlpha(38)
        self._bg_amber = QColor(sh.c(t, "amber"))
        self._bg_amber.setAlpha(38)
        # Phase 2 EPIC 2.3 — anomaly stripe: stronger alpha so the column
        # reads as a 4-px-wide marker even on busy rows.
        self._bg_anomaly_green = QColor(sh.c(t, "green_bright"))
        self._bg_anomaly_green.setAlpha(120)
        self._bg_anomaly_red = QColor(sh.c(t, "red_bright"))
        self._bg_anomaly_red.setAlpha(120)
        self._bg_anomaly_amber = QColor(sh.c(t, "amber"))
        self._bg_anomaly_amber.setAlpha(120)

    @staticmethod
    def _mae_pct(trade: Any) -> float:
        ep = _safe_float(trade, "entry_price")
        mae = _safe_float(trade, "mae")
        return (mae / ep * 100) if ep > 0 else 0.0

    @staticmethod
    def _mfe_pct(trade: Any) -> float:
        ep = _safe_float(trade, "entry_price")
        mfe = _safe_float(trade, "mfe")
        return (mfe / ep * 100) if ep > 0 else 0.0

    def _display(self, trade: Any, col: int) -> str:
        """Formatted text for ``Qt.DisplayRole``. Identical layout to the
        legacy ``QTableWidget``-based code so visual diffs are zero."""
        if col == COL_ID:
            return str(trade.id)
        if col == COL_DIRECTION:
            return str(trade.direction).upper()
        if col == COL_ENTRY_TIME:
            return str(trade.entry_time)
        if col == COL_EXIT_TIME:
            return str(trade.exit_time)
        if col == COL_ENTRY_PRICE:
            return f"{trade.entry_price:.2f}"
        if col == COL_EXIT_PRICE:
            return f"{trade.exit_price:.2f}"
        if col == COL_PNL_POINTS:
            return f"{trade.pnl_points:+.2f}"
        if col == COL_PNL_DOLLARS:
            return f"${trade.pnl:+,.2f}"
        if col == COL_VOLUME:
            return f"{trade.volume:.2f}"
        if col == COL_DURATION:
            return _format_duration(int(trade.duration_minutes))
        if col == COL_EXIT_REASON:
            return str(trade.exit_reason)
        if col == COL_MAE:
            return f"{trade.mae:.2f}"
        if col == COL_MFE:
            return f"{trade.mfe:.2f}"
        if col == COL_MAE_DOLLARS:
            return f"${getattr(trade, 'mae_dollars', 0.0):,.2f}"
        if col == COL_MFE_DOLLARS:
            return f"${getattr(trade, 'mfe_dollars', 0.0):,.2f}"
        if col == COL_MAE_PCT:
            return f"{self._mae_pct(trade):.2f}%"
        if col == COL_MFE_PCT:
            return f"{self._mfe_pct(trade):.2f}%"
        if col == COL_SESSION:
            return str(trade.session)
        if col == COL_RR:
            rr = _calc_rr(trade)
            return f"{rr:+.2f}R" if rr != 0 else "N/A"
        return ""

    def _raw(self, trade: Any, col: int) -> Any:
        """Raw-typed value for sorting (Qt sort comparator). Return
        floats / ints / Timestamps — never the formatted strings — so
        that "+\\$10,000.00" sorts numerically rather than
        lexicographically."""
        if col == COL_ID:
            return int(trade.id)
        if col == COL_DIRECTION:
            return str(trade.direction).upper()
        if col == COL_ENTRY_TIME:
            return getattr(trade, "entry_time_np", trade.entry_time)
        if col == COL_EXIT_TIME:
            return getattr(trade, "exit_time_np", trade.exit_time)
        if col == COL_ENTRY_PRICE:
            return float(trade.entry_price)
        if col == COL_EXIT_PRICE:
            return float(trade.exit_price)
        if col == COL_PNL_POINTS:
            return float(trade.pnl_points)
        if col == COL_PNL_DOLLARS:
            return float(trade.pnl)
        if col == COL_VOLUME:
            return float(trade.volume)
        if col == COL_DURATION:
            return int(trade.duration_minutes)
        if col == COL_EXIT_REASON:
            return str(trade.exit_reason)
        if col == COL_MAE:
            return float(trade.mae)
        if col == COL_MFE:
            return float(trade.mfe)
        if col == COL_MAE_DOLLARS:
            return float(getattr(trade, "mae_dollars", 0.0))
        if col == COL_MFE_DOLLARS:
            return float(getattr(trade, "mfe_dollars", 0.0))
        if col == COL_MAE_PCT:
            return float(self._mae_pct(trade))
        if col == COL_MFE_PCT:
            return float(self._mfe_pct(trade))
        if col == COL_SESSION:
            return str(trade.session)
        if col == COL_RR:
            return float(_calc_rr(trade))
        return None

    def _foreground(self, trade: Any, col: int) -> Optional[QColor]:
        """Per-cell text colour. Same palette as the legacy widget."""
        if col == COL_DIRECTION:
            return self._c_long if trade.direction == "long" else self._c_short
        if col in (COL_PNL_POINTS, COL_PNL_DOLLARS):
            if trade.pnl > 0:
                return self._c_green
            if trade.pnl < 0:
                return self._c_red
            return self._c_amber
        if col == COL_EXIT_REASON:
            er = trade.exit_reason
            if er in ("TP", "TP_2RR", "LOCK"):
                return self._c_green
            if er in ("SL", "BE", "GAP_SL"):
                return self._c_red
            return self._c_amber
        if col == COL_MAE_PCT:
            mae_pct = self._mae_pct(trade)
            if mae_pct > 1.0:
                return self._c_red
            if mae_pct > 0.5:
                return self._c_amber
            return self._c_text3
        if col == COL_MFE_PCT:
            mfe_pct = self._mfe_pct(trade)
            if mfe_pct > 1.0:
                return self._c_green
            if mfe_pct > 0.5:
                return self._c_long
            return self._c_text3
        if col == COL_RR:
            rr = _calc_rr(trade)
            if rr == 0:
                return None
            if rr > 1.0:
                return self._c_green
            if rr > 0:
                return self._c_amber
            return self._c_red
        return None

    def _background(self, trade: Any, col: int) -> Optional[QColor]:
        """Phase 6.3 — semi-transparent background badges for Direction, PnL, Exit Reason."""
        if col == COL_DIRECTION:
            return self._bg_long if trade.direction == "long" else self._bg_short
        if col in (COL_PNL_POINTS, COL_PNL_DOLLARS):
            if trade.pnl > 0:
                return self._bg_green
            if trade.pnl < 0:
                return self._bg_red
            return self._bg_amber
        if col == COL_EXIT_REASON:
            er = trade.exit_reason
            if er in ("TP", "TP_2RR", "LOCK"):
                return self._bg_green
            if er in ("SL", "BE", "GAP_SL"):
                return self._bg_red
            return self._bg_amber
        return None

    def _background_or_anomaly(self, row: int, trade: Any, col: int) -> Optional[QColor]:
        """Phase 2 EPIC 2.3 — paint an anomaly stripe on COL_ID when this
        row is an outlier; otherwise fall back to the regular badge.
        """
        if col == COL_ID and 0 <= row < self._anomaly_kind.size:
            kind = int(self._anomaly_kind[row])
            if kind == 1:
                return self._bg_anomaly_amber
            if kind == 2:
                return self._bg_anomaly_green
            if kind == 3:
                return self._bg_anomaly_red
        return self._background(trade, col)


class TradesFilterProxy(QAbstractTableModel):
    """Custom filter+sort proxy that bypasses Qt's quicksort entirely.

    Why not :class:`QSortFilterProxyModel`? On 50 000 trades, Qt's
    quicksort calls our overridden :py:meth:`lessThan` ~830 000 times
    (≈ ``n log n``), each call paying ~7 µs of Python-Qt callback
    dispatch overhead. The result is a ~6 s freeze when the user
    clicks a column header — exactly the kind of "hangs on stress
    workloads" Phase 6 is fixing.

    The replacement is a thin model that holds its own
    ``_visible: ndarray[int64]`` of source rows in display order.
    Filtering and sorting both operate on this array in pure NumPy:

        * Filter change → recompute boolean mask + reapply sort
          permutation → ``modelReset``.
        * Sort change → ``np.argsort`` over the model's precomputed
          per-column key array → ``modelReset``.

    Sort on 50 k trades drops from ~6 s → < 50 ms (≈ 100×). Search /
    type-filter changes also became 30-50× faster because we no
    longer evaluate ``filterAcceptsRow`` per source row in Python.

    Public API is intentionally compatible with the old
    QSortFilterProxyModel-based class: ``setSourceModel``,
    ``mapToSource``, ``set_filter_type``, ``set_search_text``,
    ``set_date_range``, ``sort``.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._source: Optional[TradesTableModel] = None

        # Filter state
        self._filter_type: str = "All"
        self._search_text: str = ""
        self._date_from: Optional[Any] = None
        self._date_to: Optional[Any] = None

        # Phase 6.3 — heatmap drill-down filter attributes
        self._filter_hour: Optional[int] = None
        self._filter_weekday: Optional[int] = None
        self._filter_year: Optional[int] = None
        self._filter_month: Optional[int] = None

        # Sort state
        self._sort_col: int = -1
        self._sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        # Mapping arrays. ``_visible[i]`` is the source row currently at
        # proxy row ``i``; ``_inverse[s]`` is the proxy row of source row
        # ``s`` (or ``-1`` when ``s`` is hidden by the filter).
        self._visible: np.ndarray = np.zeros(0, dtype=np.int64)
        self._inverse: np.ndarray = np.zeros(0, dtype=np.int64)

    # ------------------------------------------------------------------
    # Source-model wiring
    # ------------------------------------------------------------------
    def setSourceModel(self, sourceModel: Any) -> None:  # noqa: N802, N803 (Qt API)
        """Bind the source model and rebuild the visible-rows mapping."""
        if self._source is not None:
            try:
                self._source.modelReset.disconnect(self._on_source_reset)
                self._source.dataChanged.disconnect(self._on_source_data_changed)
            except (RuntimeError, TypeError):
                pass
        self._source = (
            sourceModel if isinstance(sourceModel, TradesTableModel) else None
        )
        if self._source is not None:
            self._source.modelReset.connect(self._on_source_reset)
            self._source.dataChanged.connect(self._on_source_data_changed)
        self._rebuild_mapping()

    def sourceModel(self) -> Optional[TradesTableModel]:  # noqa: N802 (Qt API)
        return self._source

    def _on_source_reset(self) -> None:
        self._rebuild_mapping()

    def _on_source_data_changed(
        self, top_left: Any, bot_right: Any, roles: Any = None
    ) -> None:
        # Forward dataChanged through the proxy after remapping the
        # touched rows to proxy coordinates.
        if self._source is None or self._inverse.size == 0:
            return
        # Conservative: if any visible source row is in the changed
        # range, emit a single dataChanged covering the affected
        # proxy rows. For typical usage (theme refresh) the source
        # emits the full table at once, so we just emit the same.
        if self._visible.size == 0:
            return
        proxy_top_left = self.index(0, 0)
        proxy_bot_right = self.index(self._visible.size - 1, self.columnCount() - 1)
        self.dataChanged.emit(proxy_top_left, proxy_bot_right, roles or [])

    # ------------------------------------------------------------------
    # Filter / sort setters — each triggers exactly one modelReset
    # ------------------------------------------------------------------
    def set_filter_type(self, value: str) -> None:
        if value != self._filter_type:
            self._filter_type = value
            self._rebuild_mapping()

    def set_search_text(self, value: str) -> None:
        v = value.lower()
        if v != self._search_text:
            self._search_text = v
            self._rebuild_mapping()

    def set_date_range(self, d_from: Optional[QDate], d_to: Optional[QDate]) -> None:
        a = pd.Timestamp(d_from.toPython()).to_datetime64() if d_from else None
        if d_to is not None:
            end_ts = (
                pd.Timestamp(d_to.toPython())
                + pd.Timedelta(days=1)
                - pd.Timedelta(nanoseconds=1)
            )
            b = end_ts.to_datetime64()
        else:
            b = None
        self._date_from = a
        self._date_to = b
        self._rebuild_mapping()

    def apply_filters(
        self,
        *,
        filter_type: Optional[str] = None,
        search_text: Optional[str] = None,
        d_from: Optional[QDate] = None,
        d_to: Optional[QDate] = None,
    ) -> None:
        changed = False
        if filter_type is not None and filter_type != self._filter_type:
            self._filter_type = filter_type
            changed = True
        if search_text is not None:
            v = search_text.lower()
            if v != self._search_text:
                self._search_text = v
                changed = True
        d_from_dt: Any = None
        if d_from is not None:
            d_from_dt = pd.Timestamp(d_from.toPython()).to_datetime64()
            if d_from_dt != self._date_from:
                self._date_from = d_from_dt
                changed = True
        d_to_dt: Any = None
        if d_to is not None:
            end_ts = (
                pd.Timestamp(d_to.toPython())
                + pd.Timedelta(days=1)
                - pd.Timedelta(nanoseconds=1)
            )
            d_to_dt = end_ts.to_datetime64()
            if d_to_dt != self._date_to:
                self._date_to = d_to_dt
                changed = True
        if changed:
            self._rebuild_mapping()

    def apply_all_filters(
        self,
        *,
        filter_type: Optional[str] = None,
        search_text: Optional[str] = None,
        d_from: Optional[QDate] = None,
        d_to: Optional[QDate] = None,
        hour: Optional[int] = None,
        weekday: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> None:
        changed = False
        if filter_type is not None and filter_type != self._filter_type:
            self._filter_type = filter_type
            changed = True
        if search_text is not None:
            v = search_text.lower()
            if v != self._search_text:
                self._search_text = v
                changed = True
        d_from_dt: Any = None
        if d_from is not None:
            d_from_dt = pd.Timestamp(d_from.toPython()).to_datetime64()
        if d_from_dt != self._date_from:
            self._date_from = d_from_dt
            changed = True
        d_to_dt: Any = None
        if d_to is not None:
            end_ts = (
                pd.Timestamp(d_to.toPython())
                + pd.Timedelta(days=1)
                - pd.Timedelta(nanoseconds=1)
            )
            d_to_dt = end_ts.to_datetime64()
        if d_to_dt != self._date_to:
            self._date_to = d_to_dt
            changed = True
        if hour != self._filter_hour:
            self._filter_hour = hour
            changed = True
        if weekday != self._filter_weekday:
            self._filter_weekday = weekday
            changed = True
        if year != self._filter_year:
            self._filter_year = year
            changed = True
        if month != self._filter_month:
            self._filter_month = month
            changed = True
        if changed:
            self._rebuild_mapping()

    # ------------------------------------------------------------------
    # Phase 6.3 — heatmap drill-down filter setters
    # ------------------------------------------------------------------
    def set_filter_hour(self, hour: Optional[int]) -> None:
        if hour != self._filter_hour:
            self._filter_hour = hour
            self._rebuild_mapping()

    def set_filter_weekday(self, weekday: Optional[int]) -> None:
        if weekday != self._filter_weekday:
            self._filter_weekday = weekday
            self._rebuild_mapping()

    def set_filter_month(self, month: Optional[int]) -> None:
        if month != self._filter_month:
            self._filter_month = month
            self._rebuild_mapping()

    def set_filter_year(self, year: Optional[int]) -> None:
        if year != self._filter_year:
            self._filter_year = year
            self._rebuild_mapping()

    # ------------------------------------------------------------------
    # Phase 6.4 — public drill-down API (one rebuild per change set)
    # ------------------------------------------------------------------
    def apply_drilldown(
        self,
        *,
        hour: Optional[int] = None,
        weekday: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> None:
        """Apply heatmap drill-down filters in one shot.

        Avoids direct external mutation of private attributes and ensures
        exactly one `_rebuild_mapping()` call.
        """
        changed = (
            hour != self._filter_hour
            or weekday != self._filter_weekday
            or year != self._filter_year
            or month != self._filter_month
        )
        self._filter_hour = hour
        self._filter_weekday = weekday
        self._filter_year = year
        self._filter_month = month
        if changed:
            self._rebuild_mapping()

    def clear_drilldown(self) -> None:
        """Clear all drill-down filters (hour/weekday/year/month)."""
        self.apply_drilldown(hour=None, weekday=None, year=None, month=None)

    def sort(
        self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
    ) -> None:  # noqa: N802
        """Re-sort the visible rows in pure NumPy.

        ``np.argsort`` is C-level and runs in tens of ms on 50 k items;
        we then emit one ``modelReset`` so the view refreshes once.
        """
        self._sort_col = column
        self._sort_order = order
        self._rebuild_mapping()

    def sortColumn(self) -> int:  # noqa: N802 (Qt API)
        return self._sort_col

    def sortOrder(self) -> Qt.SortOrder:  # noqa: N802 (Qt API)
        return self._sort_order

    # ------------------------------------------------------------------
    # Visible-row mapping (the engine of this proxy)
    # ------------------------------------------------------------------
    def _rebuild_mapping(self) -> None:
        """Recompute :data:`_visible` and :data:`_inverse` from the
        current filter + sort state. ``modelReset`` is the only signal
        the view sees; it handles everything else."""
        self.beginResetModel()
        try:
            src = self._source
            if src is None or src.rowCount() == 0:
                self._visible = np.zeros(0, dtype=np.int64)
                self._inverse = np.zeros(0, dtype=np.int64)
                return
            n = src.rowCount()
            mask = self._compute_filter_mask(n)
            visible = np.flatnonzero(mask).astype(np.int64)
            visible = self._apply_sort(visible)
            inverse = np.full(n, -1, dtype=np.int64)
            inverse[visible] = np.arange(visible.size, dtype=np.int64)
            self._visible = visible
            self._inverse = inverse
        finally:
            self.endResetModel()

    def _compute_filter_mask(self, n: int) -> np.ndarray:
        """Vectorised filter pass — produces a boolean mask of size ``n``.

        Replaces the per-row Python ``filterAcceptsRow`` of the legacy
        proxy. On 50 k trades the difference is ~5 ms vs ~120 ms.
        """
        src = self._source
        assert src is not None
        mask = np.ones(n, dtype=bool)

        ft = self._filter_type
        pnls = src._numeric_keys.get(COL_PNL_DOLLARS)
        rr_values = src._numeric_keys.get(COL_RR)
        durations = src._numeric_keys.get(COL_DURATION)

        # 1. Base filter types (Wins, Losses, BE, Long, Short)
        if ft == "Wins" and pnls is not None:
            mask &= (pnls > 1e-9)
        elif ft == "Losses" and pnls is not None:
            mask &= (pnls < -1e-9)
        elif ft == "BE" and pnls is not None:
            mask &= (np.abs(pnls) <= 1e-9)
        elif ft in ("Long", "Short"):
            target_long = ft == "Long"
            dir_mask = src._is_long if target_long else src._is_short
            if dir_mask is not None and dir_mask.size == n:
                mask &= dir_mask
        # Phase 6.3 — extended filter presets
        elif ft == "Large Wins (>2R)" and rr_values is not None and pnls is not None:
            mask &= (pnls > 0) & (rr_values > 2.0)
        elif ft == "Large Losses (>2R)" and rr_values is not None and pnls is not None:
            mask &= (pnls < 0) & (rr_values < -2.0)
        elif ft == "Long Duration (>1h)" and durations is not None:
            mask &= durations >= 60
        elif ft == "Short Duration (<5m)" and durations is not None:
            mask &= durations < 5

        # 2. Date Filtering (AI-2: Applies to all filter types)
        # This covers both "Custom Date" and global "Period Sync".
        et = src._datetime_keys.get(COL_ENTRY_TIME)
        if et is not None:
            if self._date_from is not None:
                mask &= et >= self._date_from
            if self._date_to is not None:
                mask &= et <= self._date_to
        elif ft == "Custom Date":
            # If we explicitly asked for Custom Date but have no timestamps, reject all.
            mask &= False

        # 3. Heatmap drill-down filters (Phase 6.3)
        if hasattr(src, "_drill_hour") and self._filter_hour is not None:
            mask &= src._drill_hour == self._filter_hour
        if hasattr(src, "_drill_weekday") and self._filter_weekday is not None:
            mask &= src._drill_weekday == self._filter_weekday
        if hasattr(src, "_drill_year") and self._filter_year is not None:
            mask &= src._drill_year == self._filter_year
        if hasattr(src, "_drill_month") and self._filter_month is not None:
            mask &= src._drill_month == self._filter_month

        if self._search_text:
            blobs = src._search_blobs
            needle = self._search_text
            # Phase 1 stabilization — faster search (AI-9)
            # List comprehension is significantly faster than generator in fromiter
            search_mask = np.array([needle in b for b in blobs], dtype=bool)
            if search_mask.size == n:
                mask &= search_mask

        return mask

    def _apply_sort(self, visible: np.ndarray) -> np.ndarray:
        """Reorder ``visible`` (already filtered) by the active sort key."""
        col = self._sort_col
        if col < 0 or visible.size == 0:
            return visible
        src = self._source
        assert src is not None
        keys: Any = None
        if col in src._numeric_keys:
            keys = src._numeric_keys[col]
        elif col in src._string_keys:
            keys = np.asarray(src._string_keys[col], dtype=object)
        elif col in src._datetime_keys:
            keys = src._datetime_keys[col]
        if keys is None:
            return visible
        sub = keys[visible]
        order = np.argsort(sub, kind="stable")
        if self._sort_order == Qt.SortOrder.DescendingOrder:
            order = order[::-1]
        return visible[order]

    # ------------------------------------------------------------------
    # QAbstractTableModel implementation — forwards through _visible
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return int(self._visible.size)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        if self._source is None:
            return len(COLUMNS)
        return self._source.columnCount(parent)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if self._source is None:
            return None
        return self._source.headerData(section, orientation, role)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid() or self._source is None:
            return Qt.ItemFlag.NoItemFlags
        return self._source.flags(self.mapToSource(index))

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or self._source is None:
            return None
        return self._source.data(self.mapToSource(index), role)

    # ------------------------------------------------------------------
    # Source/proxy index mapping
    # ------------------------------------------------------------------
    def mapToSource(self, proxy_index: QModelIndex) -> QModelIndex:  # noqa: N802 (Qt API)
        if not proxy_index.isValid() or self._source is None:
            return QModelIndex()
        row = proxy_index.row()
        if not (0 <= row < self._visible.size):
            return QModelIndex()
        return self._source.index(int(self._visible[row]), proxy_index.column())

    def mapFromSource(self, source_index: QModelIndex) -> QModelIndex:  # noqa: N802 (Qt API)
        if not source_index.isValid() or self._source is None:
            return QModelIndex()
        src_row = source_index.row()
        if not (0 <= src_row < self._inverse.size):
            return QModelIndex()
        proxy_row = int(self._inverse[src_row])
        if proxy_row < 0:
            return QModelIndex()
        return self.index(proxy_row, source_index.column())
