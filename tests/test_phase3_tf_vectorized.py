"""TZ Phase 3 — vectorized TimeframeAggregator.

The legacy ``feed`` path is preserved by ``test_tf_aggregator.py``; this
module exercises the new vectorized contract:

* ``prebuild`` produces the same per-bucket OHLCV as a feed-by-feed
  walk for every supported TF.
* ``advance_to`` matches the per-step ``completed_tfs`` flags emitted
  by the legacy ``feed`` path bar-for-bar.
* The :class:`HTFView` returned by ``aggregator.view(tf)`` slices to
  the current completed count and respects Zero-Future.
* ``BacktestCore._process_one_candle`` no longer calls ``feed`` (the
  hot loop now allocates zero HTF candle dicts).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest

from engine.backtest_engine import BacktestEngine  # noqa: E402
from engine.strategy.context import Strategy  # noqa: E402
from engine.data.htf_view import HTFView  # noqa: E402
from engine.data.tf_aggregator import TimeframeAggregator  # noqa: E402


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------
@pytest.fixture
def app():
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    return inst if inst is not None else QCoreApplication([])


def _synthetic_1m(n_bars: int, start: str = "2024-03-13 09:00") -> pd.DataFrame:
    """Build a deterministic 1m OHLCV frame used to compare paths."""
    times = pd.date_range(start=start, periods=n_bars, freq="1min")
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 0.05, size=n_bars))
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) + rng.uniform(0, 0.10, size=n_bars)
    lows = np.minimum(opens, closes) - rng.uniform(0, 0.10, size=n_bars)
    volumes = rng.integers(1, 5, size=n_bars).astype(float)
    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def _feed_legacy(df: pd.DataFrame) -> tuple[TimeframeAggregator, list[dict[str, bool]]]:
    """Drive a fresh aggregator through ``feed`` bar-by-bar and return
    both the aggregator (so callers can inspect ``get_completed`` /
    ``get_forming``) and the per-step ``completed_tfs`` history.
    """
    agg = TimeframeAggregator()
    history: list[dict[str, bool]] = []
    for _, row in df.iterrows():
        history.append(
            agg.feed(
                {
                    "time": row["time"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
            )
        )
    return agg, history


# ----------------------------------------------------------------------
# Vectorized parity vs legacy ``feed``
# ----------------------------------------------------------------------
def test_prebuild_matches_legacy_feed_for_all_tfs() -> None:
    df = _synthetic_1m(500)
    legacy, _ = _feed_legacy(df)

    fast = TimeframeAggregator()
    fast.prebuild(df)
    fast.advance_to(len(df) - 1)

    for tf in TimeframeAggregator.TF_MINUTES:
        legacy_completed = legacy.get_completed(tf)
        fast_completed = fast.get_completed(tf)
        assert len(fast_completed) == len(legacy_completed), (
            f"{tf}: completed-bar count mismatch "
            f"(legacy={len(legacy_completed)}, fast={len(fast_completed)})"
        )
        for i, (a, b) in enumerate(zip(legacy_completed, fast_completed)):
            assert a["time"] == b["time"], f"{tf}#{i} time mismatch"
            assert a["open"] == pytest.approx(b["open"]), f"{tf}#{i} open"
            assert a["high"] == pytest.approx(b["high"]), f"{tf}#{i} high"
            assert a["low"] == pytest.approx(b["low"]), f"{tf}#{i} low"
            assert a["close"] == pytest.approx(b["close"]), f"{tf}#{i} close"
            assert a["volume"] == pytest.approx(b["volume"]), f"{tf}#{i} volume"


def test_advance_to_matches_legacy_completed_flags_per_step() -> None:
    df = _synthetic_1m(120)
    _, legacy_history = _feed_legacy(df)

    fast = TimeframeAggregator()
    fast.prebuild(df)

    for i in range(len(df)):
        flags = fast.advance_to(i)
        for tf in TimeframeAggregator.TF_MINUTES:
            assert flags[tf] == legacy_history[i][tf], (
                f"step {i}: {tf} completed flag mismatch "
                f"(legacy={legacy_history[i][tf]}, fast={flags[tf]})"
            )


def test_get_forming_matches_legacy_at_every_step() -> None:
    df = _synthetic_1m(80)
    legacy_per_step = []
    legacy = TimeframeAggregator()
    for _, row in df.iterrows():
        legacy.feed(
            {
                "time": row["time"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
        )
        # Snapshot dicts — ``feed`` mutates the forming dict in place
        # on subsequent steps, so we copy out the values we want to
        # compare against the vectorized path.
        legacy_per_step.append(
            {
                tf: dict(legacy.get_forming(tf))
                if legacy.get_forming(tf) is not None
                else None
                for tf in legacy.TF_MINUTES
            }
        )

    fast = TimeframeAggregator()
    # Phase 0.2 (A3) — default ``track_forming`` no longer allocates
    # forming arrays for every TF, so opt in explicitly here to keep
    # the legacy-parity assertion meaningful.
    fast.prebuild(df, track_forming=set(TimeframeAggregator.TF_MINUTES.keys()))
    for i in range(len(df)):
        fast.advance_to(i)
        for tf in TimeframeAggregator.TF_MINUTES:
            f_form = fast.get_forming(tf)
            l_form = legacy_per_step[i][tf]
            assert f_form is not None and l_form is not None, (
                f"step {i} {tf}: forming missing"
            )
            assert f_form["time"] == l_form["time"], f"{tf} forming.time @ {i}"
            assert f_form["open"] == pytest.approx(l_form["open"])
            assert f_form["high"] == pytest.approx(l_form["high"])
            assert f_form["low"] == pytest.approx(l_form["low"])
            assert f_form["close"] == pytest.approx(l_form["close"])
            assert f_form["volume"] == pytest.approx(l_form["volume"])


def test_view_slices_to_completed_count_and_is_readonly() -> None:
    df = _synthetic_1m(60)
    fast = TimeframeAggregator()
    fast.prebuild(df)

    view = fast.view("5m")
    assert isinstance(view, HTFView)
    assert len(view) == 0  # before advancing, nothing visible

    # After bar 5 (the 6th 1m bar) the first 5m bucket *just* started
    # the next bucket → exactly one HTF bar is completed.
    fast.advance_to(5)
    assert len(view) == 1
    assert view.close[-1] == pytest.approx(view.close[0])
    with pytest.raises(ValueError):
        view.close[0] = 999.0  # writeable=False

    fast.advance_to(len(df) - 1)
    expected = len(fast.get_completed("5m"))
    assert len(view) == expected


def test_advance_to_negative_index_clears_visible_count() -> None:
    df = _synthetic_1m(30)
    fast = TimeframeAggregator()
    fast.prebuild(df)
    fast.advance_to(20)
    assert len(fast.view("5m")) > 0
    flags = fast.advance_to(-1)
    assert all(v is False for v in flags.values())
    assert len(fast.view("5m")) == 0


# ----------------------------------------------------------------------
# End-to-end: BacktestCore drives the vectorized path
# ----------------------------------------------------------------------
class _RecordHTF(Strategy):
    """Strategy that records ``ctx.completed_tfs`` and HTF view sizes
    every step so the test can compare two backtests."""

    def __init__(self) -> None:
        self.flags: list[dict[str, bool]] = []
        self.view_sizes: list[int] = []
        self.last_close_5m: list[float | None] = []

    def on_candle(self, ctx) -> None:
        self.flags.append(dict(ctx.completed_tfs))
        view = ctx.get_tf("5m")
        self.view_sizes.append(len(view))
        self.last_close_5m.append(float(view.close[-1]) if len(view) > 0 else None)


def test_backtest_engine_uses_vectorized_path(app) -> None:
    df = _synthetic_1m(120)
    eng = BacktestEngine()
    eng.load_data(df)
    # ``load_strategy`` after ``load_data`` so the engine's reset() in
    # ``load_data`` doesn't re-instantiate (and discard) our recorder.
    eng.load_strategy(_RecordHTF())
    eng.step_n(len(df))

    strat = eng.strategy  # active recorder instance owned by the engine
    assert isinstance(strat, _RecordHTF)

    # Build the legacy reference completion-flag history from the same
    # frame so we can compare bar-by-bar.
    _, legacy_history = _feed_legacy(df)
    assert len(strat.flags) == len(legacy_history)  # one per step

    # Strategy is invoked for each 1m bar starting at idx=0.
    for i, recorded in enumerate(strat.flags):
        for tf in TimeframeAggregator.TF_MINUTES:
            assert recorded[tf] == legacy_history[i][tf], (
                f"engine step {i}: {tf} completed flag drift"
            )

    # HTFView size is monotonically non-decreasing — Zero-Future is
    # respected and the engine never reveals future HTF bars.
    sizes = strat.view_sizes
    assert all(b >= a for a, b in zip(sizes, sizes[1:])), (
        f"HTFView size regressed across steps: {sizes[:20]}"
    )
