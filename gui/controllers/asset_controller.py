"""Sprint 3.5 — Asset combo + Custom-Asset dialog plumbing.

Owns the asset selector that lives at the left edge of the chart
toolbar. ``MainWindow`` keeps thin public methods (``on_asset_changed``,
``_reload_asset_list``, ``on_add_custom_asset``) that delegate here so
existing wiring (signals, settings dump) doesn't need to change.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from engine.asset_registry import list_asset_names, save_asset

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.asset")


class AssetController:
    """Owns the asset combobox population and Custom-Asset dialog flow."""

    def __init__(self, host: MainWindow) -> None:
        self._host = host

    def on_asset_changed(self, name: str) -> None:
        if not name:
            return
        h = self._host
        h.engine.load_asset_config(name)
        h._settings_ctrl.save("asset", name)

    def reload_asset_list(self, *, initial: bool = False, select: str = "") -> None:
        """Repopulate the asset combo from ``config/assets.json``.

        ``initial`` skips signal handling during construction; ``select``
        lets callers (e.g. the Custom Asset dialog) jump to the freshly
        added entry.
        """
        h = self._host
        names = list_asset_names()
        if not names:
            # Defensive fallback so the UI is never empty if assets.json
            # is missing or unreadable.
            names = ["DE40"]
        keep = select or h.asset_cb.currentText() or ("DE40" if "DE40" in names else names[0])
        h.asset_cb.blockSignals(True)
        h.asset_cb.clear()
        h.asset_cb.addItems(names)
        if keep in names:
            h.asset_cb.setCurrentText(keep)
        h.asset_cb.blockSignals(False)
        if not initial and keep != h.asset_cb.currentText():
            self.on_asset_changed(h.asset_cb.currentText())

    def on_add_custom_asset(self) -> None:
        """Open the Custom Asset dialog and persist any newly defined symbol."""
        from gui.dialogs.custom_asset_dialog import CustomAssetDialog

        h = self._host
        dlg = CustomAssetDialog(existing=set(list_asset_names()), parent=h)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        name, cfg = dlg.result()
        try:
            save_asset(name, cfg)
        except OSError as e:
            QMessageBox.warning(h, "Save failed", f"Could not write assets.json:\n{e}")
            return
        self.reload_asset_list(select=name)
        h.asset_cb.setCurrentText(name)
        h.engine.load_asset_config(name)
        h._settings_ctrl.save("asset", name)
        logger.info("Custom asset %s added and selected.", name)
