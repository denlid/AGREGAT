"""
Simple reusable QAbstractTableModel for small-to-medium tables.

Used to standardize Analytics/Quant tables on Model/View.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PySide6.QtGui import QColor


CellStyleFn = Callable[[int, int, Any], dict]


class SimpleTableModel(QAbstractTableModel):
    def __init__(
        self,
        headers: List[str],
        rows: Optional[List[List[Any]]] = None,
        *,
        align_center: bool = True,
        cell_style_fn: Optional[CellStyleFn] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._headers = list(headers)
        self._rows: List[List[Any]] = list(rows or [])
        self._align_center = bool(align_center)
        self._cell_style_fn = cell_style_fn

    def set_rows(self, rows: List[List[Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if not index.isValid():
            return None
        r = index.row()
        c = index.column()
        if not (0 <= r < len(self._rows)) or not (0 <= c < len(self._headers)):
            return None
        v = self._rows[r][c] if c < len(self._rows[r]) else ""

        if role == Qt.ItemDataRole.DisplayRole:
            return str(v)
        if role == Qt.ItemDataRole.TextAlignmentRole and self._align_center:
            return int(Qt.AlignmentFlag.AlignCenter)

        if self._cell_style_fn is not None:
            st = self._cell_style_fn(r, c, v) or {}
            if role == Qt.ItemDataRole.ForegroundRole and "fg" in st:
                return QColor(st["fg"])
            if role == Qt.ItemDataRole.BackgroundRole and "bg" in st:
                return QColor(st["bg"])
        return None

