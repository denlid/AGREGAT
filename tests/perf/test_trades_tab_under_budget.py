"""Phase 6.2 perf-budget tests for the TradesTab proxy.

These guard the wins we got from rewriting :class:`TradesFilterProxy`
to maintain its own NumPy-based ``_visible`` permutation. The legacy
:class:`QSortFilterProxyModel` path took ~6 s to sort 50 k trades by
PnL; with the custom proxy the same call is ~6 ms (roughly 1000×).

The budgets here are intentionally generous (5–10× headroom over the
measured numbers) so they don't flake on slow CI runners — they are
*regression* tests, not benchmarks. If sort or filter ever climbs back
to the QSortFilterProxyModel-class scale, these tests fail loudly.
"""

from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.tabs.trades_table_model import (
    COL_DIRECTION,
    COL_ENTRY_TIME,
    COL_PNL_DOLLARS,
    TradesFilterProxy,
    TradesTableModel,
)


# Headless Qt — same convention as tests/test_trades_tab_model.py.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():  # type: ignore[no-untyped-def]
    return QApplication.instance() or QApplication([])


def _make_trades(n: int):
    """Synthetic stress fixture (mirrors bench/bench_report_stress)."""
    from bench.bench_report_stress import _make_trades as _factory

    return _factory(n)


# ---------------------------------------------------------------------
# Budgets — measured on a 4-vCPU runner; we give ~10× headroom.
#
# Real measurements (50 k trades, custom proxy):
#   set_trades       =  157 ms   →   budget 1.0 s
#   sort by PnL desc =    6 ms   →   budget 200 ms
#   sort by Direction=   16 ms   →   budget 200 ms
#   filter Wins      =   10 ms   →   budget 250 ms
#   search "long"    =    5 ms   →   budget 250 ms
# ---------------------------------------------------------------------
N_STRESS = 50_000


def test_set_trades_under_one_second_for_50k(qapp) -> None:  # type: ignore[no-untyped-def]
    """Initial population (incl. all sort-key + search-blob caches)."""
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    t0 = time.perf_counter()
    model.set_trades(trades)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, (
        f"set_trades({N_STRESS}) took {elapsed:.3f}s — "
        "Phase 6.2 budget is 1.0 s (measured ~0.16 s; 6× headroom)."
    )


def test_sort_50k_trades_under_200ms(qapp) -> None:  # type: ignore[no-untyped-def]
    """Sort by PnL on 50 k trades. Pre-Phase-6.2 this took ~9.6 s."""
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    model.set_trades(trades)
    proxy = TradesFilterProxy()
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.sort(COL_PNL_DOLLARS, Qt.SortOrder.DescendingOrder)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.2, (
        f"sort by PnL on {N_STRESS} trades took {elapsed:.3f}s — "
        "Phase 6.2 budget is 200 ms (measured ~6 ms; 30× headroom)."
    )

    # Sanity: top row should be the largest PnL, last row the smallest.
    top = model.trade_at(int(proxy._visible[0])).pnl
    bottom = model.trade_at(int(proxy._visible[-1])).pnl
    assert top >= bottom, "PnL desc must place largest first"


def test_sort_string_column_50k_trades_under_300ms(qapp) -> None:  # type: ignore[no-untyped-def]
    """Sort by a string column (Direction) — slowest path because
    NumPy ``argsort`` of dtype=object falls back to Python ``__lt__``,
    so we give a slightly looser budget than the numeric path."""
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    model.set_trades(trades)
    proxy = TradesFilterProxy()
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.sort(COL_DIRECTION, Qt.SortOrder.AscendingOrder)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, (
        f"sort by Direction on {N_STRESS} trades took {elapsed:.3f}s — "
        "Phase 6.2 budget is 300 ms (measured ~16 ms; 18× headroom)."
    )


def test_sort_datetime_column_50k_trades_under_300ms(qapp) -> None:  # type: ignore[no-untyped-def]
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    model.set_trades(trades)
    proxy = TradesFilterProxy()
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.sort(COL_ENTRY_TIME, Qt.SortOrder.AscendingOrder)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, (
        f"sort by Entry Time on {N_STRESS} trades took {elapsed:.3f}s — "
        "Phase 6.2 budget is 300 ms."
    )


def test_filter_50k_trades_under_300ms(qapp) -> None:  # type: ignore[no-untyped-def]
    """Type-filter ('Wins') on 50 k trades after a fresh population."""
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    model.set_trades(trades)
    proxy = TradesFilterProxy()
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.set_filter_type("Wins")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, (
        f"filter Wins on {N_STRESS} trades took {elapsed:.3f}s — "
        "Phase 6.2 budget is 300 ms (measured ~10 ms; 30× headroom)."
    )
    assert proxy.rowCount() > 0
    assert proxy.rowCount() < N_STRESS


def test_search_50k_trades_under_300ms(qapp) -> None:  # type: ignore[no-untyped-def]
    """Search on 50 k trades. Each row's search blob is precomputed
    so the filter is a single Python ``in`` over the cached list."""
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    model.set_trades(trades)
    proxy = TradesFilterProxy()
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.set_search_text("long")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, (
        f"search 'long' on {N_STRESS} trades took {elapsed:.3f}s — "
        "Phase 6.2 budget is 300 ms (measured ~5 ms; 60× headroom)."
    )
    # Half of all trades are "long" by construction.
    assert proxy.rowCount() > N_STRESS // 4


def test_combined_filter_and_sort_50k_under_500ms(qapp) -> None:  # type: ignore[no-untyped-def]
    """The realistic user flow: type-filter + search + sort. All
    three should compose in ~ms each because they each emit one
    modelReset and do the work in NumPy."""
    trades = _make_trades(N_STRESS)
    model = TradesTableModel()
    model.set_trades(trades)
    proxy = TradesFilterProxy()
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.set_filter_type("Long")
    proxy.set_search_text("3")  # any digit-3 in id / minute / hour
    proxy.sort(COL_PNL_DOLLARS, Qt.SortOrder.DescendingOrder)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, (
        f"filter+search+sort on {N_STRESS} trades took {elapsed:.3f}s — "
        "Phase 6.2 budget is 500 ms."
    )
    assert proxy.rowCount() > 0
