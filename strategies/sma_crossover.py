"""SMA Crossover — example strategy bundled with AGREGAT.

This file demonstrates the **dual-API** introduced in Phase 2 of the
platform plan:

* :pymeth:`SMACrossover.on_candle` — step-by-step logic suited to the
  Visual / live-ready :class:`engine.backtest_engine.BacktestEngine`.
  Useful for debugging, BarReplay, drawing tools, and (eventually) live
  trading once an ``ExecutionModel`` is swapped in for a real broker
  gateway.
* :pymeth:`SMACrossover.vectorized_signals` — NumPy-friendly variant
  consumed by :func:`engine.vectorized_engine.run_vectorized` for
  optimisation sweeps (millions of bars in seconds, no UI thread).

Trading idea
============
The classic 20-period / 50-period simple moving average crossover. We use
the ``current_close`` only after both averages are well-defined (we wait
for ``slow`` periods of history so the strategy doesn't fire on warm-up).

* **Long** entry — fast SMA crosses **above** slow SMA, no position open.
* **Short** entry — fast SMA crosses **below** slow SMA, no position open.

Risk per trade: 1 % of equity, sized via :pymeth:`Context.calculate_lot`
(visual path) or the engine's built-in lot calc (vectorized path).

Stops & targets:
* Stop-loss = 1.5 × ATR(14) away from entry close.
* Take-profit = 3.0 × ATR(14) away from entry close — i.e. R:R ≈ 2:1.

Both directions are exercised so the Phase 1 SL/TP fix
(short-side SL/TP triggered against the Ask price) is visible in the
trade list.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.core.indicators import atr as atr_indicator

# Strategy is intentionally not subclassed: the loader detects either
# `on_candle` or `vectorized_signals` on duck-typed classes.


FAST = 20
SLOW = 50
ATR_LEN = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 3.0
RISK_PCT = 1.0


class SMACrossover:
    """20/50 SMA crossover with ATR-based exits."""

    name = "SMA Crossover"

    # ------------------------------------------------------------------
    # Visual / live-ready path: invoked once per 1m candle.
    # ------------------------------------------------------------------
    def on_candle(self, ctx) -> None:  # noqa: D401 — interface method
        # Need at least SLOW + ATR_LEN bars of history before we can
        # compute both SMAs and a meaningful ATR. ``ctx.data`` exposes
        # read-only NumPy slices that respect the Zero-Future Rule.
        if len(ctx.data) < SLOW + ATR_LEN:
            return

        closes = ctx.data.close
        highs = ctx.data.high
        lows = ctx.data.low

        fast_now = closes[-FAST:].mean()
        slow_now = closes[-SLOW:].mean()
        fast_prev = closes[-FAST - 1 : -1].mean()
        slow_prev = closes[-SLOW - 1 : -1].mean()

        atr_series = atr_indicator(highs, lows, closes, period=ATR_LEN)
        atr_value = atr_series[-1]
        if not np.isfinite(atr_value) or atr_value <= 0:
            return

        # Skip if a position is already on or there's a queued order.
        if ctx.position is not None or ctx._engine.pending_order is not None:
            return

        close_now = float(closes[-1])
        sl_pts = ATR_SL_MULT * atr_value
        tp_pts = ATR_TP_MULT * atr_value

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_dn = fast_prev >= slow_prev and fast_now < slow_now
        if not (crossed_up or crossed_dn):
            return

        lot = ctx.calculate_lot(sl_points=sl_pts, risk_pct=RISK_PCT)
        if not lot or lot <= 0:
            return

        if crossed_up:
            sl_price = close_now - sl_pts
            tp_price = close_now + tp_pts
            ctx.buy_at_open(
                volume=lot,
                sl=sl_price,
                tp=tp_price,
                reason="SMA20 crossed above SMA50",
                tag="sma_x_up",
            )
        else:
            sl_price = close_now + sl_pts
            tp_price = close_now - tp_pts
            ctx.sell_at_open(
                volume=lot,
                sl=sl_price,
                tp=tp_price,
                reason="SMA20 crossed below SMA50",
                tag="sma_x_dn",
            )

    # ------------------------------------------------------------------
    # Fast Quant / vectorized path.
    # ------------------------------------------------------------------
    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame with the columns required by the
        vectorized engine: ``signal`` (+1/-1/0), ``sl_points``,
        ``tp_points``, ``risk_pct``.

        The signal is emitted on the bar where the crossover happens
        (i.e. the same bar the visual path queues a ``buy_at_open`` /
        ``sell_at_open`` for execution at the next bar's open).
        """

        # Accept either AGREGAT's standard PascalCase OHLCV columns or the
        # lower-case convention used by some external feeds.
        def col(name: str) -> str:
            return name if name in df.columns else name.capitalize()

        close = df[col("close")].to_numpy(dtype=np.float64)
        high = df[col("high")].to_numpy(dtype=np.float64)
        low = df[col("low")].to_numpy(dtype=np.float64)
        n = len(df)

        fast = pd.Series(close).rolling(FAST).mean().to_numpy()
        slow = pd.Series(close).rolling(SLOW).mean().to_numpy()
        atr = atr_indicator(high, low, close, period=ATR_LEN)

        signal = np.zeros(n, dtype=np.int8)
        sl_pts = np.zeros(n, dtype=np.float64)
        tp_pts = np.zeros(n, dtype=np.float64)

        # Crossover detection (bar i-1 vs bar i). Skip warm-up bars.
        for i in range(SLOW + ATR_LEN, n):
            if (
                not np.isfinite(fast[i])
                or not np.isfinite(slow[i])
                or not np.isfinite(fast[i - 1])
                or not np.isfinite(slow[i - 1])
                or not np.isfinite(atr[i])
                or atr[i] <= 0
            ):
                continue
            up = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
            dn = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
            if up:
                signal[i] = 1
            elif dn:
                signal[i] = -1
            if signal[i] != 0:
                sl_pts[i] = ATR_SL_MULT * atr[i]
                tp_pts[i] = ATR_TP_MULT * atr[i]

        return pd.DataFrame(
            {
                "signal": signal,
                "sl_points": sl_pts,
                "tp_points": tp_pts,
                "risk_pct": np.full(n, RISK_PCT, dtype=np.float64),
            },
            index=df.index,
        )
