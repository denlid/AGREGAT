"""Synthetic data generator shared across the AGREGAT bench scripts.

The bench harness deliberately does **not** depend on real OHLC files —
those vary by user and would give noisy baselines. Instead we draw a
mildly trending, sinusoidally-modulated random walk with a fixed seed
so two consecutive bench runs on the same machine produce timings that
can be compared meaningfully.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_ohlcv(
    n_bars: int = 200_000,
    *,
    seed: int = 1337,
    pascal_case: bool = False,
) -> pd.DataFrame:
    """Return an OHLCV frame of ``n_bars`` rows.

    Parameters
    ----------
    n_bars
        Number of 1-minute bars to synthesise. The roadmap targets a
        200 000-bar baseline; smaller datasets keep CI fast while still
        exercising the same code paths.
    seed
        RNG seed. Pinned so two bench runs on the same host are stable.
    pascal_case
        Use ``Open / High / Low / Close / Volume`` column casing
        (vectorised engine convention) when ``True``; default is
        lowercase (visual engine convention).
    """
    rng = np.random.default_rng(seed)
    drift = np.linspace(0.0, 50.0, n_bars)
    sine = 12.0 * np.sin(np.linspace(0.0, 14.0 * np.pi, n_bars))
    noise = rng.normal(0.0, 1.0, n_bars).cumsum()
    close = 100.0 + drift + sine + noise
    high = close + rng.uniform(0.2, 1.5, n_bars)
    low = close - rng.uniform(0.2, 1.5, n_bars)
    open_ = np.concatenate(([close[0]], close[:-1]))
    times = pd.date_range("2024-01-02 09:30", periods=n_bars, freq="1min")
    cols = (
        ["Open", "High", "Low", "Close", "Volume"]
        if pascal_case
        else ["open", "high", "low", "close", "volume"]
    )
    return pd.DataFrame(
        {
            "time": times,
            cols[0]: open_,
            cols[1]: high,
            cols[2]: low,
            cols[3]: close,
            cols[4]: 1.0,
        }
    )
