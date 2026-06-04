"""LINEAR LOGIC CORE (15m + 1m).

A linear-structure system that uses the 15m timeframe for context and
the 1m timeframe for entry confirmation. Wicks are ignored at every
decision point — the system reads only candle bodies (closes).

Algorithm
---------
**Step A — 15m context (Linear CISD).**
On every 15m close we look for a "Close Inside Structural Detail":
the most recent body close prints strictly past the most recent
confirmed pivot body extreme on the opposite side. A bullish CISD
unlocks a fresh bullish 15m FVG — that is the *active zone*.

**Step B — 1m tap & confirmation.**
Inside the active 15m FVG zone we wait for a 1m bar to *touch* the
gap (wicks count for the touch — that's the only place wicks matter).
After the touch, we wait for a 1m linear CISD in the same direction
as the 15m bias. When that 1m CISD prints, we queue a market entry
at the next 1m open.

**Step C — Trade parameters.**
* SL = the 15m structural pivot's body extreme on the *opposite* side
  of the trade direction, plus an asset-side buffer (default 1 pt for
  DE40).
* TP = ``rr`` × SL distance (default 2RR).

**Step D — Active management (Counter-CISD on 15m).**
While the position is open, every 15m close fires
``on_15m_close``. If a *counter* CISD prints (bear CISD while long,
bull CISD while short):

* unrealised PnL < 0  →  immediate market exit (``COUNTER_CISD``);
* unrealised PnL > 0  →  one of three actions, configurable:
  ``"none"`` (do nothing), ``"be"`` (move SL to break-even),
  ``"trail_pivot"`` (move SL to the latest 15m pivot ± buffer).

Configuration
-------------
Every rule is editable from Settings → Strategy. The defaults match
the LINEAR LOGIC CORE specification.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from engine.strategy.context import Param, Strategy
from engine.core.linear import (
    body_extremes,
    last_active_fvg,
    last_pivots,
    linear_cisd_last,
    linear_cisd_series,
    linear_pivots,
)

logger = logging.getLogger("StrategyTester.strategies.linear_logic_core")


class LinearLogicCore(Strategy):
    """LINEAR LOGIC CORE — 15m context + 1m entry, close-only structure."""

    name = "LINEAR LOGIC CORE (15m-1m)"

    PARAMS = [
        Param(
            name="risk_pct",
            label="Risk per trade (%)",
            kind="float",
            default=1.0,
            min=0.1,
            max=5.0,
            step=0.1,
            tooltip="Account percentage risked per trade. LINEAR LOGIC CORE: 1%.",
        ),
        Param(
            name="strict_lot_cap",
            label="Strict lot cap (cancel if over)",
            kind="bool",
            default=True,
            tooltip="When the calculated lot exceeds max_lots_per_100k or the broker "
            "absolute cap, CANCEL the trade instead of clamping. "
            "Strategy spec requires this.",
        ),
        Param(
            name="ott_filter",
            label="OTT trading-hours filter (09:00-18:00)",
            kind="bool",
            default=True,
            tooltip="Block new entries outside the asset's trading window. "
            "Existing positions are unaffected.",
        ),
        Param(
            name="force_eod_close",
            label="Force close at EOD (22:00)",
            kind="bool",
            default=True,
            tooltip="Close any open position at the asset's eod_close. "
            "Disable for swing-style holding through the daily roll.",
        ),
        Param(
            name="counter_cisd_winning",
            label="Counter-CISD action when in profit",
            kind="choice",
            default="be",
            choices=["none", "be", "trail_pivot"],
            tooltip="What to do when a 15m counter-CISD prints while the position "
            "is in profit. Losing positions always exit at market.",
        ),
        Param(
            name="rr",
            label="Take-profit RR multiple",
            kind="float",
            default=2.0,
            min=0.5,
            max=10.0,
            step=0.5,
            tooltip="TP distance = RR × SL distance.",
        ),
        Param(
            name="sl_buffer_pts",
            label="SL buffer beyond pivot (points)",
            kind="float",
            default=1.0,
            min=0.0,
            max=20.0,
            step=0.5,
            tooltip="Additional points beyond the 15m structural pivot for SL.",
        ),
        Param(
            name="pivot_lookback",
            label="Pivot lookback (bars per side)",
            kind="int",
            default=3,
            min=2,
            max=10,
            step=1,
            tooltip="A pivot needs this many bars on each side. Default 3 = 7-bar pivot.",
        ),
        Param(
            name="entry_tf",
            label="Entry timeframe",
            kind="choice",
            default="1m",
            choices=["1m"],
            tooltip="Timeframe used for tap & 1m CISD confirmation. Currently 1m only.",
        ),
        Param(
            name="context_tf",
            label="Context timeframe",
            kind="choice",
            default="15m",
            choices=["5m", "15m", "30m"],
            tooltip="Higher timeframe used for structure (FVG + pivots + Counter-CISD).",
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Per-trade state — reset on every fresh setup.
        self._pending_bias: int = 0  # +1 long bias, -1 short bias, 0 none
        self._fvg_low: float = 0.0
        self._fvg_high: float = 0.0
        self._pivot_price: float = 0.0  # opposite-side pivot used for SL
        self._tap_seen: bool = False  # has 1m touched the active FVG?

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _reset_setup(self) -> None:
        self._pending_bias = 0
        self._fvg_low = 0.0
        self._fvg_high = 0.0
        self._pivot_price = 0.0
        self._tap_seen = False

    def _ott_blocks(self, ctx) -> bool:
        """OTT (09:00-18:00) filter — only when the user has it enabled."""
        if not self.params.get("ott_filter", True):
            return False
        return not ctx.is_trading_hours()

    # ------------------------------------------------------------------
    # 15m context hook — fires once per completed 15m bar BEFORE on_candle
    # ------------------------------------------------------------------
    def on_15m_close(self, ctx) -> None:
        ctx_tf = self.params.get("context_tf", "15m")
        if ctx_tf != "15m":
            # The dispatch is name-based; if the user picks a different
            # context TF we route through the generic hook below.
            return
        self._on_context_close(ctx)

    def on_5m_close(self, ctx) -> None:
        if self.params.get("context_tf", "15m") == "5m":
            self._on_context_close(ctx)

    def on_30m_close(self, ctx) -> None:
        if self.params.get("context_tf", "15m") == "30m":
            self._on_context_close(ctx)

    def _on_context_close(self, ctx) -> None:
        lookback = int(self.params.get("pivot_lookback", 3))
        ctx_tf = self.params.get("context_tf", "15m")
        view = ctx.get_tf(ctx_tf)
        if len(view) < 2 * lookback + 2:
            return
        ctx_open = np.asarray(view.open, dtype=np.float64)
        ctx_close = np.asarray(view.close, dtype=np.float64)
        cisd = linear_cisd_last(ctx_open, ctx_close, lookback=lookback)

        # 1) Active management on the open position.
        if ctx.position is not None:
            self._manage_open_position(ctx, cisd, ctx_open, ctx_close, lookback)
            return

        # 2) No position: re-arm a fresh setup if the latest close
        #    printed a CISD with a still-active matching FVG.
        if cisd == +1:
            fvg = last_active_fvg(ctx_open, ctx_close, direction=+1)
            if fvg is None:
                self._reset_setup()
                return
            ph, pl = last_pivots(ctx_open, ctx_close, lookback=lookback)
            if pl is None:
                self._reset_setup()
                return
            self._pending_bias = +1
            self._fvg_low = fvg.low
            self._fvg_high = fvg.high
            self._pivot_price = pl.price  # opposite-side pivot for SL
            self._tap_seen = False
        elif cisd == -1:
            fvg = last_active_fvg(ctx_open, ctx_close, direction=-1)
            if fvg is None:
                self._reset_setup()
                return
            ph, pl = last_pivots(ctx_open, ctx_close, lookback=lookback)
            if ph is None:
                self._reset_setup()
                return
            self._pending_bias = -1
            self._fvg_low = fvg.low
            self._fvg_high = fvg.high
            self._pivot_price = ph.price
            self._tap_seen = False
        else:
            # Setup invalidated when no new CISD prints AND the gap is
            # consumed (last_active_fvg returns None next time).
            if self._pending_bias != 0:
                fvg = last_active_fvg(ctx_open, ctx_close, direction=self._pending_bias)
                if fvg is None:
                    self._reset_setup()

    def _manage_open_position(
        self,
        ctx,
        cisd: int,
        ctx_open: np.ndarray,
        ctx_close: np.ndarray,
        lookback: int,
    ) -> None:
        pos = ctx.position
        if pos is None:
            return
        opposite = (pos.direction == "long" and cisd == -1) or (
            pos.direction == "short" and cisd == +1
        )
        if not opposite:
            return

        # Unrealised PnL on the bid: long sells at last close, short
        # buys at last close + spread (executed at engine layer when we
        # call ctx.close()).
        last_close = float(ctx.candle.get("close", pos.entry_price))
        if pos.direction == "long":
            unrealised_pts = last_close - pos.entry_price
        else:
            unrealised_pts = pos.entry_price - last_close

        if unrealised_pts < 0:
            ctx.close(reason="COUNTER_CISD")
            self._reset_setup()
            return

        action = self.params.get("counter_cisd_winning", "be")
        if action == "none":
            return
        if action == "be":
            ctx.update_sl(pos.entry_price)
            return
        if action == "trail_pivot":
            ph, pl = last_pivots(ctx_open, ctx_close, lookback=lookback)
            buf = float(self.params.get("sl_buffer_pts", 1.0))
            if pos.direction == "long" and pl is not None:
                new_sl = pl.price - buf
                # Never loosen the stop.
                if pos.sl is None or new_sl > pos.sl:
                    ctx.update_sl(new_sl)
            elif pos.direction == "short" and ph is not None:
                new_sl = ph.price + buf
                if pos.sl is None or new_sl < pos.sl:
                    ctx.update_sl(new_sl)

    # ------------------------------------------------------------------
    # 1m hook — tap detection + 1m CISD confirmation + entry
    # ------------------------------------------------------------------
    def on_candle(self, ctx) -> None:
        if ctx.position is not None or ctx._engine.pending_order is not None:
            return
        if self._pending_bias == 0:
            return
        if self._ott_blocks(ctx):
            return
        if len(ctx.data) < 8:
            return

        # 1) Tap detection — wicks count, the 1m bar must touch the
        #    active 15m FVG zone.
        bar_high = float(ctx.data.high[-1])
        bar_low = float(ctx.data.low[-1])
        if not self._tap_seen:
            tapped = bar_low <= self._fvg_high and bar_high >= self._fvg_low
            if tapped:
                self._tap_seen = True
            else:
                return

        # 2) 1m linear CISD confirmation (bodies only).
        lookback = int(self.params.get("pivot_lookback", 3))
        m1_open = np.asarray(ctx.data.open, dtype=np.float64)
        m1_close = np.asarray(ctx.data.close, dtype=np.float64)
        if len(m1_close) < 2 * lookback + 2:
            return
        m1_cisd = linear_cisd_last(m1_open, m1_close, lookback=lookback)
        if m1_cisd != self._pending_bias:
            return

        # 3) Sizing & queue.
        last_close = float(m1_close[-1])
        buf = float(self.params.get("sl_buffer_pts", 1.0))
        rr = float(self.params.get("rr", 2.0))
        risk_pct = float(self.params.get("risk_pct", 1.0))
        strict = bool(self.params.get("strict_lot_cap", True))
        if self._pending_bias == +1:
            sl_price = self._pivot_price - buf
            sl_dist = last_close - sl_price
            if sl_dist <= 0:
                self._reset_setup()
                return
            tp_price = last_close + sl_dist * rr
            sl_pts = sl_dist / max(ctx.asset.tick_size, 1e-9)
            lot = ctx.calculate_lot(
                sl_points=sl_pts,
                risk_pct=risk_pct,
                strict=strict,
            )
            if lot is None:
                # Cancelled by strict cap; setup stays armed for the
                # next 1m bar to retry sizing if equity changes.
                return
            ctx.buy_at_open(
                volume=lot,
                sl=sl_price,
                tp=tp_price,
                reason="LLC long entry",
                tag="llc_long",
            )
            self._reset_setup()
        elif self._pending_bias == -1:
            sl_price = self._pivot_price + buf
            sl_dist = sl_price - last_close
            if sl_dist <= 0:
                self._reset_setup()
                return
            tp_price = last_close - sl_dist * rr
            sl_pts = sl_dist / max(ctx.asset.tick_size, 1e-9)
            lot = ctx.calculate_lot(
                sl_points=sl_pts,
                risk_pct=risk_pct,
                strict=strict,
            )
            if lot is None:
                return
            ctx.sell_at_open(
                volume=lot,
                sl=sl_price,
                tp=tp_price,
                reason="LLC short entry",
                tag="llc_short",
            )
            self._reset_setup()

    # ------------------------------------------------------------------
    # Fast Quant (vectorised) path — same setup detection as on_candle,
    # but executed in a single pass over the 1m frame so the optimiser /
    # parameter sweeps do not need to spin up the GUI engine.
    #
    # The vectorised path produces *entry signals only* — Counter-CISD
    # active management is intentionally not modelled here because it
    # would require simulating broker state inside this method, which
    # is the very thing the vectorised engine handles. A position
    # opened in vectorised mode therefore exits at SL or TP only (or
    # at EOD if the engine flag is on); the visual on_candle path
    # remains the canonical source of truth for active management.
    # ------------------------------------------------------------------
    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        sl_pts = np.zeros(n, dtype=np.float64)
        tp_pts = np.zeros(n, dtype=np.float64)
        if n == 0:
            return pd.DataFrame(
                {"signal": signal, "sl_points": sl_pts, "tp_points": tp_pts},
                index=df.index,
            )

        ctx_tf = str(self.params.get("context_tf", "15m"))
        try:
            tf_minutes = int(ctx_tf.rstrip("m"))
        except ValueError:
            tf_minutes = 15
        lookback = int(self.params.get("pivot_lookback", 3))
        buf = float(self.params.get("sl_buffer_pts", 1.0))
        rr = float(self.params.get("rr", 2.0))

        # Vectorised engine passes Pascal-cased columns; visual / unit
        # tests use lowercase. Accept either.
        def _col(name: str) -> np.ndarray:
            if name in df.columns:
                return np.asarray(df[name], dtype=np.float64)
            cap = name.capitalize()
            if cap in df.columns:
                return np.asarray(df[cap], dtype=np.float64)
            raise KeyError(f"vectorized_signals: missing column {name!r}/{cap!r}")

        if "time" in df.columns:
            time_col = df["time"]
        elif "Time" in df.columns:
            time_col = df["Time"]
        else:
            time_col = df.index
        times = pd.to_datetime(time_col)

        m1_open = _col("open")
        m1_high = _col("high")
        m1_low = _col("low")
        m1_close = _col("close")

        # Build HTF aggregate once. The HTF bar at index k is "complete"
        # at the moment the next bar begins, i.e. after the LAST 1m bar
        # whose timestamp falls inside it. Mirror the engine's
        # ``TimeframeAggregator`` boundary detection so the on_<tf>_close
        # event timing is byte-identical (Visual ↔ vectorised parity).
        bucket_starts = pd.Series(times).dt.floor(f"{tf_minutes}min").values
        n_bars = len(bucket_starts)
        boundary = np.empty(n_bars, dtype=bool)
        boundary[0] = True
        boundary[1:] = bucket_starts[1:] != bucket_starts[:-1]
        bucket_idx = np.cumsum(boundary) - 1
        n_htf = int(bucket_idx[-1]) + 1 if n_bars > 0 else 0

        htf_open = np.empty(n_htf, dtype=np.float64)
        htf_close = np.empty(n_htf, dtype=np.float64)
        htf_close_idx = np.empty(n_htf, dtype=np.int64)
        # First 1m index of each bucket: where boundary transitions True.
        first_idx = np.where(boundary)[0]
        for k in range(n_htf):
            start = int(first_idx[k])
            end = int(first_idx[k + 1] - 1) if k + 1 < n_htf else n_bars - 1
            htf_open[k] = m1_open[start]
            htf_close[k] = m1_close[end]
            htf_close_idx[k] = end

        # ----- Pre-compute every per-bar primitive ONCE (O(n) total).
        # The previous implementation called ``linear_cisd_last`` /
        # ``last_pivots`` on growing slices ``arr[: i + 1]`` inside the
        # 1-bar loop; that's O(n²·lookback) and dominates wall time on
        # any realistic dataset (≥10k bars). We now precompute:
        #   * htf_cisd_arr[k]  — CISD signal at HTF bar k
        #   * htf_last_high_idx[k] / htf_last_low_idx[k] — index of the
        #     most recent confirmed pivot of each kind at HTF bar k
        #   * m1_cisd_arr[i]   — 1m CISD signal at 1m bar i
        # All run in O(n) (or close to it) and turn the per-bar loop
        # below into pure O(1) lookups.
        htf_cisd_arr = linear_cisd_series(htf_open, htf_close, lookback=lookback)
        htf_is_high, htf_is_low = linear_pivots(htf_open, htf_close, lookback=lookback)
        htf_body_lo, htf_body_hi = body_extremes(htf_open, htf_close)

        def _ffill_idx(mask: np.ndarray, lookback: int) -> np.ndarray:
            """Return ``arr`` where ``arr[i]`` is the most recent
            ``j`` with ``mask[j] and j + lookback <= i``, or ``-1``."""
            n_local = mask.shape[0]
            out = np.full(n_local, -1, dtype=np.int64)
            idx = np.flatnonzero(mask)
            if idx.size == 0:
                return out
            confirm = idx + lookback
            valid = confirm < n_local
            if not valid.any():
                return out
            out[confirm[valid]] = idx[valid]
            # Forward fill the last seen index.
            for k in range(1, n_local):
                if out[k] == -1:
                    out[k] = out[k - 1]
            return out

        htf_last_high_idx = _ffill_idx(htf_is_high, lookback)
        htf_last_low_idx = _ffill_idx(htf_is_low, lookback)
        m1_cisd_arr = linear_cisd_series(m1_open, m1_close, lookback=lookback)

        # Strategy state (mirrors __init__)
        pending_bias = 0
        fvg_low = 0.0
        fvg_high = 0.0
        pivot_price = 0.0
        tap_seen = False

        # For each 1m bar, lookup how many HTF bars have completed.
        # The Visual engine considers an HTF bar *completed* on the
        # FIRST 1m bar of the *next* bucket (so the on_<tf>_close hook
        # fires before on_candle on bar ``htf_close_idx[k] + 1``). We
        # mirror that here: an HTF bar k is available for processing
        # starting at 1m index ``htf_close_idx[k] + 1``. ``side="left"``
        # with the offset gives this count in O(log n).
        htf_completed = np.searchsorted(htf_close_idx + 1, np.arange(n), side="right")

        last_processed_htf = -1
        for i in range(n):
            n_done = int(htf_completed[i])
            # 1) Process newly completed HTF bars (one or more) up to this i.
            while last_processed_htf < n_done - 1:
                last_processed_htf += 1
                k = last_processed_htf
                if k + 1 < 2 * lookback + 2:
                    continue
                cisd = int(htf_cisd_arr[k])
                ho = htf_open[: k + 1]
                hc = htf_close[: k + 1]
                if cisd == +1:
                    fvg = last_active_fvg(ho, hc, direction=+1)
                    pl_idx = int(htf_last_low_idx[k])
                    if fvg is not None and pl_idx >= 0:
                        pending_bias = +1
                        fvg_low = fvg.low
                        fvg_high = fvg.high
                        pivot_price = float(htf_body_lo[pl_idx])
                        tap_seen = False
                    else:
                        pending_bias = 0
                elif cisd == -1:
                    fvg = last_active_fvg(ho, hc, direction=-1)
                    ph_idx = int(htf_last_high_idx[k])
                    if fvg is not None and ph_idx >= 0:
                        pending_bias = -1
                        fvg_low = fvg.low
                        fvg_high = fvg.high
                        pivot_price = float(htf_body_hi[ph_idx])
                        tap_seen = False
                    else:
                        pending_bias = 0
                else:
                    if pending_bias != 0:
                        fvg = last_active_fvg(ho, hc, direction=pending_bias)
                        if fvg is None:
                            pending_bias = 0

            # 2) On the 1m bar itself, check tap + 1m CISD.
            if pending_bias == 0:
                continue
            bar_high = m1_high[i]
            bar_low = m1_low[i]
            if not tap_seen:
                tapped = (bar_low <= fvg_high) and (bar_high >= fvg_low)
                if tapped:
                    tap_seen = True
                else:
                    continue

            # 1m CISD on bodies up to and including bar i.
            if i + 1 < 2 * lookback + 2:
                continue
            m1_cisd = int(m1_cisd_arr[i])
            if m1_cisd != pending_bias:
                continue

            # 3) Compute SL / TP distances from this bar's close.
            last_close = m1_close[i]
            if pending_bias == +1:
                sl_distance = last_close - (pivot_price - buf)
            else:
                sl_distance = (pivot_price + buf) - last_close
            if sl_distance <= 0:
                pending_bias = 0
                tap_seen = False
                continue
            signal[i] = int(pending_bias)
            sl_pts[i] = float(sl_distance)
            tp_pts[i] = float(sl_distance * rr)
            # Reset state — next bar's open is the entry.
            pending_bias = 0
            tap_seen = False

        return pd.DataFrame(
            {"signal": signal, "sl_points": sl_pts, "tp_points": tp_pts},
            index=df.index,
        )
