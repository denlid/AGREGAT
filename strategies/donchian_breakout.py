"""Donchian Channel Breakout — example strategy bundled with AGREGAT.

A second reference strategy showing how to write a breakout system that
implements both APIs:

* :pymeth:`DonchianBreakout.on_candle` — the visual path (one bar at a
  time) for use with the standard backtest engine, BarReplay, and the
  upcoming live broker gateway.
* :pymeth:`DonchianBreakout.vectorized_signals` — NumPy-friendly path
  for the fast-quant engine.

Trading idea
============
20-bar Donchian breakout (Turtle-style). When the close prints above the
prior 20-bar high we go long; when it prints below the prior 20-bar low
we go short. The position is closed on the *opposite* breakout via
hard SL — there is no separate exit signal in this version. This keeps
the example small yet exercises:

* Both long and short directions (so you can see Phase 1's Ask-side
  SL/TP fix on shorts).
* Long-running trades (because Donchian channels expand slowly), making
  for an easy visual debug experience.

Risk per trade: 1.5 % of equity. Stop-loss is the *opposite* channel
edge at the time of entry (i.e. the recent low for longs, the recent
high for shorts). Take-profit is fixed at 2× the SL distance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.core.indicators import donchian_channel

LOOKBACK = 20
RR_MULT = 2.0  # take-profit = RR_MULT * sl_distance
RISK_PCT = 1.5


class DonchianBreakout:
    """20-bar Donchian breakout with 2:1 reward/risk."""

    name = "Donchian Breakout"

    # ------------------------------------------------------------------
    # Visual / live-ready path
    # ------------------------------------------------------------------
    def on_candle(self, ctx) -> None:
        # ``ctx.data`` is the modern Zero-Future-safe data window; the
        # NumPy slices it returns are read-only views on the engine's
        # storage so a strategy cannot peek at future bars.
        if len(ctx.data) < LOOKBACK + 1:
            return

        highs = ctx.data.high
        lows = ctx.data.low
        # Lookback window — exclude the current bar so the breakout is
        # on truly new territory.
        recent_high = float(highs[-LOOKBACK - 1 : -1].max())
        recent_low = float(lows[-LOOKBACK - 1 : -1].min())

        if ctx.position is not None or ctx._engine.pending_order is not None:
            return

        close = float(ctx.data.close[-1])
        if close > recent_high:
            sl_price = recent_low
            sl_price_dist = close - sl_price
            if sl_price_dist <= 0:
                return
            tp_price_dist = sl_price_dist * RR_MULT
            tp_price = close + tp_price_dist
            sl_pts = sl_price_dist / ctx.asset.tick_size
            lot = ctx.calculate_lot(sl_points=sl_pts, risk_pct=RISK_PCT)
            if lot:
                ctx.buy_at_open(
                    volume=lot,
                    sl=sl_price,
                    tp=tp_price,
                    reason="Donchian high break",
                    tag="don_long",
                )
        elif close < recent_low:
            sl_price = recent_high
            sl_price_dist = sl_price - close
            if sl_price_dist <= 0:
                return
            tp_price_dist = sl_price_dist * RR_MULT
            tp_price = close - tp_price_dist
            sl_pts = sl_price_dist / ctx.asset.tick_size
            lot = ctx.calculate_lot(sl_points=sl_pts, risk_pct=RISK_PCT)
            if lot:
                ctx.sell_at_open(
                    volume=lot,
                    sl=sl_price,
                    tp=tp_price,
                    reason="Donchian low break",
                    tag="don_short",
                )

    # ------------------------------------------------------------------
    # Fast Quant / vectorized path
    # ------------------------------------------------------------------
    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        def col(name: str) -> str:
            return name if name in df.columns else name.capitalize()

        close = df[col("close")].to_numpy(dtype=np.float64)
        high = df[col("high")].to_numpy(dtype=np.float64)
        low = df[col("low")].to_numpy(dtype=np.float64)
        n = len(df)

        upper, lower = donchian_channel(high, low, LOOKBACK, exclude_current=True)

        signal = np.zeros(n, dtype=np.int8)
        sl_pts = np.zeros(n, dtype=np.float64)
        tp_pts = np.zeros(n, dtype=np.float64)

        for i in range(LOOKBACK, n):
            if not (np.isfinite(upper[i]) and np.isfinite(lower[i])):
                continue
            if close[i] > upper[i]:
                dist = close[i] - lower[i]
                if dist > 0:
                    signal[i] = 1
                    sl_pts[i] = dist
                    tp_pts[i] = dist * RR_MULT
            elif close[i] < lower[i]:
                dist = upper[i] - close[i]
                if dist > 0:
                    signal[i] = -1
                    sl_pts[i] = dist
                    tp_pts[i] = dist * RR_MULT

        return pd.DataFrame(
            {
                "signal": signal,
                "sl_points": sl_pts,
                "tp_points": tp_pts,
                "risk_pct": np.full(n, RISK_PCT, dtype=np.float64),
            },
            index=df.index,
        )
