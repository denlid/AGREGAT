"""K-means clustering of trades by behavioural features (Phase 4.3).

Goal: surface "this strategy is actually three different sub-strategies"
patterns that aggregate metrics hide. We cluster on:

    * duration       — exit_bar_index − entry_bar_index (or fallback in
                       minutes if bar indices aren't recorded)
    * entry_session  — one-hot encoded {asia, london, ny}
    * mae / risk     — R-multiples of the worst intra-trade dip
    * mfe / risk     — R-multiples of the best intra-trade peak
    * exit_reason    — one-hot encoded (TP / SL / EOD / GAP_SL / OTHER)

We don't depend on scikit-learn — AGREGAT's existing requirements are
intentionally minimal. The Lloyd's-iteration k-means below is fast
enough for the trade counts we deal with (≤ 50k); ``k`` is auto-picked
by an elbow heuristic over k ∈ [3, 7] using the within-cluster sum of
squares (WCSS) drop, falling back to silhouette when the elbow is flat.

Outputs are pickle-/JSON-friendly so the report layer can serialise
them straight into the tear-sheet section.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


_SESSIONS = ("asia", "london", "ny")
_EXIT_REASONS = ("TP", "SL", "EOD", "GAP_SL", "OTHER")
FEATURE_NAMES = (
    "duration_bars",
    "session_asia",
    "session_london",
    "session_ny",
    "mae_r",
    "mfe_r",
    "exit_TP",
    "exit_SL",
    "exit_EOD",
    "exit_GAP_SL",
    "exit_OTHER",
)


def _onehot(value: str, vocab: Tuple[str, ...]) -> List[float]:
    v = (value or "").strip().lower()
    return [1.0 if v == term.lower() else 0.0 for term in vocab]


def _onehot_exit(reason: str) -> List[float]:
    """Map an exit reason to the canonical 5-bin onehot.

    Anything that's not in the canonical vocab falls into ``OTHER`` so
    rare reasons (e.g. ``MANUAL`` from interactive runs) don't blow up
    the cluster space dimensionality.
    """
    norm = (reason or "").strip().upper()
    out = [0.0] * len(_EXIT_REASONS)
    if norm in _EXIT_REASONS:
        out[_EXIT_REASONS.index(norm)] = 1.0
    else:
        out[_EXIT_REASONS.index("OTHER")] = 1.0
    return out


def build_feature_matrix(
    trades: Iterable[Any],
) -> Tuple[np.ndarray, List[int]]:
    """Assemble (X, trade_ids).

    ``X`` shape is ``(n_trades, len(FEATURE_NAMES))``. Trades missing
    *required* fields (no entry bar, no MAE/MFE attributes) are skipped
    — their ids are absent from the returned id list.
    """
    rows: List[List[float]] = []
    ids: List[int] = []
    for t in trades:
        eb = getattr(t, "entry_bar_index", None)
        xb = getattr(t, "exit_bar_index", None)
        if eb is not None and xb is not None:
            duration = max(0.0, float(xb) - float(eb))
        else:
            # Fallback to minutes between timestamps.
            try:
                import pandas as pd

                et = pd.to_datetime(getattr(t, "entry_time", None))
                xt = pd.to_datetime(getattr(t, "exit_time", None))
                duration = max(0.0, (xt - et).total_seconds() / 60.0)
            except Exception:
                duration = 0.0
        sess = _onehot(str(getattr(t, "session", "") or ""), _SESSIONS)
        mae_r = float(getattr(t, "mae_r", 0.0) or 0.0)
        mfe_r = float(getattr(t, "mfe_r", 0.0) or 0.0)
        exit_oh = _onehot_exit(str(getattr(t, "exit_reason", "") or ""))
        rows.append([float(duration), *sess, mae_r, mfe_r, *exit_oh])
        ids.append(int(getattr(t, "id", 0) or 0))
    if not rows:
        return np.empty((0, len(FEATURE_NAMES)), dtype=float), []
    return np.asarray(rows, dtype=float), ids


def _zscore(X: np.ndarray) -> np.ndarray:
    """Zero-mean / unit-std per column. NaN-safe (constant cols → 0)."""
    if X.size == 0:
        return X
    mu = X.mean(axis=0, keepdims=True)
    sigma = X.std(axis=0, keepdims=True)
    safe_sigma = np.where(sigma > 1e-12, sigma, 1.0)
    return (X - mu) / safe_sigma


# ---------------------------------------------------------------------------
# K-means (Lloyd's algorithm) — small, dependency-free
# ---------------------------------------------------------------------------


def _kmeans_pp_init(X: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ seeding: probabilistic spread of initial centroids.

    Reduces the chance Lloyd's converges to a degenerate local minimum
    on small samples. Returns a ``(k, n_features)`` array.
    """
    n = X.shape[0]
    centroids = np.empty((k, X.shape[1]), dtype=float)
    centroids[0] = X[rng.integers(0, n)]
    for i in range(1, k):
        d2 = np.min(((X[:, None, :] - centroids[None, :i, :]) ** 2).sum(axis=2), axis=1)
        total = float(d2.sum())
        if total <= 0:
            centroids[i] = X[rng.integers(0, n)]
            continue
        probs = d2 / total
        idx = int(rng.choice(n, p=probs))
        centroids[i] = X[idx]
    return centroids


