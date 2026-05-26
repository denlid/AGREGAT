"""
Monte Carlo simulation utilities for backtest results.

Two flavours are provided:

- **Bootstrap MC** (`bootstrap_pnl`) — resample the existing trade PnL series with
  replacement to estimate the distribution of final equity / max drawdown / etc.
- **Shock-injection MC** (`shock_pnl`) — perturb each trade's PnL by random
  slippage / missed-fill noise to model execution risk.

Both functions return a :class:`MCResult` with:

- `final_equity` — array of size ``n_iter`` of final equity values per simulation
- `max_drawdown_pct` — array of max drawdown % per simulation
- `equity_paths` — optional matrix of full equity paths (omitted when ``store_paths=False``)
- `summary` — dict with mean / median / 5th / 95th percentiles for both metrics

Memory is bounded by chunking — see ``MAX_CELLS_PER_CHUNK``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import numpy as np

MAX_CELLS_PER_CHUNK = 5_000_000  # ~40 MiB float64; safe on consumer laptops

BootstrapMethod = Literal["iid", "block", "block_auto"]


@dataclass(frozen=True)
class MCResult:
    """Aggregated Monte Carlo output."""

    final_equity: np.ndarray  # shape (n_iter,)
    max_drawdown_pct: np.ndarray  # shape (n_iter,) — positive percent values
    equity_paths: Optional[np.ndarray]  # shape (n_iter, n_trades+1) or None
    summary: dict[str, Any]
    # Phase 1.8 — per-iter trade-level metrics. Kept on the result so
    # the GUI can build histograms (Sharpe / PF / total-PnL) without
    # rerunning the bootstrap.
    sharpe: np.ndarray = field(default_factory=lambda: np.zeros(0))
    profit_factor: np.ndarray = field(default_factory=lambda: np.zeros(0))


def _drawdown_pct_along_axis(equity_paths: np.ndarray) -> np.ndarray:
    """Vectorised max drawdown % per row (axis=1).

    ``equity_paths`` shape ``(n_iter, n_steps)`` (positive equity values).
    Returns a 1-D array of size ``n_iter`` with positive percent drawdowns.
    """
    if equity_paths.size == 0:
        return np.zeros(equity_paths.shape[0], dtype=float)
    running_max = np.maximum.accumulate(equity_paths, axis=1)
    # Avoid divide-by-zero — running_max can be ≤ 0 only if equity goes negative.
    safe_max = np.where(running_max > 0, running_max, 1.0)
    dd_pct = (running_max - equity_paths) / safe_max * 100.0
    dd_pct = np.where(running_max > 0, dd_pct, 0.0)
    out: np.ndarray = dd_pct.max(axis=1)
    return out


def _summarize(
    final_equity: np.ndarray,
    max_dd: np.ndarray,
    initial: float,
    *,
    sharpe: Optional[np.ndarray] = None,
    profit_factor: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """Build the summary dict used by both bootstrap and shock MC.

    Phase 1.8 adds per-iter Sharpe and Profit-Factor confidence
    intervals (p5 / p50 / p95) plus total-PnL percentiles and the
    drawdown-tail risk probabilities at the institutional 30 % and
    50 % thresholds.
    """
    total_pnl = final_equity - initial
    out: dict[str, Any] = {
        "iterations": int(final_equity.size),
        "initial_equity": float(initial),
        "final_equity_mean": float(np.mean(final_equity)),
        "final_equity_median": float(np.median(final_equity)),
        "final_equity_p5": float(np.percentile(final_equity, 5)),
        "final_equity_p95": float(np.percentile(final_equity, 95)),
        "total_pnl_p5": float(np.percentile(total_pnl, 5)),
        "total_pnl_p50": float(np.percentile(total_pnl, 50)),
        "total_pnl_p95": float(np.percentile(total_pnl, 95)),
        "max_dd_mean": float(np.mean(max_dd)),
        "max_dd_median": float(np.median(max_dd)),
        "max_dd_p5": float(np.percentile(max_dd, 5)),
        "max_dd_p50": float(np.percentile(max_dd, 50)),
        "max_dd_p95": float(np.percentile(max_dd, 95)),
        "prob_drawdown_gt_20pct": float(np.mean(max_dd > 20.0)),
        "prob_drawdown_gt_30pct": float(np.mean(max_dd > 30.0)),
        "prob_drawdown_gt_50pct": float(np.mean(max_dd > 50.0)),
        "prob_loss": float(np.mean(final_equity < initial)),
    }
    if sharpe is not None and sharpe.size:
        finite = sharpe[np.isfinite(sharpe)]
        if finite.size:
            out["sharpe_p5"] = float(np.percentile(finite, 5))
            out["sharpe_p50"] = float(np.percentile(finite, 50))
            out["sharpe_p95"] = float(np.percentile(finite, 95))
    if profit_factor is not None and profit_factor.size:
        finite_pf = profit_factor[np.isfinite(profit_factor)]
        if finite_pf.size:
            out["pf_p5"] = float(np.percentile(finite_pf, 5))
            out["pf_p50"] = float(np.percentile(finite_pf, 50))
            out["pf_p95"] = float(np.percentile(finite_pf, 95))
    return out


def _per_row_sharpe_and_pf(
    sampled: np.ndarray, n: int
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised Sharpe ratio and Profit Factor per row.

    Sharpe is annualisation-free trade-level Sharpe:
    ``mean(pnl) / std(pnl) * sqrt(n_trades)``. PF is
    ``sum(positive) / |sum(negative)|`` with sane defaults when one
    side is empty (``inf`` if all wins, ``0`` if all losses).
    """
    chunk_mean = sampled.mean(axis=1)
    chunk_std = sampled.std(axis=1, ddof=0)
    sharpe = np.where(
        chunk_std > 0, chunk_mean / np.where(chunk_std > 0, chunk_std, 1.0), 0.0
    ) * np.sqrt(max(n, 1))
    gross_profit = np.where(sampled > 0, sampled, 0.0).sum(axis=1)
    gross_loss = -np.where(sampled < 0, sampled, 0.0).sum(axis=1)
    pf = np.where(
        gross_loss > 0,
        gross_profit / np.where(gross_loss > 0, gross_loss, 1.0),
        np.where(gross_profit > 0, np.inf, 0.0),
    )
    return sharpe, pf


