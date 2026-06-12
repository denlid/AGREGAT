"""
Context & Strategy — The API surface exposed to strategies.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from engine.models.models import (
    AssetConfig,
    PendingOrder,
    Position,
    get_session,
    is_trading_hours,
)

logger = logging.getLogger("StrategyTester.engine.context")


@dataclass
class Param:
    """Declarative description of a single tunable strategy parameter.

    Strategies advertise their tunables via the class-level ``PARAMS``
    list; the GUI walks that list to auto-build a form (Settings →
    Strategy sub-tab) and persisted values flow back into
    ``self.params[name]`` before each backtest. Keep one instance per
    parameter — every field that needs to live behind a checkbox / spin
    box / combobox is one ``Param``.

    Fields
    ------
    name        Stable identifier used as a dict key (snake_case).
    label       Human-readable label shown in the GUI.
    kind        One of ``"bool" | "int" | "float" | "choice"``.
    default     The value used until the user overrides it.
    min, max    Clamps for numeric kinds (ignored for bool / choice).
    step        Spinbox step for numeric kinds.
    choices     Allowed string values for ``"choice"`` kind.
    tooltip     Optional explanation surfaced as a tooltip.
    """

    name: str
    label: str
    kind: str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: Optional[List[str]] = None
    tooltip: str = ""


class Strategy:
    """Base strategy interface. Strategies may inherit from this but don't have to.

    A strategy is free to implement either or both of the two execution
    APIs:

    * :pymeth:`on_candle` — the original step-by-step API used by the
      Visual / live-ready engine (:class:`engine.backtest_engine.BacktestEngine`).
    * :pymeth:`vectorized_signals` — an optional NumPy-friendly API used by
      the fast-quant engine (:func:`engine.vectorized_engine.run_vectorized`).
      Strategies that only care about the on-candle path can leave this
      unimplemented; the loader uses :func:`has_vectorized_signals` to
      detect support at runtime.

    Parameter API
    -------------
    Subclasses can advertise tunable parameters via the ``PARAMS`` class
    attribute (a list of :class:`Param`). Default values populate
    ``self.params`` on construction; the GUI overrides them via
    :pymeth:`apply_params` before each backtest.

    HTF close hooks
    ---------------
    Strategies may also implement methods of the form
    ``on_<tf>_close(self, ctx)`` (e.g. ``on_15m_close``). The engine
    invokes them right after the corresponding HTF bucket completes —
    *before* the per-1m :pymeth:`on_candle` hook on the same bar — so
    that "active management" decisions tied to the higher timeframe
    (counter-CISD exits, trailing stops anchored to HTF pivots, …) can
    be expressed without manually polling ``ctx.completed_tfs``.
    """

    PARAMS: List[Param] = []

    def __init__(self) -> None:
        self.params: Dict[str, Any] = {p.name: p.default for p in self.PARAMS}

    def apply_params(self, overrides: Optional[Dict[str, Any]]) -> None:
        """Merge user-supplied overrides into ``self.params``.

        Unknown keys are ignored (forward compatibility with old
        strategies); declared keys are coerced to the parameter's
        declared kind so a stray ``"true"`` from JSON deserialisation
        does not silently become ``bool("true") == True`` only on some
        Python versions.
        """
        if not overrides:
            return
        kinds = {p.name: p for p in self.PARAMS}
        for key, value in overrides.items():
            spec = kinds.get(key)
            if spec is None:
                continue
            try:
                if spec.kind == "bool":
                    self.params[key] = bool(value)
                elif spec.kind == "int":
                    self.params[key] = int(value)
                elif spec.kind == "float":
                    self.params[key] = float(value)
                elif spec.kind == "choice":
                    sval = str(value)
                    if spec.choices and sval not in spec.choices:
                        logger.warning(
                            f"Strategy.apply_params: value {sval!r} for "
                            f"{key!r} not in choices {spec.choices!r}; "
                            f"keeping default {spec.default!r}"
                        )
                        continue
                    self.params[key] = sval
                else:
                    self.params[key] = value
            except (TypeError, ValueError):
                logger.warning(
                    f"Strategy.apply_params: bad value for {key!r}={value!r}; "
                    f"keeping default {spec.default!r}"
                )

    def on_candle(self, ctx: "Context") -> None:
        pass

    def build_vectorized_features(self, df: Any, prepared: Any = None) -> Any:
        """Optional precompute hook for optimizer-style repeated runs.

        Strategies may override this to split heavy feature construction
        from cheap signal generation. The default implementation returns
        ``None``, signalling that :meth:`vectorized_signals` should be
        used directly.
        """
        return None

    def feature_params(self) -> Any:
        """Return the subset of params that influence built features.

        Used by the engine-side feature cache (Roadmap v2 / S1.5) to decide
        when cached features can be reused across runs. Features depend ONLY
        on these params — varying any other param (e.g. SL/TP thresholds)
        does not invalidate the cache.

        The default returns ``None``, which disables feature caching for the
        strategy (every run rebuilds features). Strategies implementing the
        feature-cache contract should override this to return a hashable tuple
        of the indicator params, e.g.::

            def feature_params(self):
                return (self.params["fast_ema"], self.params["slow_ema"])

        Returning ``None`` is safe but forfeits the cross-run cache benefit.
        """
        return None

    def vectorized_signals_from_features(self, features: Any, params: Any = None) -> Any:
        """Optional signal-generation hook from a prepared feature bundle.

        Strategies that override :meth:`build_vectorized_features` should
        typically override this method as well.
        """
        raise NotImplementedError(
            "Strategy.vectorized_signals_from_features is not implemented. "
            "Override it when using build_vectorized_features()."
        )

    def vectorized_signals(self, df: Any) -> Any:  # noqa: D401 — interface stub
        """Return a per-bar signal frame for the vectorised engine.

        Default implementation raises :class:`NotImplementedError` so the
        :func:`run_vectorized` pre-check (``hasattr``) reports the
        capability as missing rather than silently producing empty
        results. See :func:`engine.vectorized_engine.run_vectorized` for
        the expected frame schema.
        """
        raise NotImplementedError(
            "Strategy.vectorized_signals is not implemented. "
            "Add it to your strategy class or run with the Visual engine."
        )


def has_vectorized_signals(strategy: "Strategy") -> bool:
    """Return True iff `strategy` actually overrides
    :pymeth:`Strategy.vectorized_signals`.

    Used by the GUI to decide whether the "Quant" run-mode is offered to
    the user. We compare against the base function via ``__func__`` so
    that a class that simply inherits from :class:`Strategy` without
    overriding the method does not trigger the capability.
    """
    method = getattr(strategy, "vectorized_signals", None)
    if method is None:
        return False
    base_method = Strategy.vectorized_signals
    bound_func = getattr(method, "__func__", None)
    return bound_func is not base_method


class StrategyLoader:
    """Loads a user-supplied .py strategy file.

    NOTE: there is **no real sandbox** — the loaded code runs with the same
    privileges as AGREGAT itself. Only load strategies you trust.
    """

    @staticmethod
    def load(filepath: str) -> Strategy:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Strategy file not found: {filepath}")

        module_name = os.path.splitext(os.path.basename(filepath))[0]
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load specifications for {filepath}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error(f"❌ Strategy execution failed: {e!r}")
            raise

        # Try 1: engine.api.Strategy objects (new simplified API)
        try:
            from engine.api import Strategy as ApiStrategy
            for item_name in dir(module):
                item = getattr(module, item_name)
                if isinstance(item, ApiStrategy):
                    strategy_class = item._build_class()
                    instance: Strategy = strategy_class()
                    return instance
        except ImportError:
            pass  # engine.api not available, fall through

        # Try 2: legacy class-based Strategy subclasses
        strategy_class = None
        for item_name in dir(module):
            item = getattr(module, item_name)
            if not isinstance(item, type):
                continue
            if item.__name__ == "Strategy":
                continue
            if hasattr(item, "on_candle") or hasattr(item, "vectorized_signals"):
                strategy_class = item
                break

        if not strategy_class:
            raise ValueError(
                f"No valid strategy class found in {filepath}; expected at least "
                "one of: on_candle(ctx) or vectorized_signals(df), "
                "or an engine.api.Strategy instance."
            )

        instance: Strategy = strategy_class()
        return instance


class Context:
    """
    Context passed to strategy's on_candle method.

    Properties:
        candle         — current 1m candle dict
        position       — current Position or None
        equity         — current equity
        initial_equity — starting equity
        asset          — AssetConfig for the current asset
        completed_tfs  — dict of {tf: bool} indicating which TFs completed this step

    Methods:
        buy_at_open(volume, sl=, tp=)           — queue LONG entry at next candle's open
        sell_at_open(volume, sl=, tp=)          — queue SHORT entry at next candle's open
        cancel_pending()                        — drop any pending order
        close(price=, reason=)                  — close current position
        update_sl(new_sl) / update_tp(new_tp)   — update position stops
        log(message)                            — log to console
        get_tf(tf)                              — completed TF candles (Zero-Future safe)
        get_session(time=) / is_trading_hours() — session helpers
        calculate_lot(sl_pts, risk_pct=)        — risk-based lot sizing (clamped)
        draw_line/rect/text/remove/update       — drawing API
        remove/update_drawings_by_tag           — batch drawing API by layer tag
        register_layer(tag, label, default_on)  — add a toggleable UI layer
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.candle: Dict[str, Any] = {}
        self._completed_tfs: Dict[str, bool] = {}
        # ``ctx.data`` is the modern strategy-facing API: read-only NumPy
        # slices that respect the Zero-Future Rule.
        self.data = getattr(engine, "data", None)

    # --- Position & Equity ---
    @property
    def position(self) -> Optional[Position]:
        pos: Optional[Position] = self._engine.position
        return pos

    @property
    def equity(self) -> float:
        eq: float = self._engine.equity
        return eq

    @property
    def initial_equity(self) -> float:
        ie: float = self._engine.initial_equity
        return ie

    @property
    def asset(self) -> AssetConfig:
        ac: AssetConfig = self._engine.asset_config
        return ac

    @property
    def completed_tfs(self) -> Dict[str, bool]:
        return self._completed_tfs

    # --- TF Data Access (Zero-Future Safe) ---
    def get_tf(self, tf: str) -> Any:
        """Read-only NumPy-backed view of completed HTF bars.

        Returns an :class:`engine.htf_view.HTFView` (the HTF mirror of
        ``ctx.data``). Use the same NumPy ergonomics — ``view.close[-1]``
        for the most recent close, ``view.high[-N:]`` for the last N
        highs, etc. The slice respects the Zero-Future Rule: only HTF
        bars whose last constituent 1m bar has been processed are
        visible.

        Strategies that still want a list of dicts can call
        :py:meth:`get_tf_dicts`. (TZ Phase 3 — vectorized HTF.)
        """
        return self._engine.tf_aggregator.view(tf)

    def get_tf_dicts(self, tf: str) -> List[Dict[str, Any]]:
        """Legacy ``List[dict]`` accessor for completed HTF candles.

        Kept for callers (e.g. persistence, GUI redraw helpers) that
        consume HTF data as dicts. New strategy code should prefer
        :py:meth:`get_tf` which returns the read-only ``HTFView``.
        """
        completed: List[Dict[str, Any]] = self._engine.tf_aggregator.get_completed(tf)
        return completed

    def get_tf_forming(self, tf: str) -> Optional[Dict[str, Any]]:
        """Get the current forming (incomplete) HTF candle as a dict.
        NOT Zero-Future safe — visualisation only.
        """
        forming: Optional[Dict[str, Any]] = self._engine.tf_aggregator.get_forming(tf)
        return forming

    # --- Legacy Trading API ---
    def close(self, price: Optional[float] = None, reason: str = "MANUAL") -> None:
        """Close current position at given price (default: current candle close)."""
        self._engine.execute_close(price=price, reason=reason, exit_candle=self.candle)

    # --- New Trading API (pending order at next open) ---
    @staticmethod
    def _validate_sl_tp(
        direction: str,
        sl: Optional[float],
        tp: Optional[float],
        ref_price: float,
    ) -> None:
        """Log warnings for obviously wrong SL/TP values."""
        if sl is not None:
            if sl <= 0:
                logger.warning(f"⚠️ SL={sl} is non-positive — likely a bug.")
            elif direction == "long" and sl > ref_price:
                logger.warning(
                    f"⚠️ LONG SL={sl:.4f} is above current price {ref_price:.4f} "
                    f"— will likely trigger immediately."
                )
            elif direction == "short" and sl < ref_price:
                logger.warning(
                    f"⚠️ SHORT SL={sl:.4f} is below current price {ref_price:.4f} "
                    f"— will likely trigger immediately."
                )
        if tp is not None:
            if tp <= 0:
                logger.warning(f"⚠️ TP={tp} is non-positive — likely a bug.")
            elif direction == "long" and tp < ref_price:
                logger.warning(
                    f"⚠️ LONG TP={tp:.4f} is below current price {ref_price:.4f} "
                    f"— will likely trigger immediately."
                )
            elif direction == "short" and tp > ref_price:
                logger.warning(
                    f"⚠️ SHORT TP={tp:.4f} is above current price {ref_price:.4f} "
                    f"— will likely trigger immediately."
                )
        if sl is not None and tp is not None and sl == tp:
            logger.warning(f"⚠️ SL == TP ({sl}) — trade has zero room.")

    def buy_at_open(
        self,
        volume: float = 1.0,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        reason: str = "",
        tag: str = "",
    ) -> bool:
        """Queue a LONG entry at the open of the next 1m candle."""
        if self._engine.position is not None:
            logger.warning("buy_at_open ignored: position already open.")
            return False
        if self._engine.pending_order is not None:
            logger.warning("buy_at_open ignored: pending order already exists.")
            return False
        self._validate_sl_tp("long", sl, tp, self.candle.get("close", 0))
        self._engine.pending_order = PendingOrder(
            direction="long",
            volume=volume,
            sl=sl,
            tp=tp,
            reason=reason,
            tag=tag,
        )
        t_str = self._format_time()
        sl_str = f", SL={sl}" if sl is not None else ""
        tp_str = f", TP={tp}" if tp is not None else ""
        logger.info(
            f"{t_str}[Strategy] Queued LONG at next open (Vol: {volume:.2f}{sl_str}{tp_str})"
        )
        return True

    def sell_at_open(
        self,
        volume: float = 1.0,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        reason: str = "",
        tag: str = "",
    ) -> bool:
        """Queue a SHORT entry at the open of the next 1m candle."""
        if self._engine.position is not None:
            logger.warning("sell_at_open ignored: position already open.")
            return False
        if self._engine.pending_order is not None:
            logger.warning("sell_at_open ignored: pending order already exists.")
            return False
        self._validate_sl_tp("short", sl, tp, self.candle.get("close", 0))
        self._engine.pending_order = PendingOrder(
            direction="short",
            volume=volume,
            sl=sl,
            tp=tp,
            reason=reason,
            tag=tag,
        )
        t_str = self._format_time()
        sl_str = f", SL={sl}" if sl is not None else ""
        tp_str = f", TP={tp}" if tp is not None else ""
        logger.info(
            f"{t_str}[Strategy] Queued SHORT at next open (Vol: {volume:.2f}{sl_str}{tp_str})"
        )
        return True

    def cancel_pending(self) -> bool:
        """Drop any queued pending order. Returns True if one existed."""
        if self._engine.pending_order is None:
            return False
        self._engine.pending_order = None
        logger.info(f"{self._format_time()}[Strategy] Pending order cancelled.")
        return True

    # --- Position Management ---
    def update_sl(self, new_sl: float) -> None:
        """Update the stop-loss of the current position."""
        if self._engine.position is None:
            logger.warning("update_sl ignored: no position open.")
            return
        old_sl = self._engine.position.sl
        self._engine.position.sl = new_sl
        t_str = self._format_time()
        logger.info(f"{t_str}[Strategy] SL updated: {old_sl} → {new_sl}")

    def update_tp(self, new_tp: Optional[float]) -> None:
        """Update the take-profit of the current position."""
        if self._engine.position is None:
            logger.warning("update_tp ignored: no position open.")
            return
        old_tp = self._engine.position.tp
        self._engine.position.tp = new_tp
        t_str = self._format_time()
        logger.info(f"{t_str}[Strategy] TP updated: {old_tp} → {new_tp}")

    def move_sl_to_be(self, offset_points: float = 0.0) -> bool:
        """Phase 3.1 (E4) — move the open position's SL to break-even.

        Convenience wrapper around :py:meth:`update_sl` that handles the
        per-asset point conversion and the long/short sign flip so
        strategies don't have to duplicate the same boilerplate (which
        was the most common in-strategy bug we found in code review:
        callers either forgot to multiply by ``tick_size`` or applied
        the offset in the wrong direction on shorts).

        Args:
            offset_points: Optional buffer **in points** to apply on top
                of the entry price so the SL covers commission /
                slippage. Positive values move the SL into profit
                regardless of trade direction (i.e. up for longs, down
                for shorts).

        Returns:
            ``True`` when the SL was actually moved, ``False`` if no
            position is open or the proposed SL would be looser than
            the current one (we never widen risk in this helper —
            callers that *want* to widen should use
            :py:meth:`update_sl` directly).
        """
        pos = self._engine.position
        if pos is None:
            logger.warning("move_sl_to_be ignored: no position open.")
            return False
        # ``tick_size`` is the per-tick price increment (e.g. 0.5 for
        # DE40); multiplying by ``offset_points`` converts the strategy-
        # facing "points" unit into the engine-native price unit.
        tick = float(getattr(self._engine.asset_config, "tick_size", 1.0) or 1.0)
        offset_price = float(offset_points) * tick
        if pos.direction == "long":
            new_sl = pos.entry_price + offset_price
            # Refuse to widen — only move SL up for longs.
            if pos.sl is not None and new_sl <= pos.sl:
                return False
        else:
            new_sl = pos.entry_price - offset_price
            # Refuse to widen — only move SL down for shorts.
            if pos.sl is not None and new_sl >= pos.sl:
                return False
        self.update_sl(new_sl)
        return True

    # --- Session & Risk ---
    def get_session(self, time: Any = None) -> str:
        """Get session name for a given time (default: current candle time)."""
        t = time if time is not None else self.candle.get("time", "")
        return get_session(t)

    def is_trading_hours(self) -> bool:
        """Check if current candle is within the asset's trading window."""
        t = self.candle.get("time", "")
        return is_trading_hours(t, self._engine.asset_config)

    DEFAULT_ABSOLUTE_MAX_LOTS: float = 100.0

    def calculate_lot(
        self,
        sl_points: float,
        risk_pct: Optional[float] = None,
        absolute_max_lots: Optional[float] = None,
        *,
        strict: bool = False,
    ) -> Optional[float]:  # noqa: D401
        """Calculate lot size from `risk_pct`% of equity at risk on `sl_points` distance.

        Falls back to `engine.risk_pct` (configured in Settings) when risk_pct is None.
        Returns None when sl_points is invalid; clamps to asset.max_lots_per_100k otherwise.
        Lot size is rounded to `asset.lot_step` and floored to `asset.min_lot`.

        Real brokers cap the order size irrespective of margin. The
        `absolute_max_lots` cap (default `DEFAULT_ABSOLUTE_MAX_LOTS` = 100 lots)
        models that hard ceiling: even when free margin would permit thousands
        of lots, the calculated size is clamped down to a realistic order book.
        Pass `absolute_max_lots=float('inf')` to disable the cap explicitly.

        Strict-cap mode (LINEAR LOGIC CORE): when ``strict=True`` and the
        risk-derived lot exceeds ``max_lots_per_100k`` *or* the absolute
        broker cap, the trade is **cancelled** (returns ``None``) instead
        of being silently down-sized. This matches systems where position
        size below the requested risk is considered an unintended deviation
        from the strategy contract.
        """
        if sl_points is None or sl_points <= 0:
            self._engine.skipped_trades.append(
                {
                    "time": str(self.candle.get("time", "")),
                    "reason": "INVALID_SL",
                    "sl_points": sl_points,
                    "equity": self._engine.equity,
                }
            )
            return None
        asset = self._engine.asset_config
        rp = self._engine.risk_pct if risk_pct is None else risk_pct
        risk = self._engine.equity * (rp / 100.0)
        denom = sl_points * asset.point_value
        if denom <= 0:
            return None
        lot = risk / denom

        max_lots = asset.max_lots_per_100k * (self._engine.equity / 100_000.0)
        if lot > max_lots:
            self._engine.skipped_trades.append(
                {
                    "time": str(self.candle.get("time", "")),
                    "reason": "LOT_LIMIT_STRICT" if strict else "LOT_LIMIT",
                    "calculated_lot": lot,
                    "max_lot": max_lots,
                    "sl_points": sl_points,
                    "equity": self._engine.equity,
                }
            )
            t_str = self._format_time()
            if strict:
                logger.info(
                    f"{t_str}[Risk] Trade cancelled (strict cap): "
                    f"lot={lot:.2f} > cap={max_lots:.2f} (sl_pts={sl_points})"
                )
                return None
            logger.info(
                f"{t_str}[Risk] Lot clamped: {lot:.2f} → {max_lots:.2f} (sl_pts={sl_points})"
            )
            lot = max_lots

        # Absolute broker-side cap (independent of margin / equity).
        abs_cap = (
            self.DEFAULT_ABSOLUTE_MAX_LOTS
            if absolute_max_lots is None
            else float(absolute_max_lots)
        )
        if abs_cap > 0 and lot > abs_cap:
            self._engine.skipped_trades.append(
                {
                    "time": str(self.candle.get("time", "")),
                    "reason": "ABS_LOT_LIMIT_STRICT" if strict else "ABS_LOT_LIMIT",
                    "calculated_lot": lot,
                    "absolute_max_lots": abs_cap,
                    "sl_points": sl_points,
                    "equity": self._engine.equity,
                }
            )
            t_str = self._format_time()
            if strict:
                logger.info(
                    f"{t_str}[Risk] Trade cancelled (strict broker cap): "
                    f"lot={lot:.2f} > abs_max={abs_cap:.2f}"
                )
                return None
            logger.info(
                f"{t_str}[Risk] Lot capped by broker limit: "
                f"{lot:.2f} → {abs_cap:.2f} (abs_max={abs_cap})"
            )
            lot = abs_cap

        # Snap to lot_step and floor to min_lot
        step = max(asset.lot_step, 1e-9)
        lot = math.floor(lot / step) * step
        if lot < asset.min_lot:
            return None
        rounded: float = round(lot, 6)
        return rounded

    # --- Logging ---
    def log(self, message: str) -> None:
        t_str = self._format_time()
        logger.info(f"{t_str}[Strategy] {message}")

    # --- Layer Registration ---
    def register_layer(self, tag: str, label: str, default_on: bool = True) -> None:
        """Register a toggleable layer in the UI Layers menu. Idempotent."""
        self._engine.register_layer(tag, label, default_on)

    # --- Drawing API ---
    def draw_line(
        self,
        t1: Any,
        p1: float,
        t2: Any,
        p2: float,
        color: str = "yellow",
        width: int = 1,
        tag: str = "",
    ) -> int:
        line_id: int = self._engine.add_drawing(
            {
                "type": "line",
                "t1": str(t1),
                "p1": p1,
                "t2": str(t2),
                "p2": p2,
                "color": color,
                "width": width,
                "tag": tag,
            }
        )
        return line_id

    def draw_rect(
        self,
        t1: Any,
        p1: float,
        t2: Any,
        p2: float,
        color: str = "yellow",
        fill: bool = True,
        opacity: float = 0.3,
        tag: str = "",
    ) -> int:
        rect_id: int = self._engine.add_drawing(
            {
                "type": "rect",
                "t1": str(t1),
                "p1": p1,
                "t2": str(t2),
                "p2": p2,
                "color": color,
                "fill": fill,
                "opacity": opacity,
                "tag": tag,
            }
        )
        return rect_id

    def draw_text(
        self, t: Any, p: float, text: str, color: str = "white", tag: str = ""
    ) -> int:
        text_id: int = self._engine.add_drawing(
            {
                "type": "text",
                "t": str(t),
                "p": p,
                "text": text,
                "color": color,
                "tag": tag,
            }
        )
        return text_id

    def remove_drawing(self, d_id: int) -> None:
        self._engine.remove_drawing(d_id)

    def update_drawing(self, d_id: int, updates: Dict[str, Any]) -> None:
        self._engine.update_drawing(d_id, updates)

    def remove_drawings_by_tag(self, tag: str) -> int:
        return self._engine.remove_drawings_by_tag(tag)

    def update_drawings_by_tag(self, tag: str, updates: Dict[str, Any]) -> int:
        return self._engine.update_drawings_by_tag(tag, updates)

    # --- Internal ---
    def _format_time(self) -> str:
        t = self.candle.get("time", "")
        if t:
            try:
                return f"[{pd.to_datetime(t).strftime('%Y-%m-%d %H:%M')}] "
            except Exception:
                pass
        return ""
