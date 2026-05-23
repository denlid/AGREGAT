"""GUI helpers around :mod:`engine.report_export`.

A single dialog wrapper that asks the user for an output folder, then calls
``engine.report_export.export_report`` and returns the resulting path. Kept in
the gui layer so the engine module stays Qt-free.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from engine.report_export import export_report


def export_report_dialog(parent: QWidget) -> Optional[str]:
    """Prompt for an output folder, write the report, and return its path.

    Returns ``None`` if the user cancels or the engine has no trades yet.
    """
    engine = getattr(parent, "engine", None)
    if engine is None:
        QMessageBox.warning(parent, "No engine", "No backtest engine available.")
        return None
    if not getattr(engine, "trades", None):
        QMessageBox.information(
            parent,
            "Nothing to export",
            "Run a backtest first — there are no trades to include in the report.",
        )
        return None

    default_dir = os.path.expanduser("~")
    out_dir = QFileDialog.getExistingDirectory(
        parent,
        "Choose folder for the AGREGAT report",
        default_dir,
    )
    if not out_dir:
        return None

    asset = getattr(getattr(engine, "asset_config", None), "name", None)
    strategy_name = type(getattr(engine, "strategy", None) or object()).__name__
    return export_report(
        engine,
        out_dir,
        strategy_name=strategy_name,
        asset_name=asset,
    )