# ----------------------------------------------------------------------------
# Bootstrap Monte Carlo
# ----------------------------------------------------------------------------
def _block_bootstrap_indices(
    rng: np.random.Generator,
    *,
    n: int,
    rows: int,
    block_size: int,
) -> np.ndarray:
    """Build ``(rows, n)`` index matrix using a circular block-bootstrap.

    Each row is a concatenation of randomly chosen blocks of size
    ``block_size`` drawn (with replacement) from a circular view of the
    PnL series. Within a block the original order is preserved, which is
    what gives the bootstrap its ability to mimic the autocorrelation /
    streak structure of the source — see Politis & Romano (1992),
    "A circular block-resampling procedure for stationary data".

    Returns row indices of length exactly ``n`` (trailing partial block
    is truncated, so total trades-per-iter matches the source).
    """
    blocks_needed = (n + block_size - 1) // block_size
    starts = rng.integers(0, n, size=(rows, blocks_needed))
    # Expand each start into [start, start+1, ..., start+block_size-1] mod n.
    offsets = np.arange(block_size, dtype=np.int64)
    expanded = (starts[:, :, None] + offsets[None, None, :]) % n
    # (rows, blocks_needed, block_size) → (rows, blocks_needed*block_size)
    flat = expanded.reshape(rows, blocks_needed * block_size)
    # Truncate to exactly n columns so the equity-path length matches
    # the iid case and the source PnL count.
    return flat[:, :n]


