"""Walk-forward split generation + driver."""

from __future__ import annotations

import numpy as np
import pytest

from engine.optimization.walk_forward import make_splits, walk_forward


def test_rolling_splits_match_expected_count():
    splits = make_splits(100, is_size=40, oos_size=10, mode="rolling")
    # First IS=[0,40), OOS=[40,50). Then advances by 10. Last: IS=[50,90), OOS=[90,100).
    assert len(splits) == 6
    assert splits[0].is_start == 0 and splits[0].is_end == 40
    assert splits[0].oos_start == 40 and splits[0].oos_end == 50
    assert splits[-1].oos_end == 100


def test_anchored_splits_grow_is_window():
    splits = make_splits(100, is_size=40, oos_size=10, mode="anchored")
    assert splits[0].is_start == 0 and splits[0].is_end == 40
    assert splits[1].is_start == 0 and splits[1].is_end == 50
    # Each step grows IS by oos_size.
    for i in range(1, len(splits)):
        assert splits[i].is_end == splits[i - 1].is_end + 10


def test_anchored_oos_windows_disjoint():
    splits = make_splits(80, is_size=20, oos_size=10, mode="anchored")
    for a, b in zip(splits, splits[1:]):
        assert a.oos_end == b.oos_start


def test_no_splits_when_data_too_short():
    assert make_splits(10, is_size=20, oos_size=5) == []
    assert make_splits(0, is_size=10, oos_size=5) == []


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        make_splits(100, is_size=10, oos_size=5, mode="bogus")


def test_walk_forward_drives_run_window_per_split():
    calls = []

    def run_window(is_range, oos_range):
        calls.append((is_range, oos_range))
        is_metrics = {"profit_factor": 1.5}
        oos_metrics = {"profit_factor": 1.2}
        oos_pnls = np.ones(oos_range[1] - oos_range[0])
        return is_metrics, oos_metrics, oos_pnls

    res = walk_forward(50, run_window, is_size=20, oos_size=10, mode="rolling")
    assert len(res.windows) == len(calls)
    assert all(w.is_metrics["profit_factor"] == 1.5 for w in res.windows)


def test_oos_concat_pnls_combines_all_windows():
    def run_window(is_range, oos_range):
        n = oos_range[1] - oos_range[0]
        return {"pf": 1.0}, {"pf": 0.9}, np.full(n, 5.0)

    res = walk_forward(50, run_window, is_size=20, oos_size=10, mode="rolling")
    expected_total = sum(w.split.oos_length for w in res.windows)
    assert res.oos_concat_pnls.size == expected_total
    assert (res.oos_concat_pnls == 5.0).all()


def test_oos_summary_aggregates_metric():
    def run_window(is_range, oos_range):
        is_pf = 2.0
        oos_pf = 1.5 if oos_range[0] < 30 else 0.5
        return {"pf": is_pf}, {"pf": oos_pf}, np.array([1.0])

    res = walk_forward(50, run_window, is_size=20, oos_size=10, mode="rolling")
    summary = res.oos_summary("pf")
    assert summary["count"] == len(res.windows)
    assert summary["min"] == 0.5
    assert summary["max"] == 1.5


def test_is_oos_correlation_perfect_when_proportional():
    """IS / OOS metrics co-vary linearly → corr = 1."""
    state = {"i": 0}

    def run_window(is_range, oos_range):
        state["i"] += 1
        return {"pf": float(state["i"])}, {"pf": float(state["i"]) * 2.0}, np.array([1.0])

    res = walk_forward(50, run_window, is_size=20, oos_size=10, mode="rolling")
    if len(res.windows) >= 2:
        corr = res.is_oos_correlation("pf")
        assert abs(corr - 1.0) < 1e-9
