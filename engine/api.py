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
# Internal: current engine ctx and active compiled strategy instance
# ---------------------------------------------------------------------------
_current_ctx: Any = None
_current_strategy_instance: Any = None


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
        c.volume_htf     float — last HTF volume
        c.time_htf       datetime — last HTF timestamp
        c.close_htf_arr  np.ndarray — all HTF closes
        c.high_htf_arr   np.ndarray — all HTF highs
        c.low_htf_arr    np.ndarray — all HTF lows
        c.open_htf_arr   np.ndarray — all HTF opens
        c.volume_htf_arr np.ndarray — all HTF volumes
        c.time_htf_arr   np.ndarray — all HTF timestamps
    
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
        engine = self._ctx._engine
        return float(engine._close[engine.current_idx])

    @property
    def high(self) -> float:
        engine = self._ctx._engine
        return float(engine._high[engine.current_idx])

    @property
    def low(self) -> float:
        engine = self._ctx._engine
        return float(engine._low[engine.current_idx])

    @property
    def open_(self) -> float:
        engine = self._ctx._engine
        return float(engine._open[engine.current_idx])

    @property
    def volume(self) -> float:
        engine = self._ctx._engine
        return float(engine._volume[engine.current_idx])

    @property
    def time(self) -> Any:
        engine = self._ctx._engine
        return engine._time[engine.current_idx]

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
    def volume_htf(self) -> float:
        v = self._htf_view()
        if v is None or len(v) == 0:
            return 0.0
        return float(v.volume[-1])

    @property
    def time_htf(self) -> Any:
        v = self._htf_view()
        if v is None or len(v) == 0:
            return None
        return v.time[-1]

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

    @property
    def open_htf_arr(self) -> np.ndarray:
        v = self._htf_view()
        if v is None:
            return np.array([], dtype=np.float64)
        return v.open

    @property
    def volume_htf_arr(self) -> np.ndarray:
        v = self._htf_view()
        if v is None:
            return np.array([], dtype=np.float64)
        return v.volume

    @property
    def time_htf_arr(self) -> np.ndarray:
        v = self._htf_view()
        if v is None:
            return np.array([], dtype="datetime64[ns]")
        return v.time

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
        strategy.remove_drawing(id)           — remove one drawing by id
        strategy.update_drawing(id, updates)  — update one drawing by id
        strategy.remove_drawings_by_tag(tag)  — remove all drawings in a tag group
        strategy.update_drawings_by_tag(...)  — update all drawings in a tag group
    
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

    def _save_initial_state(self) -> None:
        """Capture the initial state of the declarative strategy after loading."""
        self._initial_keys = set(self.__dict__.keys())
        self._initial_values = {}
        for k in self._initial_keys:
            if k.startswith('_'):
                continue
            v = getattr(self, k)
            if isinstance(v, (int, float, bool, str, type(None))):
                self._initial_values[k] = v
            elif isinstance(v, list):
                self._initial_values[k] = list(v)
            elif isinstance(v, dict):
                self._initial_values[k] = dict(v)
            elif isinstance(v, set):
                self._initial_values[k] = set(v)
            else:
                self._initial_values[k] = v

    def reset(self) -> None:
        """Reset the declarative strategy by deleting all custom runtime variables
        and restoring simple variables to their initial state."""
        if not hasattr(self, '_initial_keys'):
            return
        # Delete any custom attributes added dynamically during the backtest
        for key in list(self.__dict__.keys()):
            if key.startswith('_'):
                continue
            if key not in self._initial_keys:
                try:
                    delattr(self, key)
                except AttributeError:
                    pass
        # Restore initial values
        for key, val in self._initial_values.items():
            if isinstance(val, list):
                setattr(self, key, list(val))
            elif isinstance(val, dict):
                setattr(self, key, dict(val))
            elif isinstance(val, set):
                setattr(self, key, set(val))
            else:
                setattr(self, key, val)

    def __getattr__(self, name: str) -> Any:
        # Route parameter access to current running compiled strategy instance if active
        if name in self._params_def:
            from engine.api import _current_strategy_instance
            if _current_strategy_instance is not None:
                return _current_strategy_instance.params.get(name, self._params_def[name]["default"])
            return self._params_def[name]["default"]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __dir__(self) -> list[str]:
        return sorted(super().__dir__() + list(self._params_def.keys()))

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
                t1, float(p1),
                t2, float(p2),
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
                t1, float(p1),
                t2, float(p2),
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
                t, float(p),
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

    @staticmethod
    def remove_drawings_by_tag(tag: str) -> int:
        ctx = _current_ctx
        if ctx is not None:
            return ctx.remove_drawings_by_tag(tag)
        return 0

    @staticmethod
    def update_drawings_by_tag(tag: str, updates: Dict[str, Any]) -> int:
        ctx = _current_ctx
        if ctx is not None:
            return ctx.update_drawings_by_tag(tag, updates)
        return 0

    # ------------------------------------------------------------------
    # Compile to engine-compatible Strategy class
    # ------------------------------------------------------------------
    def _build_class(self) -> type:
        """Convert this declarative object into a ``engine.strategy.context.Strategy``
        subclass that the engine can load and run."""
        self._save_initial_state()
        declarative_self = self
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
            name = declarative_self._name
            PARAMS = params
            _declarative = declarative_self

            def __init__(self) -> None:
                super().__init__()
                declarative_self.reset()

            def on_candle(self, ctx: Any) -> None:
                if ctx.position:
                    return
                cb = callbacks.get("on_candle")
                if cb is not None:
                    _set_ctx(ctx)
                    global _current_strategy_instance
                    _current_strategy_instance = self
                    try:
                        c = _Context(ctx)
                        cb(c)
                    finally:
                        _current_strategy_instance = None
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
                        global _current_strategy_instance
                        _current_strategy_instance = self
                        try:
                            c = _Context(ctx, htf_tf=t)
                            fn(c)
                        finally:
                            _current_strategy_instance = None
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