def bootstrap_pnl(
    pnls: np.ndarray,
    *,
    initial_equity: float,
    n_iter: int = 1000,
    store_paths: bool = False,
    seed: Optional[int] = None,
    block_size: int = 1,
    method: BootstrapMethod = "iid",
    cancel_token: Optional[Callable[[], bool]] = None,
) -> MCResult:
    """Resample ``pnls`` with replacement and reconstruct equity paths.

    Parameters
    ----------
    pnls
        Per-trade PnL series (positive = win, negative = loss).
    initial_equity
        Starting equity for each simulated path.
    n_iter
        Number of bootstrap iterations.
    store_paths
        If True the full equity paths are returned; otherwise only summaries.
    seed
        Optional RNG seed for reproducibility.
    block_size
        Length of the block in the circular block-bootstrap. ``1`` (the
        default) reproduces the legacy iid resampling. ``> 1`` keeps
        runs of consecutive trades together — preserving win/loss-streak
        autocorrelation that ordinary iid resampling destroys, which
        widens the simulated DD-tail (TZ Phase 4 / D4). A common
        rule-of-thumb is ``block_size ≈ n ** (1/3)``.
    method
        Phase 1.8 — ``"iid"`` keeps the legacy independent draw,
        ``"block"`` uses ``block_size`` as supplied, and
        ``"block_auto"`` picks ``block_size = round(n ** 1/3)`` so the
        bootstrap captures realistic streak autocorrelation without
        the user having to guess.
    """
    pnls = np.asarray(pnls, dtype=float)
    if pnls.size == 0 or n_iter <= 0:
        zeros = np.array([initial_equity], dtype=float)
        empty_metric = np.zeros(0, dtype=float)
        return MCResult(
            final_equity=zeros,
            max_drawdown_pct=np.array([0.0]),
            equity_paths=None,
            summary=_summarize(
                zeros,
                np.array([0.0]),
                initial_equity,
                sharpe=empty_metric,
                profit_factor=empty_metric,
            ),
            sharpe=empty_metric,
            profit_factor=empty_metric,
        )

    n = int(pnls.size)
    if block_size <= 0:
        raise ValueError(f"block_size must be ≥ 1, got {block_size!r}")
    if method == "block_auto":
        # Phase 1.8 — auto-pick block_size = round(n ** 1/3) per
        # Politis & White (2004) practical bandwidth rule. Whatever
        # value the caller passed via ``block_size`` is overridden.
        block_size = max(1, int(round(n ** (1.0 / 3.0))))
    # NB. For method='iid' we do *not* clamp ``block_size`` to 1 — the
    # caller's explicit value wins (preserves legacy behaviour where
    # ``block_size>1`` already produced a block bootstrap regardless of
    # the new ``method`` argument).
    block_size = int(min(block_size, n))  # capping at n means "one big block"
    rng = np.random.default_rng(seed)
    chunk = max(1, min(int(n_iter), MAX_CELLS_PER_CHUNK // n))

    final_eq = np.empty(int(n_iter), dtype=float)
    max_dd = np.empty(int(n_iter), dtype=float)
    sharpe_buf = np.empty(int(n_iter), dtype=float)
    pf_buf = np.empty(int(n_iter), dtype=float)
    paths_buf = np.empty((int(n_iter), n + 1), dtype=float) if store_paths else None

    done = 0
    while done < n_iter:
        if cancel_token is not None and cancel_token():
            raise RuntimeError("cancelled")
        size = min(chunk, int(n_iter) - done)
        if block_size <= 1:
            idx = rng.integers(0, n, size=(size, n))
        else:
            idx = _block_bootstrap_indices(
                rng,
                n=n,
                rows=size,
                block_size=block_size,
            )
        sampled = pnls[idx]
        eq_path = np.empty((size, n + 1), dtype=float)
        eq_path[:, 0] = initial_equity
        np.cumsum(sampled, axis=1, out=eq_path[:, 1:])
        eq_path[:, 1:] += initial_equity

        final_eq[done : done + size] = eq_path[:, -1]
        max_dd[done : done + size] = _drawdown_pct_along_axis(eq_path)
        chunk_sharpe, chunk_pf = _per_row_sharpe_and_pf(sampled, n)
        sharpe_buf[done : done + size] = chunk_sharpe
        pf_buf[done : done + size] = chunk_pf
        if paths_buf is not None:
            paths_buf[done : done + size] = eq_path
        done += size

    return MCResult(
        final_equity=final_eq,
        max_drawdown_pct=max_dd,
        equity_paths=paths_buf,
        summary=_summarize(
            final_eq,
            max_dd,
            initial_equity,
            sharpe=sharpe_buf,
            profit_factor=pf_buf,
        ),
        sharpe=sharpe_buf,
        profit_factor=pf_buf,
    )


# ----------------------------------------------------------------------------
# Shock-injection Monte Carlo
# ----------------------------------------------------------------------------
def shock_pnl(
    pnls: np.ndarray,
    *,
    initial_equity: float,
    slippage_std_pct: float = 5.0,
    missed_fill_pct: float = 2.0,
    n_iter: int = 1000,
    store_paths: bool = False,
    seed: Optional[int] = None,
) -> MCResult:
    """Perturb each trade's PnL by random slippage and missed-fill noise.

    Parameters
    ----------
    pnls
        Per-trade PnL series.
    initial_equity
        Starting equity.
    slippage_std_pct
        Std-dev of multiplicative slippage shock applied to each trade
        (e.g. ``5.0`` → ``N(1.0, 0.05)`` multiplier).
    missed_fill_pct
        Probability that a trade is "missed" (PnL → 0 for that draw).
    n_iter
        Number of MC iterations.
    store_paths
        If True the full equity paths are returned.
    seed
        Optional RNG seed.
    """
    pnls = np.asarray(pnls, dtype=float)
    if pnls.size == 0 or n_iter <= 0:
        zeros = np.array([initial_equity], dtype=float)
        empty_metric = np.zeros(0, dtype=float)
        return MCResult(
            final_equity=zeros,
            max_drawdown_pct=np.array([0.0]),
            equity_paths=None,
            summary=_summarize(
                zeros,
                np.array([0.0]),
                initial_equity,
                sharpe=empty_metric,
                profit_factor=empty_metric,
            ),
            sharpe=empty_metric,
            profit_factor=empty_metric,
        )

    n = int(pnls.size)
    rng = np.random.default_rng(seed)
    chunk = max(1, min(int(n_iter), MAX_CELLS_PER_CHUNK // n))

    slip_sigma = max(0.0, slippage_std_pct / 100.0)
    miss_prob = min(1.0, max(0.0, missed_fill_pct / 100.0))

    final_eq = np.empty(int(n_iter), dtype=float)
    max_dd = np.empty(int(n_iter), dtype=float)
    sharpe_buf = np.empty(int(n_iter), dtype=float)
    pf_buf = np.empty(int(n_iter), dtype=float)
    paths_buf = np.empty((int(n_iter), n + 1), dtype=float) if store_paths else None

    done = 0
    while done < n_iter:
        size = min(chunk, int(n_iter) - done)
        # Slippage: multiplicative Gaussian shock, clipped at +/- 50 %.
        if slip_sigma > 0:
            shocks = rng.normal(loc=1.0, scale=slip_sigma, size=(size, n))
            np.clip(shocks, 0.5, 1.5, out=shocks)
        else:
            shocks = np.ones((size, n), dtype=float)
        if miss_prob > 0:
            mask = rng.random(size=(size, n)) >= miss_prob  # True → keep
            shocks *= mask
        sampled = shocks * pnls  # broadcasting over rows

        eq_path = np.empty((size, n + 1), dtype=float)
        eq_path[:, 0] = initial_equity
        np.cumsum(sampled, axis=1, out=eq_path[:, 1:])
        eq_path[:, 1:] += initial_equity

        final_eq[done : done + size] = eq_path[:, -1]
        max_dd[done : done + size] = _drawdown_pct_along_axis(eq_path)
        chunk_sharpe, chunk_pf = _per_row_sharpe_and_pf(sampled, n)
        sharpe_buf[done : done + size] = chunk_sharpe
        pf_buf[done : done + size] = chunk_pf
        if paths_buf is not None:
            paths_buf[done : done + size] = eq_path
        done += size

    return MCResult(
        final_equity=final_eq,
        max_drawdown_pct=max_dd,
        equity_paths=paths_buf,
        summary=_summarize(
            final_eq,
            max_dd,
            initial_equity,
            sharpe=sharpe_buf,
            profit_factor=pf_buf,
        ),
        sharpe=sharpe_buf,
        profit_factor=pf_buf,
    )


__all__ = [
    "MCResult",
    "BootstrapMethod",
    "bootstrap_pnl",
    "shock_pnl",
    "MAX_CELLS_PER_CHUNK",
]
