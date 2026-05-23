"""
Backtest Core — pure-Python iterative engine, no PySide6 / Qt dependencies.

This is the headless heart of AGREGAT. It can be driven by the GUI (via
:class:`engine.adapters.qt_adapter.QtBacktestAdapter`, which forwards each
callback to a Qt signal), by a CLI walk-forward harness, or by a cloud
optimisation worker — none of which require an X server or
``QCoreApplication``.

Design notes:
    • A single internal :meth:`_process_one_candle` is shared by :meth:`step`
      and :meth:`step_n`, so EOD/SL/TP/strategy logic cannot drift between
      them.
    • Same-bar SL/TP collisions resolve worst-case (assume SL hit before TP).
    • Recovery factor uses absolute drawdown ($), not %-of-initial-equity.
    • PnL/equity are quantised to the asset's ``tick_size`` (in dollars per
      point).
    • All UI hooks are Python callable lists exposed as ``on_*`` attributes;
      adapters subscribe by appending callbacks. Failures inside a callback
      are logged but do not abort the simulation.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

import numpy as np
import pandas as pd

from engine.strategy.context import Context, Strategy
from engine.core.data_view import DataView
from engine.execution.execution import ExecutionModel
from engine.models.models import (
    AssetConfig,
    PendingOrder,
    Position,
    Trade,
    get_session,
    is_eod,
    is_trading_hours,
    is_weekend,
)
from engine.analytics.stats_accumulator import DrawdownTracker, StatsAccumulator
from engine.data.tf_aggregator import TimeframeAggregator
from engine.execution.vectorized_engine import VectorizedResult

logger = logging.getLogger("StrategyTester.engine.backtest")


def _round_to_step(value: float, step: float) -> float:
    """Round `value` to the nearest multiple of `step`. Defaults to 0.01."""
    if step is None or step <= 0:
        return round(value, 2)
    return round(round(value / step) * step, 10)


class BacktestCore:
    """Pure-Python backtest core.

    Notification hooks:
        on_data_loaded(total_candles: int)
        on_step_completed(current_idx: int, current_candle: dict)
        on_trade_completed(trade: Trade)
        on_stats_updated(stats: dict)
        on_simulation_finished()
        on_error(message: str)
        on_position_changed()
        on_layer_registered(tag: str, label: str, default_on: bool)
        on_drawing_changed(drawing_id: int)

    Adapters subscribe by appending callbacks to the relevant list, e.g.::

        core.on_trade_completed.append(self.handle_trade)
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _numpy_columns(self) -> Dict[str, np.ndarray]:
        """Return the live NumPy columns by name.

        :class:`DataView` keeps a reference to this dict and walks it on
        every property access, so the same ``DataView`` object stays
        valid across :py:meth:`load_data` / :py:meth:`set_date_range`
        calls (the underlying arrays change, the dict identity does not).
        """
        return {
            "open": self._open,
            "high": self._high,
            "low": self._low,
            "close": self._close,
            "volume": self._volume,
            "time": self._time,
        }

    def _refresh_numpy_columns(self) -> None:
        """Re-bind the NumPy storage from ``self.df`` and refresh the
        DataView's column dict.
        """
        df = self.df
        if df is None or len(df) == 0:
            self._open = np.empty(0, dtype=np.float64)
            self._high = np.empty(0, dtype=np.float64)
            self._low = np.empty(0, dtype=np.float64)
            self._close = np.empty(0, dtype=np.float64)
            self._volume = np.empty(0, dtype=np.float64)
            self._time = np.empty(0, dtype="datetime64[ns]")
            # Phase 1.4 — per-bar spread overrides (points). Empty when
            # the data source has no ``spread`` column.
            self._spread_per_bar = np.empty(0, dtype=np.float64)
        else:
            self._open = df["open"].to_numpy(dtype=np.float64, copy=False)
            self._high = df["high"].to_numpy(dtype=np.float64, copy=False)
            self._low = df["low"].to_numpy(dtype=np.float64, copy=False)
            self._close = df["close"].to_numpy(dtype=np.float64, copy=False)
            volume = (
                df["volume"].to_numpy(dtype=np.float64, copy=False)
                if "volume" in df.columns
                else np.zeros(len(df), dtype=np.float64)
            )
            self._volume = volume
            self._time = pd.to_datetime(df["time"]).to_numpy()
            # Phase 1.4 — per-bar spread (points). When the column is
            # missing we keep an empty array so ``has_per_bar_spread``
            # stays False and the legacy fixed spread is used.
            if "spread" in df.columns:
                self._spread_per_bar = df["spread"].to_numpy(
                    dtype=np.float64, copy=False
                )
            else:
                self._spread_per_bar = np.empty(0, dtype=np.float64)
        # Re-bind the DataView's dict in place so existing references
        # (e.g. ``ctx.data``) automatically pick up the new arrays.
        cols = self.data._arrays  # noqa: SLF001 — intentional internal handshake
        cols["open"] = self._open
        cols["high"] = self._high
        cols["low"] = self._low
        cols["close"] = self._close
        cols["volume"] = self._volume
        cols["time"] = self._time

    @property
    def total_bars(self) -> int:
        """Number of 1-minute candles currently loaded."""
        return self._close.shape[0]

    def candle_at(self, idx: int) -> Dict[str, Any]:
        """Return candle ``idx`` as a dict (built on demand from NumPy).

        Replaces the old ``self._candle_list[idx]`` access pattern
        without materialising the whole list up front. Keys mirror the
        ``DataFrame`` column layout (``open``, ``high``, ``low``,
        ``close``, ``volume``, ``time``).
        """
        if idx < 0 or idx >= self.total_bars:
            raise IndexError(
                f"candle_at: idx {idx} out of bounds for size {self.total_bars}"
            )
        return {
            "open": float(self._open[idx]),
            "high": float(self._high[idx]),
            "low": float(self._low[idx]),
            "close": float(self._close[idx]),
            "volume": float(self._volume[idx]),
            "time": self._time[idx],
        }

    @property
    def _candle_list(self) -> "_LazyCandleList":
        """Backwards-compatible candle accessor.

        Returns a lazy view that supports ``len()``, ``__getitem__``,
        ``__bool__``, and iteration without materialising the entire
        list. Existing GUI/test code that does
        ``engine._candle_list[idx]`` or ``len(engine._candle_list)``
        keeps working with O(1) cost per access. New code should call
        :py:meth:`candle_at` or use :pyattr:`data` directly.
        """
        return _LazyCandleList(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def __init__(self) -> None:
        self.df: Optional[pd.DataFrame] = None
        self.df_full: Optional[pd.DataFrame] = (
            None  # untouched copy for date-range filtering
        )
        self._date_range: tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]] = (
            None,
            None,
        )
        self.strategy: Optional[Strategy] = None
        self._strategy_class: Optional[type] = None

        # NumPy column storage — populated by ``load_data`` from the
        # incoming DataFrame and consumed by both the iterative engine
        # (per-bar reads) and strategies via :class:`DataView`. Empty
        # placeholders are installed up front so ``ctx.data`` has a
        # usable shape even before any candle is fed.
        self._open = np.empty(0, dtype=np.float64)
        self._high = np.empty(0, dtype=np.float64)
        self._low = np.empty(0, dtype=np.float64)
        self._close = np.empty(0, dtype=np.float64)
        self._volume = np.empty(0, dtype=np.float64)
        self._time = np.empty(0, dtype="datetime64[ns]")

        self.current_idx = -1
        # Strategy-facing zero-future data window. Re-bound in
        # ``load_data`` once the columns are known so ``ctx.data``
        # always has a usable handle even before any candle is fed.
        self.data: DataView = DataView(self._numpy_columns(), lambda: self.current_idx)

        # DataView is the only strategy-facing API for historical data.
        # Legacy history (list of dicts) was removed.

        self.initial_equity = 100_000.0
        self.equity = self.initial_equity
        # Running snapshot of equity after each closed trade (for sparkline / report).
        self.equity_track: List[float] = [self.initial_equity]
        self.max_equity = self.initial_equity
        self.max_drawdown_pct = 0.0
        self.max_drawdown_abs = 0.0  # peak-to-trough in dollars

        # Risk model — wired to Settings UI
        self.risk_pct: float = 1.0  # 1% of equity per trade by default

        self.position: Optional[Position] = None
        self.pending_order: Optional[PendingOrder] = None
        self.trades: List[Trade] = []
        self.drawings: List[Dict[str, Any]] = []
        self._next_drawing_id = 0
        self._registered_layers: set[str] = set()
        self._next_trade_id = 0
        self._suppress_signals = False

        # Incremental stats accumulators
        self._stats_wins = 0
        self._stats_losses = 0
        self._stats_gross_profit = 0.0
        self._stats_gross_loss = 0.0
        self._stats_longs = 0
        self._stats_shorts = 0
        self._stats_dirty = True

        self.skipped_trades: List[Dict[str, Any]] = []

        # Phase 1.1 — Bar-level equity tracking. When ``track_bar_equity``
        # is enabled, a dollar-equity value is recorded at every bar
        # (realised + unrealised PnL), giving Sharpe / Sortino / Calmar
        # access to true intra-trade volatility instead of the
        # piecewise-constant trade-level curve. Disabled by default —
        # ~80 MB on a 10M bar series. Allocated lazily in ``load_data``.
        self.track_bar_equity: bool = False
        self.bar_equity: Optional[np.ndarray] = None

        # Sprint 2.3 / 2.4 — Provenance fields persisted in the
        # ``.agbk`` archive's ``meta.json`` so a saved session can be
        # replayed bit-for-bit. ``set_input_hash`` / ``set_seeds`` are
        # the public setters; both are optional and default to ``None``.
        self.input_csv_sha256: Optional[str] = None
        self.input_parquet_sha256: Optional[str] = None
        self.seeds: Dict[str, int] = {}

        # Subsystems
        self.tf_aggregator = TimeframeAggregator()
        self.execution_model = ExecutionModel(profile="Optimistic")
        self.stats = StatsAccumulator()
        # Centralised on-line DD math (TZ Phase 4 / Etap 4.1). Both engines
        # feed it the post-trade equity; the legacy ``max_drawdown_*`` /
        # ``max_equity`` mirror fields are kept in lock-step below.
        self.drawdown = DrawdownTracker()
        self.drawdown.reset(self.initial_equity)
        self.asset_config = AssetConfig(
            name="DE40",
            point_value=1.0,
            tick_size=0.1,
            buffer=1.0,
            max_lots_per_100k=12,
            currency="EUR",
        )

        self.ctx = Context(self)

        # ------------------------------------------------------------------
        # Notification hooks — adapters append callbacks here.
        # ------------------------------------------------------------------
        self.on_data_loaded: List[Callable[[int], None]] = []
        self.on_step_completed: List[Callable[[int, Dict[str, Any]], None]] = []
        self.on_trade_completed: List[Callable[[Trade], None]] = []
        self.on_stats_updated: List[Callable[[Dict[str, Any]], None]] = []
        self.on_simulation_finished: List[Callable[[], None]] = []
        self.on_error: List[Callable[[str], None]] = []
        self.on_position_changed: List[Callable[[], None]] = []
        self.on_layer_registered: List[Callable[[str, str, bool], None]] = []
        self.on_drawing_changed: List[Callable[[int], None]] = []

    # ------------------------------------------------------------------
    # Internal: callback dispatch
    # ------------------------------------------------------------------
    @staticmethod
    def _fire(callbacks: List[Callable[..., None]], *args: Any) -> None:
        """Invoke every callback; isolate failures so the sim never aborts."""
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:  # pragma: no cover — defensive
                logger.exception("BacktestCore callback raised; continuing")

    # ------------------------------------------------------------------
    # Asset Configuration
    # ------------------------------------------------------------------
    def load_asset_config(self, name: str) -> None:
        from engine.asset_registry import get_asset, list_asset_names

        c = get_asset(name)
        if c is None:
            logger.warning(
                f"Asset '{name}' not found in config. Available: {list_asset_names()}"
            )
            return

        # Optional trading-hour windows — accept "HH:MM" string format
        from datetime import time as _time

        def _parse_time(s: Any, default: _time) -> _time:
            if s is None:
                return default
            if isinstance(s, _time):
                return s
            if isinstance(s, str) and ":" in s:
                try:
                    h, m = s.split(":", 1)
                    return _time(int(h), int(m))
                except (ValueError, TypeError):
                    return default
            return default

        kwargs: Dict[str, Any] = dict(
            name=name,
            point_value=c["point_value"],
            tick_size=c["tick_size"],
            buffer=c["buffer"],
            max_lots_per_100k=c["max_lots_per_100k"],
            currency=c["currency"],
            min_lot=c.get("min_lot", 0.01),
            lot_step=c.get("lot_step", 0.01),
            spread_points=c.get("spread_points", 0.0),
            commission_per_lot=c.get("commission_per_lot", 0.0),
            slippage_points=c.get("slippage_points", 0.0),
            enforce_trading_hours=c.get("enforce_trading_hours", False),
            enforce_eod_close=c.get("enforce_eod_close", True),
            close_on_weekend=c.get("close_on_weekend", True),
        )
        if "trading_hours_start" in c:
            kwargs["trading_hours_start"] = _parse_time(
                c["trading_hours_start"], _time(9, 0)
            )
        if "trading_hours_end" in c:
            kwargs["trading_hours_end"] = _parse_time(
                c["trading_hours_end"], _time(18, 0)
            )
        if "eod_close" in c:
            kwargs["eod_close"] = _parse_time(c["eod_close"], _time(22, 0))
        self.asset_config = AssetConfig(**kwargs)
        logger.info(
            f"Asset config loaded: {name} "
            f"(PV={c['point_value']}, Buffer={c['buffer']}, Currency={c['currency']})"
        )

    # ------------------------------------------------------------------
    # Data & Strategy Loading
    # ------------------------------------------------------------------
    def set_input_hash(
        self,
        *,
        csv_path: Optional[str] = None,
        parquet_path: Optional[str] = None,
    ) -> None:
        """Sprint 2.3 — Record the SHA-256 of the input data file.

        Called by the GUI's CSV / Parquet load flow so the digest can
        be persisted to ``meta.json`` (``input_csv_sha256`` /
        ``input_parquet_sha256``). The hash is over the *source* file's
        bytes — not over the parsed DataFrame — because the goal is to
        let users identify which file produced a saved session even if
        the parsed frame would compare equal.
        """
        from engine.persistence.integrity import hash_file

        if csv_path:
            self.input_csv_sha256 = hash_file(csv_path)
        if parquet_path:
            self.input_parquet_sha256 = hash_file(parquet_path)

    def set_seeds(self, **seeds: Optional[int]) -> None:
        """Sprint 2.4 — Record RNG seeds used during this session.

        Pass any ``key=int`` pair (e.g. ``set_seeds(monte_carlo=42,
        optimizer=123)``); ``None`` values clear the slot. The dict is
        copied into ``meta.json`` on save so a later replay can read
        the same seeds back and reproduce results bit-for-bit.
        """
        for key, value in seeds.items():
            if value is None:
                self.seeds.pop(key, None)
            else:
                self.seeds[str(key)] = int(value)

    @property
    def has_per_bar_spread(self) -> bool:
        """True when the loaded data carries a ``spread`` column."""
        return self._spread_per_bar.size > 0

    def spread_at(self, idx: int) -> Optional[float]:
        """Per-bar spread in *points* at ``idx`` if available, else ``None``.

        Phase 1.4 — used by ``_fill_pending`` / ``_check_sl_tp`` /
        ``execute_close`` to widen the bid-ask gap on bars where
        liquidity is thin (typical asia/holiday windows).
        """
        if not self.has_per_bar_spread:
            return None
        if idx < 0 or idx >= self._spread_per_bar.size:
            return None
        return float(self._spread_per_bar[idx])

    def enable_bar_equity_tracking(self, enabled: bool = True) -> None:
        """Toggle the per-bar equity buffer (Phase 1.1).

        Allocates ``self.bar_equity`` lazily — sized in
        :meth:`_allocate_bar_equity` once data has been loaded. When
        disabled the buffer is dropped so the GC can reclaim ~80 MB on
        a 10M-bar series.
        """
        self.track_bar_equity = bool(enabled)
        if not self.track_bar_equity:
            self.bar_equity = None
            return
        self._allocate_bar_equity()

    def _allocate_bar_equity(self) -> None:
        """Allocate / re-shape the bar-equity buffer to match the loaded data."""
        if not self.track_bar_equity:
            self.bar_equity = None
            return
        n = self.total_bars
        if n == 0:
            self.bar_equity = np.empty(0, dtype=np.float64)
            return
        if self.bar_equity is None or self.bar_equity.shape[0] != n:
            self.bar_equity = np.full(n, self.initial_equity, dtype=np.float64)
        else:
            self.bar_equity[:] = self.initial_equity

    def load_data(self, df: pd.DataFrame) -> None:
        logger.info(f"📊 Loading {len(df):,} candles into engine...")
        self.df_full = df
        self._date_range = (None, None)
        self.df = df
        # Pull NumPy columns into the engine — the per-step dicts are
        # built lazily by ``candle_at`` so we no longer pay the
        # ``df.to_dict('records')`` cost up front.
        self._refresh_numpy_columns()
        # Phase 1.1 — bar-equity buffer follows the loaded data shape.
        self._allocate_bar_equity()
        # Vectorized HTF resample once per dataset. After this the
        # hot loop in ``_process_one_candle`` can update the
        # aggregator with a single ``advance_to(idx)`` call instead of
        # per-bar dict feeding. (TZ Phase 3.)
        self.tf_aggregator.prebuild(self.df)
        # Quality gate: warn if any OHLC value is NaN. We do not drop
        # rows automatically (that would silently shift indices) but the
        # warning makes the user aware that downstream indicators will
        # propagate NaNs (cumsum-based MAs are particularly poison-prone).
        self._warn_on_invalid_ohlc()
        self.reset()
        self._fire(self.on_data_loaded, len(self.df))
        logger.info(f"✅ Data loaded: {len(self.df):,} candles ready for backtesting")

    def _warn_on_invalid_ohlc(self) -> None:
        """Emit a single warning if the loaded data contains NaN OHLC
        values or rows where ``high < low`` (corrupt feed).

        We do not raise — partially valid data should still backtest, but
        the user needs to know their indicators may produce NaN.
        """
        if self.total_bars == 0:
            return
        nan_open = int(np.isnan(self._open).sum())
        nan_high = int(np.isnan(self._high).sum())
        nan_low = int(np.isnan(self._low).sum())
        nan_close = int(np.isnan(self._close).sum())
        nan_total = nan_open + nan_high + nan_low + nan_close
        if nan_total > 0:
            logger.warning(
                "⚠️  OHLC data contains NaN values "
                "(open=%d high=%d low=%d close=%d). Indicators may be "
                "poisoned — consider df.dropna() before load_data.",
                nan_open,
                nan_high,
                nan_low,
                nan_close,
            )
        # Detect OHLC inconsistency only on the rows where every value
        # is finite — NaN comparisons would always be false.
        finite = (
            np.isfinite(self._open)
            & np.isfinite(self._high)
            & np.isfinite(self._low)
            & np.isfinite(self._close)
        )
        if finite.any():
            bad_hl = int(np.sum(self._high[finite] < self._low[finite]))
            if bad_hl > 0:
                logger.warning(
                    "⚠️  %d rows have high < low — possible corrupt feed.",
                    bad_hl,
                )

    def set_date_range(
        self,
        start: Optional[pd.Timestamp],
        end: Optional[pd.Timestamp],
    ) -> None:
        """Restrict the working dataset to ``[start, end]`` (inclusive).

        Pass ``None`` for both bounds to reset to the full dataset. Idempotent;
        signals and stats are left for the GUI to refresh via ``reset()``.
        """
        if self.df_full is None:
            return
        self._date_range = (start, end)
        if start is None and end is None:
            self.df = self.df_full
        else:
            mask = pd.Series([True] * len(self.df_full), index=self.df_full.index)
            times = pd.to_datetime(self.df_full["time"], errors="coerce")
            if start is not None:
                mask &= times >= start
            if end is not None:
                mask &= times <= end
            self.df = self.df_full.loc[mask].reset_index(drop=True)
        self._refresh_numpy_columns()
        # Re-resample HTF arrays for the new (possibly narrower) view.
        self.tf_aggregator.prebuild(self.df)
        # Notify listeners so progress bar / chart redraw with new totals.
        self._fire(self.on_data_loaded, len(self.df))

    def load_strategy(self, strategy: Strategy) -> None:
        self.strategy = strategy
        self._strategy_class = strategy.__class__
        logger.info(f"Loaded strategy: {strategy.__class__.__name__}")

    # ------------------------------------------------------------------
    # Phase 0.2 (A3) — runtime forming-TF management
    # ------------------------------------------------------------------
    def set_visible_tf(self, tf: Optional[str]) -> None:
        """Tell the aggregator which HTF (if any) needs forming arrays.

        Called by the chart toolbar when the user changes the visual
        timeframe; passing ``None`` (or an unsupported TF) drops every
        forming allocation. The default :py:meth:`load_data` no longer
        materialises forming arrays for every TF — the GUI opts in to a
        single TF here, keeping the cold-load memory budget low.
        """
        if tf is None:
            tfs: set[str] = set()
        else:
            tfs = {tf}
        self.tf_aggregator.set_forming_tfs(tfs)

    def reset(self) -> None:
        logger.info("🔄 Resetting simulation...")
        self.current_idx = -1
        self.equity = self.initial_equity
        self.equity_track = [self.initial_equity]
        self.max_equity = self.initial_equity
        self.max_drawdown_pct = 0.0
        self.max_drawdown_abs = 0.0
        self.drawdown.reset(self.initial_equity)
        self.position = None
        self.pending_order = None
        trades_count = len(self.trades)
        self.trades.clear()
        self.drawings.clear()
        self._next_drawing_id = 0
        self._registered_layers.clear()
        self._next_trade_id = 0
        self.stats.reset()
        self._stats_wins = 0
        self._stats_losses = 0
        self._stats_gross_profit = 0.0
        self._stats_gross_loss = 0.0
        self._stats_longs = 0
        self._stats_shorts = 0
        self._stats_dirty = True
        self.tf_aggregator.reset()
        skipped_count = len(self.skipped_trades)
        self.skipped_trades.clear()
        # Phase 1.1 — re-prime bar-equity buffer to ``initial_equity``.
        if self.track_bar_equity:
            self._allocate_bar_equity()

        # Re-instantiate strategy to clear its internal state
        if self._strategy_class is not None:
            try:
                self.strategy = self._strategy_class()
                logger.info(
                    f"✅ Strategy re-instantiated: {self._strategy_class.__name__}"
                )
            except TypeError:
                logger.warning(
                    f"⚠️ Strategy {self._strategy_class.__name__} cannot be re-instantiated "
                    f"(requires arguments). Keeping existing instance."
                )
            except Exception as e:
                logger.error(
                    f"❌ Could not re-instantiate strategy: {e!r}. Disabling strategy.",
                    exc_info=True,
                )
                self.strategy = None
        self.update_stats()
        if self.df is not None:
            self._fire(self.on_step_completed, -1, {})
        logger.info(
            f"✅ Reset complete | Cleared {trades_count} trades, {skipped_count} skipped "
            f"signals | Equity: ${self.initial_equity:,.2f}"
        )

    # ------------------------------------------------------------------
    # Step logic — single inner function shared by step() and step_n()
    # ------------------------------------------------------------------
    def _process_one_candle(self, next_idx: int) -> bool:
        """Process candle at `next_idx`. Returns False on strategy error.

        ``self.current_idx`` is advanced to ``next_idx`` at the top so that
        ``Trade.entry_index`` / ``Trade.exit_index`` always equal the index
        of the bar where the fill / exit actually happened — regardless of
        whether the close is triggered by SL/TP/EOD/WEEKEND (pre-strategy)
        or by the strategy calling ``ctx.close()`` (post-strategy). This
        also keeps ``ctx.data.close[-1]`` pointing at the bar the strategy
        is being asked to react to.
        """
        # Advance the bar pointer first — see method docstring.
        self.current_idx = next_idx

        # 1. Advance TF aggregator. Vectorized fast path: a single
        # NumPy lookup per TF (set in ``load_data`` via ``prebuild``).
        # No HTF candle dicts are built here — strategies that need
        # them go through ``ctx.get_tf(tf)`` which returns an
        # ``HTFView`` over the cached arrays. (TZ Phase 3.)
        completed_tfs = self.tf_aggregator.advance_to(next_idx)

        # 2. Fill pending order at this candle's open
        # (needs dict because _fill_pending accesses time for logging)
        if self.pending_order is not None:
            self._fill_pending(self.candle_at(next_idx))

        # 3. Position housekeeping (MAE/MFE then SL/TP collision)
        # Use zero-alloc fast path — dict only built on actual close.
        if self.position is not None:
            self._update_mae_mfe_fast(next_idx)
            self._check_sl_tp_fast(next_idx)

        # 4. EOD close — gated by ``AssetConfig.enforce_eod_close``.
        if self.position is not None and self.asset_config.enforce_eod_close:
            ct = self._time[next_idx]
            asset = self.asset_config
            try:
                if ct is not None and hasattr(ct, "hour") and hasattr(ct, "minute"):
                    eod = asset.eod_close
                    if (ct.hour, ct.minute) >= (eod.hour, eod.minute):
                        self.execute_close(
                            reason="EOD", exit_candle=self.candle_at(next_idx)
                        )
                elif is_eod(ct, asset):
                    self.execute_close(
                        reason="EOD", exit_candle=self.candle_at(next_idx)
                    )
            except Exception as e:  # pragma: no cover — defensive
                logger.error(f"EOD check failed at idx={next_idx}: {e!r}")

        # 5. Weekend close
        if self.position is not None and self.asset_config.close_on_weekend:
            if is_weekend(self._time[next_idx]):
                self.execute_close(
                    reason="WEEKEND", exit_candle=self.candle_at(next_idx)
                )

        # 6. Strategy hook — build candle dict once for ctx.candle
        candle_dict = self.candle_at(next_idx)
        self.ctx.candle = candle_dict
        self.ctx._completed_tfs = completed_tfs
        if self.strategy is not None:
            try:
                for tf, completed in completed_tfs.items():
                    if not completed:
                        continue
                    hook = getattr(self.strategy, f"on_{tf}_close", None)
                    if hook is not None:
                        hook(self.ctx)
            except Exception as e:
                logger.error(
                    f"Strategy HTF hook error at step {next_idx}: {e!r}", exc_info=True
                )
                return False
            try:
                self.strategy.on_candle(self.ctx)
            except Exception as e:
                logger.error(f"Strategy Error at step {next_idx}: {e!r}", exc_info=True)
                self._fire(self.on_error, f"Strategy error at candle #{next_idx}: {e}")
                return False

        # 7. Phase 1.1 — record realised + unrealised equity at bar close.
        # Cheap (a few ops per bar) and only allocated when explicitly
        # enabled via ``enable_bar_equity_tracking(True)``.
        if self.track_bar_equity and self.bar_equity is not None:
            self.bar_equity[next_idx] = self._compute_bar_equity(next_idx)

        return True

    def _compute_bar_equity(self, idx: int) -> float:
        """Realised equity + open-position mark-to-market at bar ``idx``.

        Mark-to-market is computed at the bar's close on the *Bid* side
        (the chart price) — the same convention used everywhere else in
        the engine. Spread-side adjustment is intentionally omitted from
        the running mark to keep the curve continuous; the realised cost
        will be deducted on the actual close exit.
        """
        if self.position is None:
            return float(self.equity)
        pos = self.position
        close = float(self._close[idx])
        if pos.direction == "long":
            unrealised_points = close - pos.entry_price
        else:
            unrealised_points = pos.entry_price - close
        unrealised = unrealised_points * pos.volume * self.asset_config.point_value
        return float(self.equity + unrealised)

    def step(self) -> None:
        """Process the next 1m candle (single-step UI mode)."""
        if self.df is None or self.strategy is None:
            return
        next_idx = self.current_idx + 1
        if next_idx >= self.total_bars:
            self._emit_finish_log()
            self._fire(self.on_simulation_finished)
            return

        ok = self._process_one_candle(next_idx)
        if not ok:
            return

        if self._stats_dirty:
            self.update_stats()
        if not self._suppress_signals:
            self._fire(
                self.on_step_completed,
                self.current_idx,
                self.candle_at(self.current_idx),
            )

    def step_n(self, n: int) -> None:
        """Process `n` candles in bulk — signals suppressed, final state emitted once."""
        if self.df is None or self.strategy is None or n <= 0:
            return

        target = min(self.current_idx + n, self.total_bars - 1)
        initial_idx = self.current_idx
        if n > 100:
            logger.info(
                f"⚡ Processing {n} candles in bulk (from #{initial_idx + 1} to #{target + 1})..."
            )

        self._suppress_signals = True
        try:
            while self.current_idx < target:
                next_idx = self.current_idx + 1
                if next_idx >= self.total_bars:
                    break
                ok = self._process_one_candle(next_idx)
                if not ok:
                    break
        except Exception as e:  # pragma: no cover — defensive
            logger.error(f"step_n critical error: {e!r}", exc_info=True)
        finally:
            self._suppress_signals = False

        processed = self.current_idx - initial_idx
        if processed > 100:
            logger.info(
                f"✅ Bulk done: {processed} candles | Trades: {len(self.trades)} | "
                f"Equity: ${self.equity:,.2f}"
            )

        self._stats_dirty = True
        self.update_stats()
        if self.current_idx >= 0:
            self._fire(
                self.on_step_completed,
                self.current_idx,
                self.candle_at(self.current_idx),
            )

        if self.current_idx >= self.total_bars - 1:
            self._emit_finish_log()
            self._fire(self.on_simulation_finished)

    def _emit_finish_log(self) -> None:
        if self.trades:
            ret_pct = (self.equity - self.initial_equity) / self.initial_equity * 100.0
            logger.info("🏁 Simulation complete!")
            logger.info(
                f"📈 Final: Trades: {len(self.trades)} | Equity: ${self.equity:,.2f} | "
                f"Return: {ret_pct:+.2f}% | Max DD: {self.max_drawdown_pct:.2f}%"
            )

    # ------------------------------------------------------------------
    # Pending Order System
    # ------------------------------------------------------------------
    def _fill_pending(self, candle: Dict[str, Any]) -> None:
        """Fill a pending order at this candle's open price."""
        if self.pending_order is None:
            return
        if self.position is not None:
            self.pending_order = None
            return

        po = self.pending_order
        self.pending_order = None

        # Phase 1.7 — unified pending-order rejection. The visual and
        # vectorised engines must drop the same pending orders for the
        # same reasons; otherwise winrate / fill-count diverge between
        # Stats / Quant / Compare-Engines tabs. The order of checks
        # matches the vectorised path:
        #   1) trading-hours violation (most specific)
        #   2) weekend block
        #   3) EOD block
        # First match wins, with its reason recorded on
        # :pyattr:`skipped_trades`.
        candle_time = candle.get("time")
        asset = self.asset_config
        skip_reason: Optional[str] = None
        if asset.enforce_trading_hours and not is_trading_hours(candle_time, asset):
            skip_reason = "TRADING_HOURS"
        elif asset.close_on_weekend and is_weekend(candle_time, asset):
            skip_reason = "WEEKEND"
        elif asset.enforce_eod_close and is_eod(candle_time, asset):
            skip_reason = "EOD"

        if skip_reason is not None:
            self.skipped_trades.append(
                {
                    "time": str(candle_time or ""),
                    "reason": skip_reason,
                    "direction": po.direction,
                }
            )
            logger.info(
                f"[{skip_reason}] Skipped {po.direction.upper()} fill — pending dropped."
            )
            return

        quoted = candle["open"]
        # Bid model: long entry pays the full spread (= Ask); short entry hits
        # the chart Bid; ExecutionModel layers slippage on top in the
        # unfavourable direction.
        price = self.execution_model.adjust_fill_price(
            quoted,
            po.direction,
            self.asset_config,
            volume=po.volume,
            bar_spread_points=self.spread_at(self.current_idx),
        )
        candle_time_raw = candle["time"]  # already np.datetime64 on the fast path
        candle_time = str(candle_time_raw)
        try:
            entry_time_np = np.datetime64(candle_time_raw)
        except (ValueError, TypeError):
            entry_time_np = None

        self.position = Position(
            direction=po.direction,
            entry_price=price,
            entry_index=self.current_idx,
            entry_time=candle_time,
            entry_time_np=entry_time_np,
            volume=po.volume,
            sl=po.sl,
            tp=po.tp,
            initial_sl=po.sl,
            tag=po.tag,
        )

        # Logging path: only convert to a Timestamp when we actually have to
        # render a string, never inside a hot loop. (TZ Phase 2 / B2.)
        t_str = pd.Timestamp(candle_time_raw).strftime("%Y-%m-%d %H:%M")
        sl_str = f", SL: {po.sl:.2f}" if po.sl else ""
        tp_str = f", TP: {po.tp:.2f}" if po.tp else ""
        slip_str = f" (slip+spread: {price - quoted:+.4f})" if price != quoted else ""
        logger.info(
            f"[{t_str}] Engine: Opened {po.direction.upper()} at {price:.4f} "
            f"(Vol: {po.volume:.2f}{sl_str}{tp_str}){slip_str}"
        )
        if not self._suppress_signals:
            self._fire(self.on_position_changed)

    # ------------------------------------------------------------------
    # SL/TP Simulation
    # ------------------------------------------------------------------
    def _check_sl_tp(self, candle: Dict[str, Any]) -> None:
        """Resolve SL/TP hits with **worst-case** assumption on collision.

        If both SL and TP are touched in the same bar we cannot know the
        intra-bar order without ticks; assume SL hit first (conservative).

        Spread handling (Bid model):
            Charts are plotted on the Bid side. Closing a short position
            requires *buying* at the Ask = Bid + spread, so for shorts the
            effective triggers are::

                SL_short hit  when (High + spread) >= SL  ⇒ Ask_high >= SL
                TP_short hit  when (Low  + spread) <= TP  ⇒ Ask_low  <= TP

            Long positions exit via Bid (sell), so their triggers are
            unchanged and use the candle's raw High/Low directly.

        Slippage on stop fills (TZ Phase 5 / C1+A4):
            Triggers are taken on the candle edge, but the *fill* widens
            by ``slippage_price`` in the adverse direction — a stop is
            filled at the next available price, not at the resting level.
            ``adjust_close_price`` is intentionally not used for stops:
            it would re-apply the spread on shorts, which is already
            captured by the Ask-side trigger comparison above.

        Gap-through-stop (TZ Phase 5 / C2):
            If the bar opens beyond the stop in the adverse direction
            we fill at the open price (further widened by slippage) and
            tag the trade with reason ``GAP_SL`` so reporting can
            surface gap risk separately from a normal stop-out.
        """
        if self.position is None:
            return
        pos = self.position
        sl = pos.sl
        tp = pos.tp
        bar_open = candle["open"]
        high = candle["high"]
        low = candle["low"]
        # Phase 1.3 — slippage scales with position volume.
        slip = self.execution_model.slippage_price(self.asset_config, volume=pos.volume)

        if pos.direction == "long":
            # Gap-through-SL: bar open already beyond the stop. Fill at
            # the open (worse than sl) further degraded by slippage.
            if sl is not None and bar_open < sl:
                self.execute_close(
                    price=bar_open - slip,
                    reason="GAP_SL",
                    exit_candle=candle,
                )
                return
            if sl is not None and low <= sl:
                base_reason = (
                    "LOCK"
                    if pos.lock_activated
                    else ("BE" if pos.be_activated else "SL")
                )
                # SL/BE/LOCK fill below the stop by the configured slippage.
                self.execute_close(
                    price=sl - slip,
                    reason=base_reason,
                    exit_candle=candle,
                )
            elif tp is not None and high >= tp:
                # TP slips against the trader (less profit) so the
                # backtest stays conservative — see C1.
                self.execute_close(
                    price=tp - slip,
                    reason="TP",
                    exit_candle=candle,
                )
        else:  # short — buy back at Ask = Bid + spread
            spread = self.execution_model.spread_price(
                self.asset_config,
                spread_points_override=self.spread_at(self.current_idx),
            )
            ask_open = bar_open + spread
            ask_high = high + spread
            ask_low = low + spread
            # Gap-through-SL on shorts: bar opens above the stop on Ask.
            if sl is not None and ask_open > sl:
                self.execute_close(
                    price=ask_open + slip,
                    reason="GAP_SL",
                    exit_candle=candle,
                )
                return
            if sl is not None and ask_high >= sl:
                base_reason = (
                    "LOCK"
                    if pos.lock_activated
                    else ("BE" if pos.be_activated else "SL")
                )
                self.execute_close(
                    price=sl + slip,
                    reason=base_reason,
                    exit_candle=candle,
                )
            elif tp is not None and ask_low <= tp:
                self.execute_close(
                    price=tp + slip,
                    reason="TP",
                    exit_candle=candle,
                )

    def _update_mae_mfe(self, candle: Dict[str, Any]) -> None:
        """Update max adverse / favourable excursion for the open position.

        Phase 1.6 — when this is the *entry bar* (the bar whose open
        price was used to fill the order) we cannot tell from OHLC
        whether the bar's High / Low occurred *before* or *after* the
        entry. The conservative simplification is to treat the entry
        bar as if only the (entry_price → close) excursion happened:
        High and Low are ignored and MAE / MFE come from the close
        relative to entry. Subsequent bars use the regular
        High / Low envelope.
        """
        pos = self.position
        if pos is None:
            return

        is_entry_bar = pos.entry_index == self.current_idx
        if is_entry_bar:
            close_price = float(candle["close"])
            if pos.direction == "long":
                adverse = max(0.0, pos.entry_price - close_price)
                favorable = max(0.0, close_price - pos.entry_price)
            else:
                adverse = max(0.0, close_price - pos.entry_price)
                favorable = max(0.0, pos.entry_price - close_price)
        elif pos.direction == "long":
            adverse = pos.entry_price - candle["low"]
            favorable = candle["high"] - pos.entry_price
        else:
            adverse = candle["high"] - pos.entry_price
            favorable = pos.entry_price - candle["low"]

        if adverse > pos.mae:
            pos.mae = adverse
        if favorable > pos.mfe:
            pos.mfe = favorable

    def _update_mae_mfe_fast(self, idx: int) -> None:
        """Zero-alloc MAE/MFE update using direct array access.

        Phase 1.6 — entry bar uses close-only excursion (see
        :py:meth:`_update_mae_mfe` for rationale).
        """
        pos = self.position
        if pos is None:
            return
        if pos.entry_index == idx:
            close_price = float(self._close[idx])
            if pos.direction == "long":
                adverse = max(0.0, pos.entry_price - close_price)
                favorable = max(0.0, close_price - pos.entry_price)
            else:
                adverse = max(0.0, close_price - pos.entry_price)
                favorable = max(0.0, pos.entry_price - close_price)
        else:
            high = float(self._high[idx])
            low = float(self._low[idx])
            if pos.direction == "long":
                adverse = pos.entry_price - low
                favorable = high - pos.entry_price
            else:
                adverse = high - pos.entry_price
                favorable = pos.entry_price - low
        if adverse > pos.mae:
            pos.mae = adverse
        if favorable > pos.mfe:
            pos.mfe = favorable

    def _check_sl_tp_fast(self, idx: int) -> None:
        """Zero-alloc SL/TP check using direct array access.

        Semantically identical to ``_check_sl_tp`` but avoids building
        a candle dict. Falls back to the dict-based path for the
        actual close (which needs the dict for logging / trade record).
        """
        if self.position is None:
            return
        pos = self.position
        sl = pos.sl
        tp = pos.tp
        if sl is None and tp is None:
            return
        bar_open = float(self._open[idx])
        high = float(self._high[idx])
        low = float(self._low[idx])
        # Phase 1.3 — slippage scales with position volume.
        slip = self.execution_model.slippage_price(self.asset_config, volume=pos.volume)

        if pos.direction == "long":
            if sl is not None and bar_open < sl:
                self.execute_close(
                    price=bar_open - slip,
                    reason="GAP_SL",
                    exit_candle=self.candle_at(idx),
                )
                return
            if sl is not None and low <= sl:
                base_reason = (
                    "LOCK"
                    if pos.lock_activated
                    else ("BE" if pos.be_activated else "SL")
                )
                self.execute_close(
                    price=sl - slip, reason=base_reason, exit_candle=self.candle_at(idx)
                )
            elif tp is not None and high >= tp:
                self.execute_close(
                    price=tp - slip, reason="TP", exit_candle=self.candle_at(idx)
                )
        else:
            spread = self.execution_model.spread_price(
                self.asset_config,
                spread_points_override=self.spread_at(idx),
            )
            ask_open = bar_open + spread
            ask_high = high + spread
            ask_low = low + spread
            if sl is not None and ask_open > sl:
                self.execute_close(
                    price=ask_open + slip,
                    reason="GAP_SL",
                    exit_candle=self.candle_at(idx),
                )
                return
            if sl is not None and ask_high >= sl:
                base_reason = (
                    "LOCK"
                    if pos.lock_activated
                    else ("BE" if pos.be_activated else "SL")
                )
                self.execute_close(
                    price=sl + slip, reason=base_reason, exit_candle=self.candle_at(idx)
                )
            elif tp is not None and ask_low <= tp:
                self.execute_close(
                    price=tp + slip, reason="TP", exit_candle=self.candle_at(idx)
                )

    # ------------------------------------------------------------------
    # Trading actions
    # ------------------------------------------------------------------
    def execute_close(
        self,
        price: Optional[float] = None,
        reason: str = "MANUAL",
        exit_candle: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Close the current position."""
        if self.position is None:
            return

        candle = exit_candle if exit_candle is not None else self.ctx.candle
        if price is None:
            price = candle["close"]

        pos = self.position

        # ExecutionModel: only adjust the exit price for *non-stop* closes.
        # SL/TP/LOCK/BE prices are user-anchored; spreading them would punish twice
        # because a strategy already chose those exact levels for risk reasons.
        if reason in ("MANUAL", "EOD", "WEEKEND"):
            price = self.execution_model.adjust_close_price(
                price,
                pos.direction,
                self.asset_config,
                volume=pos.volume,
                bar_spread_points=self.spread_at(self.current_idx),
            )

        # PnL in points and dollars (before commission)
        if pos.direction == "long":
            pnl_points = price - pos.entry_price
        else:
            pnl_points = pos.entry_price - price
        gross_pnl = pnl_points * pos.volume * self.asset_config.point_value

        # Apply commission as an explicit deduction (separate from price-side costs)
        costs = self.execution_model.costs_for_round_trip(pos.volume, self.asset_config)
        pnl = gross_pnl - costs.commission_cost

        # Quantise to cents
        pnl = _round_to_step(pnl, 0.01)

        prev_equity = self.equity
        self.equity = _round_to_step(self.equity + pnl, 0.01)

        # Drive the centralised tracker, then mirror to the legacy fields.
        # (TZ Phase 4 / Etap 4.1.)
        self.drawdown.update(self.equity)
        self.max_equity = self.drawdown.peak_equity
        self.max_drawdown_abs = self.drawdown.max_drawdown_abs
        self.max_drawdown_pct = self.drawdown.max_drawdown_pct

        # Duration — fast path uses the cached ``entry_time_np`` (set in
        # ``_execute_open``) and the candle's ``time`` (already np.datetime64
        # in the canonical loader) so we avoid a per-trade ``pd.to_datetime``
        # round-trip. (TZ Phase 2 / B2.)
        duration = 0
        exit_time_raw = candle["time"]
        if pos.entry_time_np is not None:
            try:
                exit_np = np.datetime64(exit_time_raw)
                delta = exit_np - pos.entry_time_np
                duration = max(0, int(delta / np.timedelta64(1, "m")))
            except (ValueError, TypeError):
                duration = 0
        if duration == 0 and pos.entry_time_np is None:
            # Slow fallback for adapters that don't pre-cache the np datetime.
            try:
                entry_time = pd.to_datetime(pos.entry_time)
                exit_time = pd.to_datetime(exit_time_raw)
                duration = max(0, int((exit_time - entry_time).total_seconds() / 60))
            except (ValueError, TypeError):
                duration = 0

        session = get_session(pos.entry_time, asset=self.asset_config)

        # Sprint 4.2 cross-cuts — derive day-of-week & hour-of-day from
        # the entry timestamp once, here, so downstream consumers don't
        # have to reparse strings.
        dow_label = "Unknown"
        hod_value = -1
        try:
            entry_ts = pd.Timestamp(pos.entry_time)
            dow_label = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][
                int(entry_ts.weekday())
            ]
            hod_value = int(entry_ts.hour)
        except (TypeError, ValueError):
            pass

        # Signed R-multiple
        rr: Optional[float] = None
        if pos.initial_sl is not None and pos.initial_sl > 0:
            sl_distance = abs(pos.entry_price - pos.initial_sl)
            if sl_distance > 0:
                rr = pnl_points / sl_distance

        mae_dollars = pos.mae * pos.volume * self.asset_config.point_value
        mfe_dollars = pos.mfe * pos.volume * self.asset_config.point_value

        self._next_trade_id += 1
        trade = Trade(
            id=self._next_trade_id,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=price,
            pnl=pnl,
            pnl_points=pnl_points,
            entry_index=pos.entry_index,
            exit_index=self.current_idx,
            entry_time=str(pos.entry_time),
            exit_time=str(candle["time"]),
            volume=pos.volume,
            duration_minutes=duration,
            exit_reason=reason,
            mae=pos.mae,
            mfe=pos.mfe,
            mae_dollars=_round_to_step(mae_dollars, 0.01),
            mfe_dollars=_round_to_step(mfe_dollars, 0.01),
            session=session,
            initial_sl=pos.initial_sl if pos.initial_sl is not None else 0.0,
            rr=rr,
            tag=pos.tag,
            commission=_round_to_step(costs.commission_cost, 0.01),
            spread_cost=_round_to_step(costs.spread_cost, 0.01),
            slippage_cost=_round_to_step(costs.slippage_cost, 0.01),
            dow=dow_label,
            hod=hod_value,
        )

        self.trades.append(trade)
        self.equity_track.append(self.equity)
        self.position = None
        self.pending_order = None

        # Incremental stats — both managers and legacy mirror fields stay in sync
        self.stats.update(trade)
        self._stats_wins = self.stats.wins
        self._stats_losses = self.stats.losses
        self._stats_gross_profit = self.stats.gross_profit
        self._stats_gross_loss = self.stats.gross_loss
        self._stats_longs = self.stats.longs
        self._stats_shorts = self.stats.shorts
        self._stats_dirty = True

        if not self._suppress_signals:
            self._fire(self.on_trade_completed, trade)
            self._fire(self.on_position_changed)

        # Logging only — Timestamp() handles np.datetime64 directly without
        # the parsing overhead of pd.to_datetime. (TZ Phase 2 / B2.)
        t_str = pd.Timestamp(exit_time_raw).strftime("%Y-%m-%d %H:%M")
        logger.info(
            f"[{t_str}] Engine: Closed {trade.direction.upper()} at {price:.2f} | "
            f"PnL: {pnl:.2f} ({pnl_points:+.1f}pts) | Reason: {reason} | "
            f"Eq: {self.equity:.2f} (Δ {self.equity - prev_equity:+.2f})"
        )

    # ------------------------------------------------------------------
    # Drawing management
    # ------------------------------------------------------------------
    def add_drawing(self, kwargs: Dict[str, Any]) -> int:
        self._next_drawing_id += 1
        kwargs["_id"] = self._next_drawing_id
        # Cache numpy datetime64 for fast rendering
        if "t" in kwargs:
            kwargs["_t_np"] = pd.to_datetime(kwargs["t"]).to_datetime64()
        if "t1" in kwargs:
            kwargs["_t1_np"] = pd.to_datetime(kwargs["t1"]).to_datetime64()
        if "t2" in kwargs:
            kwargs["_t2_np"] = pd.to_datetime(kwargs["t2"]).to_datetime64()
        self.drawings.append(kwargs)
        if not self._suppress_signals:
            self._fire(self.on_drawing_changed, self._next_drawing_id)
        return self._next_drawing_id

    def remove_drawing(self, d_id: int) -> None:
        before = len(self.drawings)
        self.drawings = [d for d in self.drawings if d.get("_id") != d_id]
        if len(self.drawings) != before and not self._suppress_signals:
            self._fire(self.on_drawing_changed, d_id)

    def update_drawing(self, d_id: int, updates: Dict[str, Any]) -> None:
        for d in self.drawings:
            if d.get("_id") == d_id:
                d.update(updates)
                if not self._suppress_signals:
                    self._fire(self.on_drawing_changed, d_id)
                break

    def register_layer(self, tag: str, label: str, default_on: bool = True) -> None:
        """Register a layer for UI toggle. Idempotent."""
        if tag in self._registered_layers:
            return
        self._registered_layers.add(tag)
        self._fire(self.on_layer_registered, tag, label, default_on)

    # ------------------------------------------------------------------
    # Vectorised (Quant) execution mode
    # ------------------------------------------------------------------
    def run_vectorized(self) -> Optional[VectorizedResult]:
        """Execute the loaded strategy via the fast-quant vectorised engine.

        Mirrors the on-candle ``run_to_end`` path — the engine state
        (`trades`, `equity`, `equity_track`, `max_drawdown_pct`,
        `current_idx`) is updated and the relevant callbacks fire so the
        GUI (or any subscriber) refreshes just like after a Visual run.
        Returns the raw :class:`engine.vectorized_engine.VectorizedResult`
        for callers that want to inspect the equity curve directly.
        """
        from engine.execution.vectorized_engine import run_vectorized as _run_vec

        if self.df is None or self.strategy is None:
            logger.warning("run_vectorized: data or strategy not loaded")
            return None

        # Reset state before a fresh quant run (clears trades + equity).
        self.reset()
        result = _run_vec(
            self.df,
            self.strategy,
            asset=self.asset_config,
            execution_model=self.execution_model,
            initial_equity=self.initial_equity,
            risk_pct=self.risk_pct,
        )

        # Materialise the result into engine state expected by the GUI tabs.
        self.trades = list(result.trades)
        # Mirror the vectorised engine's skipped-trades list so the GUI's
        # log / stats display the same number after both Visual and Quant
        # runs (TZ Phase 6 / A8). Empty when ``enforce_trading_hours`` is
        # off, so this is a no-op for most users.
        self.skipped_trades = list(result.skipped_trades)
        self.equity_track = result.equity_curve.tolist()
        if self.equity_track:
            self.equity = float(self.equity_track[-1])
        # Replay the equity curve through the centralised tracker so peak /
        # DD numbers match the on-candle path (TZ Phase 4 / Etap 4.1). The
        # legacy mirror fields are derived from it for backwards compat.
        self.drawdown.reset(self.initial_equity)
        for eq in self.equity_track:
            self.drawdown.update(float(eq))
        self.max_equity = self.drawdown.peak_equity
        self.max_drawdown_pct = self.drawdown.max_drawdown_pct
        self.max_drawdown_abs = self.drawdown.max_drawdown_abs
        self.current_idx = max(0, self.total_bars - 1)

        # Repopulate the centralised StatsAccumulator and the legacy mirror
        # fields together so future migrations to ``self.stats`` (TZ Phase 4)
        # cannot silently desynchronise after a Quant run — TZ Phase 1 / A5.
        self.stats.reset()
        for t in self.trades:
            self.stats.update(t)
        self._stats_wins = self.stats.wins
        self._stats_losses = self.stats.losses
        self._stats_gross_profit = self.stats.gross_profit
        self._stats_gross_loss = self.stats.gross_loss
        self._stats_longs = self.stats.longs
        self._stats_shorts = self.stats.shorts
        self._stats_dirty = True

        self.update_stats()
        self._fire(self.on_simulation_finished)
        logger.info(
            "⚡ Vectorised run complete | Trades=%d | Final equity=$%.2f | Peak DD=%.2f%%",
            len(self.trades),
            self.equity,
            self.max_drawdown_pct,
        )
        return result

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def update_stats(self) -> None:
        total_trades = len(self.trades)
        wins = self._stats_wins
        losses = self._stats_losses
        # Win-rate excludes break-even trades from the denominator (TZ Phase 1 / A9):
        # ``wins / (wins + losses)`` matches the industry-standard convention used
        # in NinjaTrader / TradeStation. ``winrate_inclusive`` keeps the legacy
        # behaviour for any callers that explicitly want it.
        decisive = wins + losses
        winrate = (wins / decisive * 100) if decisive > 0 else 0.0
        winrate_inclusive = (wins / total_trades * 100) if total_trades > 0 else 0.0

        gross_profit = self._stats_gross_profit
        gross_loss = self._stats_gross_loss
        total_pnl = gross_profit - gross_loss

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        # Recovery factor based on **absolute** drawdown ($)
        if self.max_drawdown_abs > 0:
            recovery_factor = total_pnl / self.max_drawdown_abs
        elif total_pnl > 0:
            recovery_factor = float("inf")
        else:
            recovery_factor = 0.0

        expectancy = (total_pnl / total_trades) if total_trades > 0 else 0.0

        status = "None"
        if self.position is not None:
            sl_str = f", SL: {self.position.sl:.2f}" if self.position.sl else ""
            tp_str = f", TP: {self.position.tp:.2f}" if self.position.tp else ""
            status = (
                f"{self.position.direction.upper()} @ {self.position.entry_price:.2f} "
                f"(Vol: {self.position.volume:.2f}{sl_str}{tp_str})"
            )

        stats = {
            "total_trades": total_trades,
            "decisive_trades": decisive,
            "break_evens": max(0, total_trades - decisive),
            "winrate": winrate,
            "winrate_inclusive": winrate_inclusive,
            "total_pnl": total_pnl,
            "longs": self._stats_longs,
            "shorts": self._stats_shorts,
            "profit_factor": profit_factor,
            "recovery_factor": recovery_factor,
            "max_drawdown": self.max_drawdown_pct,
            "max_drawdown_abs": self.max_drawdown_abs,
            "expectancy": expectancy,
            "equity": self.equity,
            "pos_status": status,
            "asset": self.asset_config.name,
            "currency": self.asset_config.currency,
        }
        self._fire(self.on_stats_updated, stats)


class _LazyCandleList:
    """Backwards-compatible candle accessor.

    Mimics the historical ``BacktestCore._candle_list`` (a list of
    dicts) without ever materialising the list. Each ``__getitem__``
    rebuilds a single candle dict from the engine's NumPy columns.
    Supports ``len()``, ``__bool__``, indexing, and (slow) iteration so
    GUI code that does ``engine._candle_list[idx]`` keeps working
    unchanged after the dict→NumPy migration.
    """

    __slots__ = ("_core",)

    def __init__(self, core: "BacktestCore") -> None:
        self._core = core

    def __len__(self) -> int:
        return self._core.total_bars

    def __bool__(self) -> bool:
        return self._core.total_bars > 0

    def __getitem__(self, idx: Any) -> Any:
        if isinstance(idx, slice):
            indices = range(*idx.indices(self._core.total_bars))
            return [self._core.candle_at(i) for i in indices]
        return self._core.candle_at(int(idx))

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        for i in range(self._core.total_bars):
            yield self._core.candle_at(i)


__all__ = ["BacktestCore", "_round_to_step"]
