"""
Simplified strategy API — clean, intuitive, no boilerplate.

Actions → strategy.xxx()   (long, short, log, close, cancel, ...)
Data    → c.xxx            (c.close, c.high, c.close_arr, c.close_htf, ...)

Usage:
    from engine.api import Strategy
    from engine.core.indicators import ema

    strategy = Strategy(name="EMA Crossover", fast=12, slow=26, risk_pct=1.0)

    @strategy.on_candle()
    def _(c):
        if crossover(ema(c.close_arr, strategy.fast), ema(c.close_arr, strategy.slow)):
            strategy.long(sl=c.close - 50, tp=c.close + 100, risk=strategy.risk_pct)

    @strategy.on_15m_close()
    def _(c):
        if c.close_htf > c.close_htf_arr[-2]:
            strategy.log("HTF higher high")
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np

from engine.strategy.context import Strategy as _BaseStrategy, Param
from engine.core.indicators import *  # noqa: F401, F403 — re-export all indicators

# ---------------------------------------------------------------------------
# Internal: current engine ctx per-candle (set by wrapper, read by context obj)
# ---------------------------------------------------------------------------
_current_ctx: Any = None


def _set_ctx(ctx: Any) -> None:
    global _current_ctx
    _current_ctx = ctx


def _clear_ctx() -> None:
    global _current_ctx
    _current_ctx = None


# ---------------------------------------------------------------------------
# Context object — passed as `c` to every callback
# All values are PLAIN types (float, datetime, np.ndarray) — no lazy objects.
# ---------------------------------------------------------------------------
class _Context:
    """Strategy context — available as ``c`` inside callbacks.
    
    Scalars (plain float / datetime):
        c.close          float — last 1m close
        c.high           float — last 1m high
        c.low            float — last 1m low
        c.open_          float — last 1m open
        c.volume         float — last 1m volume
        c.time           datetime — current 1m bar timestamp
    
    Arrays (np.ndarray — pass to indicators):
        c.close_arr      np.ndarray — all 1m closes
        c.high_arr       np.ndarray — all 1m highs
        c.low_arr        np.ndarray — all 1m lows
        c.volume_arr     np.ndarray — all 1m volumes
        c.time_arr       np.ndarray — all 1m timestamps
    
    HTF (inside on_15m_close / on_1h_close / on_1d_close):
        c.close_htf      float — last HTF close
        c.high_htf       float — last HTF high
        c.low_htf        float — last HTF low
        c.open_htf       float — last HTF open
        c.close_htf_arr  np.ndarray — all HTF closes
        c.high_htf_arr   np.ndarray — all HTF highs
        c.low_htf_arr    np.ndarray — all HTF lows
    
    State:
        c.position       Position or None
        c.equity         float
        c.asset          AssetConfig
    """

    def __init__(self, engine_ctx: Any, htf_tf: Optional[str] = None) -> None:
        self._ctx = engine_ctx
        self._htf_tf = htf_tf

    # ── 1m data (scalars) ──
    @property
    def close(self) -> float:
        return float(self._ctx.data.close[-1])

    @property
    def high(self) -> float:
        return float(self._ctx.data.high[-1])

    @property
    def low(self) -> float:
        return float(self._ctx.data.low[-1])

    @property
    def open_(self) -> float:
        return float(self._ctx.data.open[-1])

    @property
    def volume(self) -> float:
        return float(self._ctx.data.volume[-1])

    @property
    def time(self) -> Any:
        return self._ctx.candle.get("time")

    # ── 1m arrays ──
    @property
    def close_arr(self) -> np.ndarray:
        return self._ctx.data.close

    @property
    def high_arr(self) -> np.ndarray:
        return self._ctx.data.high

    @property
    def low_arr(self) -> np.ndarray:
        return self._ctx.data.low

    @property
    def volume_arr(self) -> np.ndarray:
        return self._ctx.data.volume

    @property
    def open_arr(self) -> np.ndarray:
        return self._ctx.data.open

    @property
    def time_arr(self) -> np.ndarray:
        return self._ctx.data.time

    @property
    def candle(self) -> Dict[str, Any]:
        """Current 1m bar as dict: {time, open, high, low, close, volume}."""
        return self._ctx.candle

    # ── HTF data ──
    def _htf_view(self):
        if self._htf_tf is None:
            return None
        return self._ctx.get_tf(self._htf_tf)

    @property
    def close_htf(self) -> float:
        v = self._htf_view()
        if v is None or len(v) == 0:
            return 0.0
        return float(v.close[-1])

    @property
    def high_htf(self) -> float:
        v = self._htf_view()
        if v is None or len(v) == 0:
            return 0.0
        return float(v.high[-1])

    @property
    def low_htf(self) -> float:
        v = self._htf_view()
        if v is None or len(v) == 0:
            return 0.0
        return float(v.low[-1])

    @property
    def open_htf(self) -> float:
        v = self._htf_view()
        if v is None or len(v) == 0:
            return 0.0
        return float(v.open[-1])

    @property
    def close_htf_arr(self) -> np.ndarray:
        v = self._htf_view()
        if v is None:
            return np.array([], dtype=np.float64)
        return v.close

    @property
    def high_htf_arr(self) -> np.ndarray:
        v = self._htf_view()
        if v is None:
            return np.array([], dtype=np.float64)
        return v.high

    @property
    def low_htf_arr(self) -> np.ndarray:
        v = self._htf_view()
        if v is None:
            return np.array([], dtype=np.float64)
        return v.low

    # ── State ──
    @property
    def position(self) -> Any:
        return self._ctx.position

    @property
    def equity(self) -> float:
        return float(self._ctx.equity)

    @property
    def asset(self) -> Any:
        return self._ctx.asset


# ---------------------------------------------------------------------------
# Crossover / Crossunder (standalone functions, NaN-safe)
# ---------------------------------------------------------------------------
def _to_level(level: Any) -> float:
    """If level is an array, extract its last value."""
    if hasattr(level, '__getitem__') and hasattr(level, 'shape'):
        return float(level[-1])
    return float(level)


def crossover(series: np.ndarray, level: Any) -> bool:
    """True if series crossed ABOVE level on the current bar.
    
    ``level`` can be a float or an array (last value is used automatically).
    """
    lv = _to_level(level)
    if not np.isfinite(series[-1]) or not np.isfinite(series[-2]):
        return False
    return bool(series[-2] <= lv < series[-1])


def crossunder(series: np.ndarray, level: Any) -> bool:
    """True if series crossed BELOW level on the current bar.
    
    ``level`` can be a float or an array (last value is used automatically).
    """
    lv = _to_level(level)
    if not np.isfinite(series[-1]) or not np.isfinite(series[-2]):
        return False
    return bool(series[-2] >= lv > series[-1])


# ===========================================================================
# Public API: Strategy class
# ===========================================================================
class Strategy:
    """Declarative strategy object.
    
    Actions (via ``strategy.xxx()``):
        strategy.long(sl, tp, risk, tag)     — queue LONG at next candle open
        strategy.short(sl, tp, risk, tag)    — queue SHORT at next candle open
        strategy.log(message)                 — log to console
        strategy.close(price, reason)         — close position
        strategy.cancel()                     — cancel pending order
        strategy.update_sl(new_sl)            — move stop loss
        strategy.update_tp(new_tp)            — move take profit
        strategy.move_sl_to_be(offset)        — SL to break-even
        strategy.draw_line(...)               — drawing
        strategy.draw_rect(...)
        strategy.draw_text(...)
    
    Data (via ``c`` argument in callbacks):
        c.close, c.high, c.low, c.open_, c.volume, c.time
        c.close_arr, c.high_arr
        c.close_htf, c.high_htf (inside on_15m_close etc.)
        c.position, c.equity, c.asset
    
    Usage::
    
        from engine.api import Strategy
        from engine.core.indicators import ema
        
        strategy = Strategy(name="EMA Crossover", fast=12, slow=26)
        
        @strategy.on_candle()
        def _(c):
            if crossover(ema(c.close_arr, strategy.fast), ema(c.close_arr, strategy.slow)):
                strategy.long(sl=c.close - 50, tp=c.close + 100)
    """

    def __init__(self, name: str, **params: Any) -> None:
        self._name = name
        self._params_def: Dict[str, Dict[str, Any]] = {}
        self._callbacks: Dict[str, Callable] = {}
        self._htf_callbacks: Dict[str, Callable] = {}

        for pname, pvalue in params.items():
            if isinstance(pvalue, Param):
                self._params_def[pname] = {
                    "label": pvalue.label,
                    "kind": pvalue.kind,
                    "default": pvalue.default,
                    "min": pvalue.min,
                    "max": pvalue.max,
                    "step": pvalue.step,
                    "choices": pvalue.choices,
                    "tooltip": pvalue.tooltip,
                }
                setattr(self, pname, pvalue.default)
            else:
                kind = type(pvalue).__name__
                if kind == "int":
                    kind = "int"
                elif kind == "float":
                    kind = "float"
                elif kind == "bool":
                    kind = "bool"
                else:
                    kind = "str"
                self._params_def[pname] = {
                    "label": pname.replace("_", " ").title(),
                    "kind": kind,
                    "default": pvalue,
                    "min": None,
                    "max": None,
                    "step": None,
                    "choices": None,
                    "tooltip": "",
                }
                setattr(self, pname, pvalue)

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------
    def on_candle(self) -> Callable[[Callable], Callable]:
        """Register the main 1m callback. Receives ``c`` (context)."""
        def wrapper(func: Callable) -> Callable:
            self._callbacks["on_candle"] = func
            return func
        return wrapper

    def on_15m_close(self) -> Callable[[Callable], Callable]:
        """Register 15m close callback. ``c.close_htf`` etc. are available."""
        def wrapper(func: Callable) -> Callable:
            self._callbacks["on_15m_close"] = func
            self._htf_callbacks["15m"] = func
            return func
        return wrapper

    def on_1h_close(self) -> Callable[[Callable], Callable]:
        """Register 1h close callback."""
        def wrapper(func: Callable) -> Callable:
            self._callbacks["on_1h_close"] = func
            self._htf_callbacks["1h"] = func
            return func
        return wrapper

    def on_1d_close(self) -> Callable[[Callable], Callable]:
        """Register 1d close callback."""
        def wrapper(func: Callable) -> Callable:
            self._callbacks["on_1d_close"] = func
            self._htf_callbacks["1d"] = func
            return func
        return wrapper

    # ------------------------------------------------------------------
    # Actions — accessed as strategy.long(), strategy.log(), etc.
    # ------------------------------------------------------------------
    @staticmethod
    def long(sl: Optional[float] = None,
             tp: Optional[float] = None,
             risk: Optional[float] = None,
             tag: str = "") -> None:
        """Queue a LONG entry at next candle open.
        
        Auto-calculates lot size from risk %. If risk is None, uses
        the engine's global risk_pct setting.
        """
        ctx = _current_ctx
        if ctx is None:
            return
        if ctx.position is not None:
            return
        entry_price = float(ctx.data.close[-1])
        if sl is None:
            sl_points = 50.0
            sl_price = entry_price - sl_points * ctx.asset.tick_size
        else:
            sl_points = abs(entry_price - sl) / ctx.asset.tick_size if ctx.asset.tick_size else 50.0
            sl_price = sl
        if tp is None:
            tp_price = None
        else:
            tp_price = tp
        vol = ctx.calculate_lot(sl_points, risk_pct=risk)
        if vol is None or vol <= 0:
            return
        ctx.buy_at_open(volume=vol, sl=sl_price, tp=tp_price,
                       reason=f"Long {tag}", tag=tag)

    @staticmethod
    def short(sl: Optional[float] = None,
              tp: Optional[float] = None,
              risk: Optional[float] = None,
              tag: str = "") -> None:
        """Queue a SHORT entry at next candle open."""
        ctx = _current_ctx
        if ctx is None:
            return
        if ctx.position is not None:
            return
        entry_price = float(ctx.data.close[-1])
        if sl is None:
            sl_points = 50.0
            sl_price = entry_price + sl_points * ctx.asset.tick_size
        else:
            sl_points = abs(sl - entry_price) / ctx.asset.tick_size if ctx.asset.tick_size else 50.0
            sl_price = sl
        if tp is None:
            tp_price = None
        else:
            tp_price = tp
        vol = ctx.calculate_lot(sl_points, risk_pct=risk)
        if vol is None or vol <= 0:
            return
        ctx.sell_at_open(volume=vol, sl=sl_price, tp=tp_price,
                        reason=f"Short {tag}", tag=tag)

    @staticmethod
    def log(message: str) -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.log(message)

    @staticmethod
    def close(price: Optional[float] = None, reason: str = "MANUAL") -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.close(price=price, reason=reason)

    @staticmethod
    def cancel() -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.cancel_pending()

    @staticmethod
    def update_sl(new_sl: float) -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.update_sl(new_sl)

    @staticmethod
    def update_tp(new_tp: float) -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.update_tp(new_tp)

    @staticmethod
    def move_sl_to_be(offset_points: float = 0.0) -> bool:
        ctx = _current_ctx
        if ctx is not None:
            return ctx.move_sl_to_be(offset_points=offset_points)
        return False

    @staticmethod
    def draw_line(t1: Any, p1: float, t2: Any, p2: float,
                  color: str = "yellow", width: int = 1, tag: str = "") -> int:
        ctx = _current_ctx
        if ctx is not None:
            return ctx.draw_line(
                str(t1), float(p1),
                str(t2), float(p2),
                color=color, width=width, tag=tag,
            )
        return 0

    @staticmethod
    def draw_rect(t1: Any, p1: float, t2: Any, p2: float,
                  color: str = "yellow", fill: bool = True,
                  opacity: float = 0.3, tag: str = "") -> int:
        ctx = _current_ctx
        if ctx is not None:
            return ctx.draw_rect(
                str(t1), float(p1),
                str(t2), float(p2),
                color=color, fill=fill,
                opacity=opacity, tag=tag,
            )
        return 0

    @staticmethod
    def draw_text(t: Any, p: float, text: str,
                  color: str = "white", tag: str = "") -> int:
        ctx = _current_ctx
        if ctx is not None:
            return ctx.draw_text(
                str(t), float(p),
                text, color=color, tag=tag,
            )
        return 0

    @staticmethod
    def remove_drawing(drawing_id: int) -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.remove_drawing(drawing_id)

    @staticmethod
    def update_drawing(drawing_id: int, updates: Dict[str, Any]) -> None:
        ctx = _current_ctx
        if ctx is not None:
            ctx.update_drawing(drawing_id, updates)

    # ------------------------------------------------------------------
    # Compile to engine-compatible Strategy class
    # ------------------------------------------------------------------
    def _build_class(self) -> type:
        """Convert this declarative object into a ``engine.strategy.context.Strategy``
        subclass that the engine can load and run."""
        params: list[Param] = []
        for pname, pdef in self._params_def.items():
            params.append(Param(
                name=pname,
                label=pdef["label"],
                kind=pdef["kind"],
                default=pdef["default"],
                min=pdef.get("min"),
                max=pdef.get("max"),
                step=pdef.get("step"),
                choices=pdef.get("choices"),
                tooltip=pdef.get("tooltip", ""),
            ))

        callbacks = dict(self._callbacks)

        class _CompiledStrategy(_BaseStrategy):
            name = self._name
            PARAMS = params

            def on_candle(self, ctx: Any) -> None:
                if ctx.position:
                    return
                cb = callbacks.get("on_candle")
                if cb is not None:
                    _set_ctx(ctx)
                    try:
                        c = _Context(ctx)
                        cb(c)
                    finally:
                        _clear_ctx()

        # Dynamically add HTF hooks
        for tf_name in ("15m", "1h", "1d"):
            cb_key = f"on_{tf_name}_close"
            cb_func = callbacks.get(cb_key)
            if cb_func is not None:
                tf = tf_name

                def make_htf_method(t: str, fn: Callable) -> Callable:
                    def htf_method(self: Any, ctx: Any) -> None:
                        _set_ctx(ctx)
                        try:
                            c = _Context(ctx, htf_tf=t)
                            fn(c)
                        finally:
                            _clear_ctx()
                    return htf_method

                setattr(_CompiledStrategy, cb_key, make_htf_method(tf, cb_func))

        return _CompiledStrategy


# ===========================================================================
# Public exports
# ===========================================================================
__all__ = [
    "Strategy",
    "crossover",
    "crossunder",
    # All indicators re-exported via `from engine.core.indicators import *`
]