"""Tests for engine.analytics.trade_clusters (Phase 4.3)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.analytics.trade_clusters import (
    FEATURE_NAMES,
    build_feature_matrix,
    cluster_trades,
    kmeans,
)


@dataclass
class _Trade:
    id: int
    entry_bar_index: int = 0
    exit_bar_index: int = 10
    session: str = "london"
    mae_r: float = -0.4
    mfe_r: float = 0.8
    exit_reason: str = "TP"
    pnl: float = 50.0


def _make_trades(n: int, pattern: str = "mixed"):
    """Generate ``n`` trades with intentional sub-populations.

    ``pattern == "mixed"`` produces three distinct clusters by session +
    duration so k-means has actual structure to find.
    """
    rng = np.random.default_rng(0)
    trades = []
    for i in range(n):
        if i % 3 == 0:
            t = _Trade(
                id=i + 1,
                entry_bar_index=i,
                exit_bar_index=i + 5,
                session="london",
                mae_r=-0.2 + rng.normal(0, 0.05),
                mfe_r=0.5 + rng.normal(0, 0.05),
                exit_reason="TP",
                pnl=80 + rng.normal(0, 5),
            )
        elif i % 3 == 1:
            t = _Trade(
                id=i + 1,
                entry_bar_index=i,
                exit_bar_index=i + 25,
                session="ny",
                mae_r=-0.9 + rng.normal(0, 0.05),
                mfe_r=0.2 + rng.normal(0, 0.05),
                exit_reason="SL",
                pnl=-100 + rng.normal(0, 5),
            )
        else:
            t = _Trade(
                id=i + 1,
                entry_bar_index=i,
                exit_bar_index=i + 60,
                session="asia",
                mae_r=-0.5 + rng.normal(0, 0.05),
                mfe_r=1.5 + rng.normal(0, 0.05),
                exit_reason="EOD",
                pnl=200 + rng.normal(0, 5),
            )
        trades.append(t)
    return trades


def test_build_feature_matrix_shape():
    trades = _make_trades(15)
    X, ids = build_feature_matrix(trades)
    assert X.shape == (15, len(FEATURE_NAMES))
    assert len(ids) == 15


def test_build_feature_matrix_handles_empty():
    X, ids = build_feature_matrix([])
    assert X.shape == (0, len(FEATURE_NAMES))
    assert ids == []


def test_kmeans_returns_correct_shapes():
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, size=(50, 6))
    labels, centroids, wcss = kmeans(X, k=3, seed=0)
    assert labels.shape == (50,)
    assert centroids.shape == (3, 6)
    assert wcss >= 0
    assert set(labels.tolist()).issubset({0, 1, 2})


def test_cluster_trades_returns_none_below_kmin():
    """Two trades < k_min=3, so clustering must skip gracefully."""
    trades = _make_trades(2)
    assert cluster_trades(trades) is None


def test_cluster_trades_finds_three_groups():
    trades = _make_trades(30)
    res = cluster_trades(trades, k_min=3, k_max=7)
    assert res is not None
    assert 3 <= res.k <= 7
    # Per-cluster trade counts must sum to total.
    assert sum(r.n_trades for r in res.rows) == 30


def test_cluster_projection_is_2d():
    trades = _make_trades(20)
    res = cluster_trades(trades)
    assert res is not None
    assert res.projection is not None
    assert res.projection.shape == (20, 2)


def test_cluster_silhouette_in_valid_range():
    trades = _make_trades(30)
    res = cluster_trades(trades)
    assert res is not None
    assert -1.0 <= res.silhouette <= 1.0


def test_cluster_representative_id_is_a_real_trade():
    trades = _make_trades(15)
    res = cluster_trades(trades)
    assert res is not None
    valid_ids = {t.id for t in trades}
    rep_ids = {row.representative_trade_id for row in res.rows if row.n_trades > 0}
    assert rep_ids.issubset(valid_ids)
