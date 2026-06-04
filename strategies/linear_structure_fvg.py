"""LINEAR STRUCTURE & FVG ALIGNMENT (15m + 1m).

Pure line-chart structural system. The 15m timeframe defines the
*Storyline* (close-only pivots → close-only CISD → 3-bar FVG); the
1m timeframe handles *tap → close-only MSS → Entry Field check →
market entry on next open*. **Open price is irrelevant to structure
everywhere** — pivots, CISD, FVG invalidation, and the SL pivot
price all read raw ``close``. ``open`` is used only to compute the
3-bar FVG geometry (which is wick-based by SMC convention) and for
1m bar tap detection (any wick touch counts).

Algorithm
---------
**HTF (15m).**

1. **Close-only pivots.** ``close[i]`` is a pivot high iff
   ``close[i] > close[i-k]`` and ``close[i] > close[i+k]`` for every
   ``1 <= k <= htf_pivot_lookback`` (default 1). Symmetric for lows.
   No body extremes, no wicks — just the line.

2. **Close-only CISD.** A 15m close strictly above the most recent
   confirmed close-pivot HIGH fires a *bullish CISD*; symmetric for
   bear (``close < last close-pivot LOW``).

3. **Anchor pivot tracking.** The bias is "armed" against the
   specific pivot that was broken. Consecutive bull CISDs against
   the SAME pivot index are a no-op (same impulsive leg). A NEW
   pivot break (different anchor index) OR opposite-direction CISD
   creates a *fresh setup*: drop any locked FVG, re-latch the SL
   pivot, and search a new FVG.

4. **15m FVG search.** After the CISD, scan **forward** for the
   first not-yet-invalidated standard 3-bar FVG. The search window
   starts at ``cisd_idx - 1`` so an FVG centred on the CISD bar
   itself (formed by the impulsive leg) is eligible. **No OTE / Fib
   / midpoint filter** — every not-invalidated FVG is valid.

5. **Close-only FVG invalidation.** A bull FVG dies when ``close <
   gap_low``; a bear FVG dies when ``close > gap_high``. Wicks and
   open prices are ignored — pure line-chart logic.

6. **Counter-CISD on a live position.** If we hold a long and a
   bearish CISD prints on 15m close, the position is closed at
   market immediately — structural context has flipped against us.

**LTF (1m).**

1. **Tap.** A 1m bar's wick or body touching the FVG zone
   (``bar_low <= fvg_high AND bar_high >= fvg_low``) registers the
   tap. Wicks count for the touch (per spec).

2. **Close-only 1m MSS.** *After* the tap (strictly later bar), wait
   for a close-only CISD in the bias direction. Default 1m lookback
   is 2 bars/side (5-minute window) to filter intra-tap noise.

3. **Entry Field corridor.** From the FVG width ``W = high - low``,
   the corridor is ``[low - 0.20·W, high + 0.20·W]``. If the
   MSS-bar close is outside this corridor, the setup is consumed
   without an entry.

4. **Execution.** Market entry at the next 1m open (engine fills via
   ``buy_at_open`` / ``sell_at_open``). One trade per CISD-anchor
   pair; the setup is consumed regardless of fill outcome.

**Risk.**
* Risk = 1% of equity per trade (configurable).
* TP = ``rr × SL`` (default 2RR).
* SL = ``close[pl_idx]`` for long (close of last close-low pivot
  before the impulsive leg) / ``close[ph_idx]`` for short, with
  optional asset-side buffer in points. **Open is never used.**
* Lot cap: per-100k linear scaling (12 lots/$100k → 24/$200k → 60/
  $500k …). Strict-cancel if the risk-derived size exceeds the cap.
* Force EOD close at the asset's ``eod_close`` (typically 22:00).

**Trading window.**
The OTT filter (default 09:00–18:00) blocks **new entries** outside
the window. Existing positions remain open until SL/TP/EOD/Counter-
CISD.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from engine.strategy.context import Param, Strategy
from engine.core.linear import (
    first_active_fvg_after,
    last_close_pivots,
    linear_cisd_last_close,
    linear_cisd_series_close,
    linear_last_close_pivots,
)

logger = logging.getLogger("StrategyTester.strategies.linear_structure_fvg")


class LinearStructureFVG(Strategy):
    """LINEAR STRUCTURE & FVG ALIGNMENT — 15m HTF + 1m LTF, close-only structure."""

    name = "LINEAR STRUCTURE & FVG ALIGNMENT (15m-1m)"

    PARAMS = [
        Param(
            name="risk_pct",
            label="Risk per trade (%)",
            kind="float",
            default=1.0,
            min=0.1,
            max=5.0,
            step=0.1,
            tooltip="Account percentage risked per trade. Spec: strictly 1%.",
        ),
        Param(
            name="strict_lot_cap",
            label="Strict lot cap (cancel if over)",
            kind="bool",
            default=True,
            tooltip="When the calculated lot exceeds max_lots_per_100k or the "
            "broker absolute cap, CANCEL the trade instead of clamping. "
            "Spec: 12 lots @ $100k → 24 @ $200k → 60 @ $500k.",
        ),
        Param(
            name="ott_filter",
            label="OTT trading-hours filter (09:00-18:00)",
            kind="bool",
            default=True,
            tooltip="Block new entries outside the asset's trading window. "
            "Existing positions are unaffected (exit only on SL/TP/EOD).",
        ),
        Param(
            name="force_eod_close",
            label="Force close at EOD (22:00)",
            kind="bool",
            default=True,
            tooltip="Liquidate any open position at the asset's eod_close.",
        ),
        Param(
            name="entry_field_pct",
            label="Entry Field (% of FVG width)",
            kind="float",
            default=0.20,
            min=0.0,
            max=1.0,
            step=0.05,
            tooltip="Entry must lie within FVG ± (width × this fraction). "
            "Outside this corridor → setup is ignored. Default 0.20.",
        ),
        Param(
            name="rr",
            label="Take-profit RR multiple",
            kind="float",
            default=2.0,
            min=0.5,
            max=10.0,
            step=0.5,
            tooltip="TP distance = RR × SL distance. Spec: fixed 2RR.",
        ),
        Param(
            name="htf_pivot_lookback",
            label="HTF (15m) pivot lookback per side",
            kind="int",
            default=1,
            min=1,
            max=10,
            step=1,
            tooltip="A 15m close-only pivot needs this many bars on each side. "
            "Default 1 = simplest line-chart turning point.",
        ),
        Param(
            name="ltf_pivot_lookback",
            label="LTF (1m) pivot lookback per side",
            kind="int",
            default=2,
            min=1,
            max=10,
            step=1,
            tooltip="A 1m close-only pivot needs this many bars on each side. "
            "Default 2 = 5-minute window for 1m MSS confirmation.",
        ),
        Param(
            name="sl_buffer_pts",
            label="SL buffer beyond pivot (points)",
            kind="float",
            default=0.0,
            min=0.0,
            max=20.0,
            step=0.5,
            tooltip="Extra points beyond the structural close-pivot for SL. "
            "Default 0 = stop sits exactly on the pivot's close.",
        ),
        Param(
            name="fvg_search_lookahead",
            label="FVG forward-search window (15m bars)",
            kind="int",
            default=20,
            min=3,
            max=200,
            step=1,
            tooltip="Maximum number of 15m bars after a CISD to search for an "
            "FVG. If no valid FVG forms within this window, the setup "
            "expires and we wait for the next CISD.",
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Per-setup state — reset whenever a fresh CISD prints, the FVG
        # is invalidated, or a trade is queued. ``_cisd_idx`` is the HTF
        # bar index of the CISD that opened the bias; FVG search uses
        # ``cisd_idx - 1`` so an FVG centred on the CISD bar itself is
        # eligible (the impulsive leg's gap forms exactly there).
        self._pending_bias: int = 0  # +1 long, -1 short, 0 idle
        self._cisd_idx: int = -1
        self._fvg_low: float = 0.0
        self._fvg_high: float = 0.0
        self._fvg_locked: bool = False  # True once a valid FVG is latched
        self._sl_pivot_price: float = 0.0  # close-low (long) / close-high (short)
        self._tap_seen: bool = False
        self._tap_bar: int = -1  # 1m bar index where the tap fired
        # Index of the structural pivot that the CURRENT bias is built on.
        # We only re-arm with a fresh setup when a *different* pivot is
        # broken — i.e. price formed a NEW pivot and broke that. Repeat
        # CISD signals against the SAME pivot (consecutive higher closes
        # without an intervening pullback / new pivot) keep the setup
        # armed and do not re-search the FVG. Per user clarification:
        # "новий bull CISD" means breaking the NEXT peak, not the same
        # one. ``-1`` means no pivot armed yet.
        self._anchor_pivot_idx: int = -1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _reset_setup(self) -> None:
        self._pending_bias = 0
        self._cisd_idx = -1
        self._fvg_low = 0.0
        self._fvg_high = 0.0
        self._fvg_locked = False
        self._sl_pivot_price = 0.0
        self._tap_seen = False
        self._tap_bar = -1
        self._anchor_pivot_idx = -1

    def _ott_blocks(self, ctx) -> bool:
        if not self.params.get("ott_filter", True):
            return False
        return not ctx.is_trading_hours()

    def _entry_field_ok(self, entry_price: float) -> bool:
        """Entry must be within ``[low - p·W, high + p·W]`` corridor."""
        if not self._fvg_locked:
            return False
        width = self._fvg_high - self._fvg_low
        if width <= 0:
            return False
        pad = width * float(self.params.get("entry_field_pct", 0.20))
        lo = self._fvg_low - pad
        hi = self._fvg_high + pad
        return lo <= entry_price <= hi

    # ------------------------------------------------------------------
    # 15m context hook
    # ------------------------------------------------------------------
    def on_15m_close(self, ctx) -> None:
        """Close-only structural processing per the LSF spec.

        Each 15m close:
          1. Compute close-only CISD on the current 15m view.
          2. **Any** non-zero CISD (same OR opposite direction) starts a
             *fresh* setup: reset state, latch new ``cisd_idx`` and SL
             pivot. Per the user's clarification: "кожен новий CISD =
             свіжий setup" — a repeated bull CISD invalidates the old
             FVG candidate and forces a search for a newer one.
          3. **Counter-CISD while in a live position** closes that
             position at market: structural context flipped against us.
          4. With a bias armed, try to lock the first not-invalidated
             3-bar FVG strictly after ``cisd_idx`` (gap geometry uses
             wicks, invalidation uses *close only*).
          5. Once locked, check close-only invalidation: bull dies on
             ``close < gap_low``; bear on ``close > gap_high``.
        """
        lookback = int(self.params.get("htf_pivot_lookback", 1))
        view = ctx.get_tf("15m")
        if len(view) < 2 * lookback + 2:
            return
        ctx_open = np.asarray(view.open, dtype=np.float64)
        ctx_high = np.asarray(view.high, dtype=np.float64)
        ctx_low = np.asarray(view.low, dtype=np.float64)
        ctx_close = np.asarray(view.close, dtype=np.float64)
        n_htf = len(ctx_close)

        cisd = linear_cisd_last_close(ctx_close, lookback=lookback)
        last_idx = n_htf - 1

        # 0) Counter-CISD on a LIVE position: exit at market — structural
        # context has flipped against us. Per spec: "Якщо ми вже в угоді,
        # і на 15m приходить протилежний CISD — виходимо негайно."
        if ctx.position is not None and cisd != 0:
            pos_dir = +1 if ctx.position.direction == "long" else -1
            if cisd == -pos_dir:
                ctx.close(reason="LSF counter-CISD exit")
                # Setup will re-arm below from this very CISD.

        # 1) Identify what pivot (if any) the current bar's CISD broke.
        # ``last_close_pivots`` returns the most recent confirmed close
        # pivots up to the current bar; bull CISD broke ``ph`` (high),
        # bear CISD broke ``pl`` (low). A repeat break of the SAME pivot
        # (same anchor index, same bias) is NOT a fresh setup — it's the
        # same impulsive leg pushing further. A NEW pivot (different
        # index) OR opposite-direction CISD = fresh setup.
        if cisd != 0:
            ph, pl = last_close_pivots(ctx_close, lookback=lookback)
            if cisd == +1:
                broken = ph
            else:
                broken = pl
            if broken is None:
                # Should not happen if CISD fired, but be defensive.
                pass
            else:
                anchor_idx = int(broken.index)
                same_anchor = (
                    self._pending_bias == int(cisd) and self._anchor_pivot_idx == anchor_idx
                )
                if not same_anchor:
                    # Fresh setup — re-latch.
                    self._reset_setup()
                    self._pending_bias = int(cisd)
                    self._cisd_idx = last_idx
                    self._anchor_pivot_idx = anchor_idx
                    if cisd == +1:
                        if pl is None:
                            self._reset_setup()
                            return
                        # SL = close[pl.index] — line-chart pivot price.
                        self._sl_pivot_price = pl.price
                    else:
                        if ph is None:
                            self._reset_setup()
                            return
                        self._sl_pivot_price = ph.price

        # No bias → nothing more to do (no setup armed).
        if self._pending_bias == 0:
            return

        # 2) Try to lock an FVG if not yet locked. Search starts at
        # ``cisd_idx - 1`` so an FVG centred on the CISD bar itself
        # (formed by the impulsive leg) is eligible.
        if not self._fvg_locked:
            max_la = int(self.params.get("fvg_search_lookahead", 20))
            fvg = first_active_fvg_after(
                ctx_open,
                ctx_high,
                ctx_low,
                ctx_close,
                direction=self._pending_bias,
                min_index=max(self._cisd_idx - 1, -1),
                max_lookahead=max_la,
                invalidate_on="close",
            )
            if fvg is not None:
                self._fvg_low = fvg.low
                self._fvg_high = fvg.high
                self._fvg_locked = True
            else:
                # Setup expires if no FVG forms within the search window.
                if last_idx - self._cisd_idx >= max_la:
                    self._reset_setup()
                return

        # 3) Already locked → close-only invalidation per spec:
        # bull dies on close < gap_low; bear on close > gap_high.
        last_close = float(ctx_close[-1])
        if self._pending_bias == +1 and last_close < self._fvg_low:
            self._reset_setup()
        elif self._pending_bias == -1 and last_close > self._fvg_high:
            self._reset_setup()

    # ------------------------------------------------------------------
    # 1m hook
    # ------------------------------------------------------------------
    def on_candle(self, ctx) -> None:
        if ctx.position is not None or ctx._engine.pending_order is not None:
            return
        if self._pending_bias == 0 or not self._fvg_locked:
            return
        if self._ott_blocks(ctx):
            return
        ltf_lookback = int(self.params.get("ltf_pivot_lookback", 2))
        if len(ctx.data) < 2 * ltf_lookback + 2:
            return

        # 1) Tap (wicks count for the touch).
        bar_high = float(ctx.data.high[-1])
        bar_low = float(ctx.data.low[-1])
        cur_idx = len(ctx.data) - 1
        if not self._tap_seen:
            tapped = (bar_low <= self._fvg_high) and (bar_high >= self._fvg_low)
            if tapped:
                self._tap_seen = True
                self._tap_bar = cur_idx
            return  # MSS only counts on bars STRICTLY AFTER the tap.

        # MSS-after-tap rule: do not evaluate MSS on the same bar as the tap.
        if cur_idx <= self._tap_bar:
            return

        # 2) 1m MSS (close-only line-chart CISD).
        m1_close = np.asarray(ctx.data.close, dtype=np.float64)
        m1_cisd = linear_cisd_last_close(m1_close, lookback=ltf_lookback)
        if m1_cisd != self._pending_bias:
            return

        # 3) Entry Field check (entry estimate = MSS-bar close).
        entry_price = float(m1_close[-1])
        if not self._entry_field_ok(entry_price):
            # One-shot per setup: corridor miss consumes the FVG.
            self._reset_setup()
            return

        # 4) Sizing (per-100k strict cap).
        buf = float(self.params.get("sl_buffer_pts", 0.0))
        rr = float(self.params.get("rr", 2.0))
        risk_pct = float(self.params.get("risk_pct", 1.0))
        strict = bool(self.params.get("strict_lot_cap", True))
        tick = max(ctx.asset.tick_size, 1e-9)

        if self._pending_bias == +1:
            sl_price = self._sl_pivot_price - buf
            sl_dist = entry_price - sl_price
            if sl_dist <= 0:
                self._reset_setup()
                return
            tp_price = entry_price + sl_dist * rr
            sl_pts = sl_dist / tick
            lot = ctx.calculate_lot(
                sl_points=sl_pts,
                risk_pct=risk_pct,
                strict=strict,
            )
            if lot is None:
                # Strict cap hit → trade cancelled, setup consumed.
                self._reset_setup()
                return
            ctx.buy_at_open(
                volume=lot,
                sl=sl_price,
                tp=tp_price,
                reason="LSF long entry",
                tag="lsf_long",
            )
            self._reset_setup()
        else:
            sl_price = self._sl_pivot_price + buf
            sl_dist = sl_price - entry_price
            if sl_dist <= 0:
                self._reset_setup()
                return
            tp_price = entry_price - sl_dist * rr
            sl_pts = sl_dist / tick
            lot = ctx.calculate_lot(
                sl_points=sl_pts,
                risk_pct=risk_pct,
                strict=strict,
            )
            if lot is None:
                self._reset_setup()
                return
            ctx.sell_at_open(
                volume=lot,
                sl=sl_price,
                tp=tp_price,
                reason="LSF short entry",
                tag="lsf_short",
            )
            self._reset_setup()

    # ------------------------------------------------------------------
    # Fast Quant (vectorised) path — entry signals only.
    #
    # Mirrors on_15m_close + on_candle in a single pass. The OTT and
    # EOD-close behaviours are enforced by the engine (asset window
    # + ``enforce_eod_close`` flag), not here.
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

        htf_lookback = int(self.params.get("htf_pivot_lookback", 1))
        ltf_lookback = int(self.params.get("ltf_pivot_lookback", 2))
        max_la = int(self.params.get("fvg_search_lookahead", 20))
        buf = float(self.params.get("sl_buffer_pts", 0.0))
        rr = float(self.params.get("rr", 2.0))
        ef_pct = float(self.params.get("entry_field_pct", 0.20))

        # Vectorised engine passes Pascal-cased columns; tests use lowercase.
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

        # Build 15m aggregate using the same boundary detection as the
        # engine's TimeframeAggregator (Visual ↔ vectorised parity).
        bucket_starts = pd.Series(times).dt.floor("15min").values
        n_bars = len(bucket_starts)
        boundary = np.empty(n_bars, dtype=bool)
        boundary[0] = True
        boundary[1:] = bucket_starts[1:] != bucket_starts[:-1]
        first_idx = np.where(boundary)[0]
        n_htf = len(first_idx)
        if n_htf == 0:
            return pd.DataFrame(
                {"signal": signal, "sl_points": sl_pts, "tp_points": tp_pts},
                index=df.index,
            )

        htf_open = np.empty(n_htf, dtype=np.float64)
        htf_high = np.empty(n_htf, dtype=np.float64)
        htf_low = np.empty(n_htf, dtype=np.float64)
        htf_close = np.empty(n_htf, dtype=np.float64)
        htf_close_idx = np.empty(n_htf, dtype=np.int64)
        for k in range(n_htf):
            start = int(first_idx[k])
            end = int(first_idx[k + 1] - 1) if k + 1 < n_htf else n_bars - 1
            htf_open[k] = m1_open[start]
            htf_high[k] = m1_high[start : end + 1].max()
            htf_low[k] = m1_low[start : end + 1].min()
            htf_close[k] = m1_close[end]
            htf_close_idx[k] = end

        # ----- Pre-compute every per-bar primitive ONCE (O(n) total).
        # Close-only line-chart logic: pivots, CISD, and SL price all
        # read raw ``close``. ``open`` is irrelevant to structure.
        htf_cisd_arr = linear_cisd_series_close(htf_close, lookback=htf_lookback)
        htf_last_high_idx, htf_last_low_idx = linear_last_close_pivots(
            htf_close,
            lookback=htf_lookback,
        )
        m1_cisd_arr = linear_cisd_series_close(m1_close, lookback=ltf_lookback)

        # An HTF bar k becomes available for processing on 1m bar
        # ``htf_close_idx[k] + 1`` (mirrors on_<tf>_close hook timing).
        htf_completed = np.searchsorted(htf_close_idx + 1, np.arange(n), side="right")

        # State (mirrors __init__).
        pending_bias = 0
        cisd_idx = -1
        fvg_low_v = 0.0
        fvg_high_v = 0.0
        fvg_locked = False
        sl_pivot = 0.0
        tap_seen = False
        tap_bar = -1
        anchor_pivot_idx = -1  # Pivot the current bias was opened against.

        last_processed_htf = -1
        for i in range(n):
            n_done = int(htf_completed[i])
            # 1) Process newly completed HTF bars up to this 1m bar.
            while last_processed_htf < n_done - 1:
                last_processed_htf += 1
                k = last_processed_htf
                if k + 1 < 2 * htf_lookback + 2:
                    continue
                ho = htf_open[: k + 1]
                hh = htf_high[: k + 1]
                hl = htf_low[: k + 1]
                hc = htf_close[: k + 1]
                cisd = int(htf_cisd_arr[k])

                # CISD with a NEW anchor pivot (or opposite direction) =
                # fresh setup. CISD against the SAME pivot we're already
                # armed on = ignored (same impulsive leg). This mirrors
                # the visual on_15m_close logic.
                if cisd != 0:
                    if cisd == +1:
                        anchor_idx = int(htf_last_high_idx[k])
                    else:
                        anchor_idx = int(htf_last_low_idx[k])
                    same_anchor = pending_bias == int(cisd) and anchor_pivot_idx == anchor_idx
                    if anchor_idx >= 0 and not same_anchor:
                        # Fresh setup.
                        pending_bias = int(cisd)
                        cisd_idx = k
                        fvg_locked = False
                        tap_seen = False
                        tap_bar = -1
                        anchor_pivot_idx = anchor_idx
                        if cisd == +1:
                            pl_idx = int(htf_last_low_idx[k])
                            if pl_idx < 0:
                                pending_bias = 0
                                cisd_idx = -1
                                anchor_pivot_idx = -1
                                continue
                            sl_pivot = float(htf_close[pl_idx])
                        else:
                            ph_idx = int(htf_last_high_idx[k])
                            if ph_idx < 0:
                                pending_bias = 0
                                cisd_idx = -1
                                anchor_pivot_idx = -1
                                continue
                            sl_pivot = float(htf_close[ph_idx])

                if pending_bias == 0:
                    continue

                # Lock FVG if not yet locked. Start search from
                # ``cisd_idx - 1`` so an FVG centred on the CISD bar
                # itself is eligible.
                if not fvg_locked:
                    fvg = first_active_fvg_after(
                        ho,
                        hh,
                        hl,
                        hc,
                        direction=pending_bias,
                        min_index=max(cisd_idx - 1, -1),
                        max_lookahead=max_la,
                        invalidate_on="close",
                    )
                    if fvg is not None:
                        fvg_low_v = fvg.low
                        fvg_high_v = fvg.high
                        fvg_locked = True
                    elif k - cisd_idx >= max_la:
                        # Setup expired without an FVG.
                        pending_bias = 0
                        cisd_idx = -1
                        anchor_pivot_idx = -1
                        continue

                # Close-only invalidation per LSF spec:
                # bull dies on close < gap_low; bear on close > gap_high.
                if fvg_locked:
                    last_c = float(hc[-1])
                    if pending_bias == +1 and last_c < fvg_low_v:
                        pending_bias = 0
                        cisd_idx = -1
                        fvg_locked = False
                        tap_seen = False
                        tap_bar = -1
                        anchor_pivot_idx = -1
                    elif pending_bias == -1 and last_c > fvg_high_v:
                        pending_bias = 0
                        cisd_idx = -1
                        fvg_locked = False
                        tap_seen = False
                        tap_bar = -1
                        anchor_pivot_idx = -1

            # 2) On the 1m bar itself.
            if pending_bias == 0 or not fvg_locked:
                continue
            bh = m1_high[i]
            bl = m1_low[i]
            if not tap_seen:
                if bl <= fvg_high_v and bh >= fvg_low_v:
                    tap_seen = True
                    tap_bar = i
                continue  # MSS strictly after tap.

            if i <= tap_bar:
                continue

            if i + 1 < 2 * ltf_lookback + 2:
                continue
            m1_cisd = int(m1_cisd_arr[i])
            if m1_cisd != pending_bias:
                continue

            entry_price = m1_close[i]
            width = fvg_high_v - fvg_low_v
            if width <= 0:
                pending_bias = 0
                cisd_idx = -1
                fvg_locked = False
                tap_seen = False
                tap_bar = -1
                anchor_pivot_idx = -1
                continue
            pad = width * ef_pct
            if not (fvg_low_v - pad <= entry_price <= fvg_high_v + pad):
                # Corridor miss → consume the setup.
                pending_bias = 0
                cisd_idx = -1
                fvg_locked = False
                tap_seen = False
                tap_bar = -1
                anchor_pivot_idx = -1
                continue

            if pending_bias == +1:
                sl_distance = entry_price - (sl_pivot - buf)
            else:
                sl_distance = (sl_pivot + buf) - entry_price
            if sl_distance <= 0:
                pending_bias = 0
                cisd_idx = -1
                fvg_locked = False
                tap_seen = False
                tap_bar = -1
                anchor_pivot_idx = -1
                continue

            signal[i] = int(pending_bias)
            sl_pts[i] = float(sl_distance)
            tp_pts[i] = float(sl_distance * rr)
            # Consume setup; next CISD re-arms.
            pending_bias = 0
            cisd_idx = -1
            fvg_locked = False
            tap_seen = False
            tap_bar = -1
            anchor_pivot_idx = -1

        return pd.DataFrame(
            {"signal": signal, "sl_points": sl_pts, "tp_points": tp_pts},
            index=df.index,
        )
