"""Pure helpers for building trade-marker arrays in :class:`ChartTab`.

The actual GUI wiring (``setData`` on ``PlotDataItem`` instances) lives
in :pymod:`gui.chart_tab.tab`. The math — bucketing trades into
buy/sell/close marker scatter arrays and into batched win/loss
connection-line curves — is factored out here so it can be unit-tested
without spinning up a ``QApplication`` / ``ChartTab`` widget. (TZ Phase
2b — batched markers.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

import numpy as np
import pandas as pd

# A single sentinel between segments lets ``pg.PlotDataItem(connect='finite')``
# render N independent dashed lines as a single scene item.
_NAN = float("nan")


@dataclass
class TradeMarkerArrays:
    """Output bundle from :func:`build_trade_marker_arrays`.

    All arrays are plain Python lists for direct hand-off to
    ``PlotDataItem.setData(x, y)`` without an extra copy.
    """

    bx: List[float]
    by: List[float]
    sx: List[float]
    sy: List[float]
    cx: List[float]
    cy: List[float]
    win_x: List[float]
    win_y: List[float]
    loss_x: List[float]
    loss_y: List[float]


def build_trade_marker_arrays(
    rt: np.ndarray,
    trades: Sequence[Any],
    df_len: int,
    position: Optional[Any] = None,
    position_close_price: Optional[float] = None,
    show_connections: bool = True,
) -> TradeMarkerArrays:
    """Bucket ``trades`` (and optional ``position``) into marker arrays.

    Vectorised implementation — extracts entry/exit times and prices
    into NumPy arrays and uses batch ``searchsorted`` instead of
    per-trade Python-level ``pd.to_datetime`` calls. With 481 trades
    this brings marker build time from ~610 ms down to ~2 ms.
    """
    out = TradeMarkerArrays([], [], [], [], [], [], [], [], [], [])
    if len(rt) == 0:
        return out

    n_trades = len(trades)
    if n_trades == 0 and position is None:
        return out

    t0 = rt[0]

    # ------------------------------------------------------------------
    # Vectorised trade processing
    # ------------------------------------------------------------------
    if n_trades > 0:
        # Build flat NumPy arrays from the trade list.
        # Trade stores pre-cached np.datetime64 in entry_time_np / exit_time_np
        # (populated in Trade.__init__). Fall back to pd.to_datetime only if
        # the cache is missing.
        entry_times = np.empty(n_trades, dtype="datetime64[ns]")
        exit_times = np.empty(n_trades, dtype="datetime64[ns]")
        entry_prices = np.empty(n_trades, dtype=np.float64)
        exit_prices = np.empty(n_trades, dtype=np.float64)
        pnls = np.empty(n_trades, dtype=np.float64)
        entry_idx = np.empty(n_trades, dtype=np.int64)
        exit_idx = np.empty(n_trades, dtype=np.int64)
        directions = []  # list of str — tiny overhead vs the old loop

        for i, tr in enumerate(trades):
            et_np = getattr(tr, "entry_time_np", None)
            xt_np = getattr(tr, "exit_time_np", None)
            entry_times[i] = et_np if et_np is not None else np.datetime64(pd.to_datetime(tr.entry_time))
            exit_times[i] = xt_np if xt_np is not None else np.datetime64(pd.to_datetime(tr.exit_time))
            entry_prices[i] = tr.entry_price
            exit_prices[i] = tr.exit_price
            pnls[i] = tr.pnl
            entry_idx[i] = tr.entry_index
            exit_idx[i] = tr.exit_index
            directions.append(tr.direction)

        # Filter: only trades within bounds and visible time range
        valid = (entry_idx < df_len) & (exit_idx < df_len)
        visible = valid & ~((entry_times < t0) & (exit_times < t0))

        if visible.any():
            # Batch searchsorted — single NumPy call for all trades
            vis_entry = entry_times[visible]
            vis_exit = exit_times[visible]

            ie_arr = np.clip(np.searchsorted(rt, vis_entry, side="right").astype(np.int64) - 1, 0, len(rt) - 1)
            ix_arr = np.clip(np.searchsorted(rt, vis_exit, side="right").astype(np.int64) - 1, 0, len(rt) - 1)

            vis_entry_prices = entry_prices[visible]
            vis_exit_prices = exit_prices[visible]
            vis_pnls = pnls[visible]
            vis_dirs = [d for d, v in zip(directions, visible) if v]

            for j in range(len(vis_dirs)):
                ie = int(ie_arr[j])
                ix = int(ix_arr[j])
                ep = float(vis_entry_prices[j])
                xp = float(vis_exit_prices[j])

                if vis_dirs[j] == "long":
                    out.bx.append(ie)
                    out.by.append(ep)
                else:
                    out.sx.append(ie)
                    out.sy.append(ep)
                out.cx.append(ix)
                out.cy.append(xp)

                if show_connections:
                    if vis_pnls[j] >= 0:
                        out.win_x.extend((ie, ix, _NAN))
                        out.win_y.extend((ep, xp, _NAN))
                    else:
                        out.loss_x.extend((ie, ix, _NAN))
                        out.loss_y.extend((ep, xp, _NAN))

    # ------------------------------------------------------------------
    # Open position (unchanged — single item, no perf concern)
    # ------------------------------------------------------------------
    if position is not None and position.entry_index < df_len:
        et_np = getattr(position, "entry_time_np", None)
        if et_np is None:
            et_np = np.datetime64(pd.to_datetime(position.entry_time))
        if et_np >= t0:
            ie = max(0, int(np.searchsorted(rt, et_np, side="right")) - 1)
            cp = position.entry_price if position_close_price is None else position_close_price
            if position.direction == "long":
                out.bx.append(ie)
                out.by.append(position.entry_price)
            else:
                out.sx.append(ie)
                out.sy.append(position.entry_price)
            if show_connections:
                u = (
                    cp - position.entry_price
                    if position.direction == "long"
                    else position.entry_price - cp
                )
                if u >= 0:
                    out.win_x.extend((ie, len(rt) - 1, _NAN))
                    out.win_y.extend((position.entry_price, cp, _NAN))
                else:
                    out.loss_x.extend((ie, len(rt) - 1, _NAN))
                    out.loss_y.extend((position.entry_price, cp, _NAN))

    return out