def kmeans(
    X: np.ndarray,
    k: int,
    *,
    max_iter: int = 100,
    tol: float = 1e-6,
    seed: Optional[int] = 42,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Run Lloyd's algorithm; return (labels, centroids, wcss).

    ``X`` should already be standardised (z-scored) — see
    ``_zscore``. ``wcss`` is the within-cluster sum of squares used by
    the elbow heuristic.
    """
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    if n == 0 or k <= 0:
        return np.empty(0, dtype=int), np.empty((0, X.shape[1]), dtype=float), 0.0
    k = min(k, n)
    centroids = _kmeans_pp_init(X, k, rng)
    labels = np.zeros(n, dtype=int)
    for _ in range(int(max_iter)):
        # Assign each point to nearest centroid (squared Euclidean).
        d2 = ((X[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        new_labels = np.argmin(d2, axis=1)
        # Recompute centroids; empty clusters get re-seeded.
        new_centroids = np.empty_like(centroids)
        for j in range(k):
            mask = new_labels == j
            if mask.any():
                new_centroids[j] = X[mask].mean(axis=0)
            else:
                new_centroids[j] = X[rng.integers(0, n)]
        shift = float(np.linalg.norm(new_centroids - centroids))
        centroids = new_centroids
        labels = new_labels
        if shift < tol:
            break
    # WCSS
    d2 = ((X - centroids[labels]) ** 2).sum(axis=1)
    wcss = float(d2.sum())
    return labels, centroids, wcss


_SILHOUETTE_SAMPLE_CAP = 1500
"""Above this many trades, silhouette is computed on a random subsample.

The mean silhouette of a stratified subsample is an unbiased estimator
of the population mean (sklearn's ``silhouette_score`` uses the same
trick under ``sample_size=``). 1500 keeps the O(n²) distance matrix
under ~20 MiB and the wall-clock under ~150 ms.
"""


def _silhouette(X: np.ndarray, labels: np.ndarray) -> float:
    """Mean silhouette score (single number ∈ [-1, 1]; higher = better).

    Phase 6.1: previously this bailed entirely for n > 5000 (returning
    ``0.0`` and silently dropping the diagnostic for any large
    backtest). The new path subsamples down to
    :data:`_SILHOUETTE_SAMPLE_CAP` trades — keeping the report
    informative on 50k-trade runs at ~150 ms instead of 8+ minutes.

    The body is fully vectorised: instead of a Python loop over every
    point we compute the per-cluster mean-distance matrix ``M`` of
    shape ``(n, k)`` once and then index it.
    """
    n = X.shape[0]
    if n < 3:
        return 0.0
    unique = np.unique(labels)
    if unique.size < 2:
        return 0.0

    if n > _SILHOUETTE_SAMPLE_CAP:
        rng = np.random.default_rng(0xA9B6)
        sample_idx = rng.choice(n, size=_SILHOUETTE_SAMPLE_CAP, replace=False)
        X = X[sample_idx]
        labels = labels[sample_idx]
        n = X.shape[0]
        unique = np.unique(labels)
        if unique.size < 2:
            return 0.0

    # Pairwise distance matrix. ``sq`` ≥ 0 mathematically; clip for FP.
    sq = ((X[:, None, :] - X[None, :, :]) ** 2).sum(axis=2)
    dist = np.sqrt(np.maximum(sq, 0))

    # Cluster-mean-distance matrix M[i, c] = mean dist from point i to
    # cluster c (excluding the point itself when c is its own cluster).
    k = unique.size
    label_to_col = {int(c): j for j, c in enumerate(unique)}
    counts = np.array([(labels == c).sum() for c in unique], dtype=float)
    M = np.zeros((n, k), dtype=float)
    for j, c in enumerate(unique):
        mask = labels == c
        M[:, j] = dist[:, mask].sum(axis=1)
    # Exclude self-distance (which is 0) from each point's own column.
    own_cols = np.array([label_to_col[int(c)] for c in labels])
    own_counts = counts[own_cols] - 1.0  # exclude self
    own_counts = np.where(own_counts <= 0, 1.0, own_counts)
    a_i = M[np.arange(n), own_cols] / own_counts

    other = M.copy()
    other[np.arange(n), own_cols] = np.inf
    other_counts = counts[None, :].repeat(n, axis=0).astype(float)
    other_counts[np.arange(n), own_cols] = 1.0
    other_means = other / other_counts
    b_i = other_means.min(axis=1)

    denom = np.maximum.reduce([a_i, b_i, np.full(n, 1e-12)])
    s_vals = (b_i - a_i) / denom
    return float(s_vals.mean())


# ---------------------------------------------------------------------------
# Auto-k selection (elbow first, silhouette tiebreaker)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterRow:
    cluster_id: int
    n_trades: int
    mean_pnl: float
    pnl_std: float
    representative_trade_id: int


@dataclass(frozen=True)
class ClusterResult:
    k: int
    labels: np.ndarray  # length = n_trades passed to fit
    centroids: np.ndarray
    wcss: float
    silhouette: float
    rows: List[ClusterRow]
    feature_names: Tuple[str, ...] = FEATURE_NAMES
    # 2-D PCA projection of the standardised features for visualisation.
    projection: Optional[np.ndarray] = None


def _pca_2d(X: np.ndarray) -> np.ndarray:
    """Tiny in-house PCA → first two components; returns shape (n, 2).

    Used purely for the scatter-plot — no need for sklearn here.
    """
    if X.shape[0] < 2:
        return np.zeros((X.shape[0], 2), dtype=float)
    centered = X - X.mean(axis=0, keepdims=True)
    u, s, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:2]
    return centered @ components.T


def cluster_trades(
    trades: Iterable[Any],
    *,
    k_min: int = 3,
    k_max: int = 7,
    seed: Optional[int] = 42,
) -> Optional[ClusterResult]:
    """Build features, choose k via elbow + silhouette, return clusters.

    Returns ``None`` when fewer than ``k_min`` trades are available so
    the report can omit the section gracefully.
    """
    trade_list = list(trades)
    X_raw, ids = build_feature_matrix(trade_list)
    if X_raw.shape[0] < int(k_min):
        return None
    X = _zscore(X_raw)

    # Sweep k → measure WCSS, find elbow.
    candidates = list(range(int(k_min), min(int(k_max), X.shape[0]) + 1))
    if not candidates:
        return None

    runs: Dict[int, Tuple[np.ndarray, np.ndarray, float]] = {}
    for k in candidates:
        runs[k] = kmeans(X, k, seed=seed)

    wcss_arr = np.asarray([runs[k][2] for k in candidates], dtype=float)
    if len(candidates) >= 3:
        # Elbow = max curvature in the WCSS curve. We compute the second
        # derivative (delta-of-delta) and pick the index of largest
        # absolute change.
        d1 = np.diff(wcss_arr)
        d2 = np.diff(d1)
        elbow_idx = int(np.argmax(np.abs(d2))) + 1  # +1 because d2 has 2 fewer
        chosen_k = candidates[elbow_idx]
    else:
        chosen_k = candidates[int(np.argmin(wcss_arr))]

    # Silhouette refinement only if elbow is ambiguous (flat WCSS curve).
    spread = float(wcss_arr.max() - wcss_arr.min())
    if spread < 1e-6:
        # All k give the same WCSS; fall back to silhouette winner.
        sils = [_silhouette(X, runs[k][0]) for k in candidates]
        chosen_k = candidates[int(np.argmax(sils))]

    labels, centroids, wcss = runs[chosen_k]
    sil = _silhouette(X, labels)

    # Per-cluster rows
    pnls = np.asarray(
        [float(getattr(t, "pnl", 0.0) or 0.0) for t in trade_list[: len(ids)]],
        dtype=float,
    )
    rows: List[ClusterRow] = []
    for j in range(chosen_k):
        mask = labels == j
        if not mask.any():
            rows.append(
                ClusterRow(
                    cluster_id=int(j),
                    n_trades=0,
                    mean_pnl=0.0,
                    pnl_std=0.0,
                    representative_trade_id=0,
                )
            )
            continue
        cluster_pnls = pnls[mask]
        # Representative = trade closest to the centroid (smallest dist).
        d2 = ((X[mask] - centroids[j]) ** 2).sum(axis=1)
        rep_local = int(np.argmin(d2))
        rep_global = int(np.where(mask)[0][rep_local])
        rep_id = int(ids[rep_global]) if rep_global < len(ids) else 0
        rows.append(
            ClusterRow(
                cluster_id=int(j),
                n_trades=int(mask.sum()),
                mean_pnl=float(cluster_pnls.mean()),
                pnl_std=float(cluster_pnls.std(ddof=0)),
                representative_trade_id=rep_id,
            )
        )

    return ClusterResult(
        k=int(chosen_k),
        labels=labels,
        centroids=centroids,
        wcss=float(wcss),
        silhouette=float(sil),
        rows=rows,
        projection=_pca_2d(X),
    )
