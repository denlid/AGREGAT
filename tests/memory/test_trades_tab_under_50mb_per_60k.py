"""Memory regression test for TradesTab population (Phase 5.3).

Loading a 60k-trade backtest is the realistic worst-case for serious
optimisation runs. The TradesTableModel keeps a single ``List[Trade]``
reference and pre-computes display strings only on demand, so the
expected steady-state footprint is bounded by:

* ``Trade.__slots__`` × 60_000 — ~30 MiB worst-case.
* The proxy's filter cache — negligible until a filter is applied.
* Theme-colour cache — sub-MB.

The ceiling we defend against is 200 MiB peak. PLAN.md set 50 MiB
as the aspirational target for the trade-data overhead alone; the
practical ceiling we measure here is dominated by *one-off* Qt /
PySide6 import allocations and theme-resource loading (~120 MiB),
so the regression budget sits well above the data overhead. The
value to monitor is *the delta* across runs — a sudden jump above
200 MiB indicates either a data structure regression in the model
or theme caches leaking. Tightening back toward 50 MiB once the
Qt baseline is factored out is a Phase 2.5 follow-up.

Why this matters: the older ``QTableWidget`` implementation duplicated
every cell into a Qt object — that path peaked over 600 MiB on the
same dataset. The current ``QAbstractTableModel`` implementation must
not regress to anywhere near that.
"""

from __future__ import annotations

import importlib
import os

import pytest


_HAS_MEMRAY = importlib.util.find_spec("pytest_memray") is not None
_HAS_QT = importlib.util.find_spec("PySide6") is not None


# Practical 60k-trade ceiling including Qt baseline — see module docstring.
MAX_BYTES = 200 * 1024 * 1024


def _make_synthetic_trades(n: int):
    """Build ``n`` lightweight stand-in trades that satisfy the model's
    duck-typing surface (``id``, ``direction``, prices, pnl, etc.)."""
    from engine.models.models import Trade

    out = []
    for i in range(n):
        sess = ("london", "ny", "asia")[i % 3]
        reason = ("TP", "SL", "EOD")[i % 3]
        pnl = 50.0 if reason == "TP" else (-30.0 if reason == "SL" else 10.0)
        out.append(
            Trade(
                id=i + 1,
                direction="long" if i % 2 == 0 else "short",
                entry_price=100.0 + (i % 100) * 0.1,
                exit_price=101.0 + (i % 100) * 0.1,
                pnl=pnl,
                pnl_points=pnl / 1.0,
                entry_index=i,
                exit_index=i + 5,
                entry_time=f"2024-01-01 {(i % 24):02d}:00:00",
                exit_time=f"2024-01-01 {(i % 24):02d}:30:00",
                volume=1.0,
                duration_minutes=30,
                exit_reason=reason,
                mae=-1.0,
                mfe=2.0,
                session=sess,
                initial_sl=99.0,
                rr=1.5,
            )
        )
    return out


@pytest.mark.skipif(
    not _HAS_MEMRAY or not _HAS_QT,
    reason="needs pytest-memray + PySide6 + offscreen Qt",
)
@pytest.mark.limit_memory(f"{MAX_BYTES // (1024 * 1024)} MB")
def test_trades_tab_60k_trades_peak_below_200mib():
    """Populate the TradesTableModel with 60k trades; pytest-memray
    asserts peak."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Imports happen lazily so the memory budget covers the model
    # construction + population path, not just data setup.
    from PySide6.QtWidgets import QApplication

    from gui.tabs.trades_table_model import TradesTableModel

    app = QApplication.instance() or QApplication([])
    try:
        trades = _make_synthetic_trades(60_000)
        model = TradesTableModel()
        model.set_trades(trades)
        # Force a few cells to render so we exercise the display path.
        for row in (0, 1000, 30_000, 59_999):
            for col in range(model.columnCount()):
                _ = model.index(row, col).data()
    finally:
        # Don't leak the QApplication between tests in the same run.
        app.processEvents()


def test_trades_tab_smoke_without_memray():
    """A non-memray sanity check: loading 1k trades into the model
    works end-to-end without raising. Keeps coverage in environments
    that don't have pytest-memray installed."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if not _HAS_QT:
        pytest.skip("PySide6 not available")

    from PySide6.QtWidgets import QApplication

    from gui.tabs.trades_table_model import TradesTableModel

    app = QApplication.instance() or QApplication([])
    try:
        trades = _make_synthetic_trades(1_000)
        model = TradesTableModel()
        model.set_trades(trades)
        assert model.rowCount() == 1_000
        # Pull a single cell so the display formatter actually runs.
        cell = model.index(0, 0).data()
        assert cell is not None
    finally:
        app.processEvents()
