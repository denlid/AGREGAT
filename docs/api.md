# AGREGAT Strategy Authoring Guide for AI

## Goal
Generate **valid AGREGAT strategies** that run without errors.

**Two clear rules:**
- **Actions** → `strategy.xxx()` (long, short, log, close, cancel, ...)
- **Data** → `c.xxx` (c.close, c.high, c.close_arr, c.close_htf, ...)

Every strategy is a plain Python file. No classes. No `ctx`. No boilerplate.

---

## Minimal strategy (10 lines)

```python
from engine.api import Strategy, crossover
from engine.core.indicators import ema

strategy = Strategy(name="EMA Crossover", fast=12, slow=26, risk_pct=1.0)

@strategy.on_candle()
def _(c):
    if crossover(ema(c.close_arr, strategy.fast), ema(c.close_arr, strategy.slow)):
        strategy.long(sl=c.close - 50, tp=c.close + 100, risk=strategy.risk_pct)
```

---

## AI output template

When generating a new strategy, use this shape unless the user explicitly asks
for the legacy class-based API:

```python
from engine.api import Strategy, Param, crossover, crossunder
from engine.core.indicators import ema, sma, atr

strategy = Strategy(
    name="My Strategy",
    period=Param(name="period", label="Period", kind="int", default=20, min=2, max=400, step=1),
    risk_pct=Param(name="risk_pct", label="Risk %", kind="float", default=1.0, min=0.1, max=10.0, step=0.1),
    sl_points=Param(name="sl_points", label="SL Points", kind="float", default=50.0, min=1.0, max=10000.0, step=1.0),
    tp_points=Param(name="tp_points", label="TP Points", kind="float", default=100.0, min=1.0, max=10000.0, step=1.0),
)

@strategy.on_candle()
def _(c):
    if len(c.close_arr) < strategy.period + 2:
        return

    # Use arrays for indicators.
    fast = ema(c.close_arr, strategy.period)

    # Entry SL/TP are absolute prices.
    sl = c.close - strategy.sl_points * c.asset.tick_size
    tp = c.close + strategy.tp_points * c.asset.tick_size
    strategy.long(sl=sl, tp=tp, risk=strategy.risk_pct, tag="LONG")
```

Before returning code, verify:
- imports use `engine.api`, not `engine.strategy.context`;
- there is no `class`, no `self`, no `ctx`;
- every indicator receives an array ending in `_arr`;
- every `[-2]` access has a length guard;
- `strategy.long()` / `strategy.short()` receive absolute SL/TP prices.

---

## Strategy object — parameters

```python
from engine.api import Strategy

# Auto-detected from keyword arguments:
strategy = Strategy(name="My Strategy",
                    period=20,           # → int parameter
                    risk_pct=1.0,        # → float parameter
                    use_sl=True)         # → bool parameter

# Access inside callbacks:
strategy.period     # → 20 (already int, no casting)
strategy.risk_pct   # → 1.0 (already float)
```

With explicit min/max/label (use `Param` for GUI controls):
```python
from engine.api import Strategy, Param

strategy = Strategy(
    name="My Strategy",
    period=Param(name="period", label="Period", kind="int", default=20, min=5, max=100),
    risk_pct=Param(name="risk_pct", label="Risk %", kind="float", default=1.0, min=0.1, max=5.0),
    use_filter=Param(name="use_filter", label="Use filter", kind="bool", default=True)
)
```

**Param syntax (all arguments must be keywords):**
```python
Param(name="param_name", label="Label", kind="int|float|bool|choice", default=value, min=..., max=..., step=..., choices=...)
```
- `name`: parameter name (must match the keyword in Strategy constructor)
- `label`: text shown in GUI
- `kind`: `"int"`, `"float"`, `"bool"`, or `"choice"`
- `default`: initial value
- `min`, `max`: numeric bounds (for int/float)
- `step`: increment for spinbox (optional)
- `choices`: list of strings (for "choice" kind)

**Important:** All `Param` arguments must be passed as keywords (e.g., `name=`, `label=`, `kind=`, `default=`).

---

## Callbacks

```python
@strategy.on_candle()
def _(c):
    """Called on every 1m bar. Main entry point."""
    ...

@strategy.on_15m_close()
def _(c):
    """Called when 15m candle completes. HTF data available."""
    ...

@strategy.on_1h_close()
def _(c):
    """Called when 1h candle completes."""
    ...

@strategy.on_1d_close()
def _(c):
    """Called when daily candle completes."""
    ...
```

Each callback receives `c` — the **context object**. All data comes through `c`.

---

## Context `c` — all data

### Scalars (plain Python types — no lazy objects!)

```python
c.close       # float — last 1m close
c.high        # float — last 1m high
c.low         # float — last 1m low
c.open_       # float — last 1m open   (open_ because "open" is a Python builtin)
c.volume      # float — last 1m volume
c.time        # datetime — current bar timestamp
c.candle      # dict — current 1m bar as dict: {time, open, high, low, close, volume}
```

### Arrays (np.ndarray — pass to indicators)

```python
c.close_arr       # np.ndarray — all 1m closes
c.high_arr        # np.ndarray — all 1m highs
c.low_arr         # np.ndarray — all 1m lows
c.open_arr        # np.ndarray — all 1m opens
c.volume_arr      # np.ndarray — all 1m volumes
c.time_arr        # np.ndarray — all 1m timestamps

# Access previous bars:
c.close_arr[-1]   # last close (same as c.close)
c.close_arr[-2]   # previous close
c.close_arr[-5:]  # last 5 closes

# Pass to indicators:
ema(c.close_arr, 20)
rsi(c.close_arr, 14)
```

### HTF data (inside on_15m_close / on_1h_close / on_1d_close)

```python
c.close_htf       # float — last HTF close
c.high_htf        # float — last HTF high
c.low_htf         # float — last HTF low
c.open_htf        # float — last HTF open

c.close_htf_arr   # np.ndarray — all HTF closes
c.high_htf_arr    # np.ndarray — all HTF highs
c.low_htf_arr     # np.ndarray — all HTF lows
c.open_htf_arr    # np.ndarray — all HTF opens (if needed)

# Previous values:
c.close_htf_arr[-2]    # previous HTF close
c.high_htf_arr[-5:]    # last 5 HTF highs
```

**Note:** HTF callbacks (`on_15m_close`, `on_1h_close`, `on_1d_close`) are called **before** the main `on_candle()` callback on the same 1m bar when an HTF candle completes.

### State

```python
c.position       # Position or None
c.equity         # float — current equity
c.asset          # AssetConfig
```

---

## Actions — via `strategy.xxx()`

### Entry

```python
strategy.long(sl=95.0, tp=105.0, risk=1.0, tag="")
strategy.short(sl=105.0, tp=95.0, risk=1.0, tag="")
```

**Automatically:**
- Checks no open position
- Calculates lot via `calculate_lot()`
- Calls `buy_at_open()` / `sell_at_open()`
- If risk is None, uses global setting

```python
# Minimal (auto SL = 50 points):
strategy.long(risk=1.0)

# Full:
strategy.long(sl=1050.0, tp=1100.0, risk=2.0, tag="LONG_1")
```

### Position management

```python
strategy.log("Message")              # log to console
strategy.close()                     # close position
strategy.close(price=100.0, reason="TARGET")
strategy.cancel()                    # cancel pending order
strategy.update_sl(97.0)             # move stop loss
strategy.update_tp(110.0)            # move take profit
strategy.move_sl_to_be()             # SL to break-even (handles long/short)
strategy.move_sl_to_be(offset_points=2.0)
```

### Drawing (text is centered on the candle)

```python
line_id = strategy.draw_line(t1, p1, t2, p2, color="yellow", width=1, tag="levels")
rect_id = strategy.draw_rect(t1, p1, t2, p2, color="yellow", fill=True, opacity=0.3, tag="fvg")
text_id = strategy.draw_text(t, p, "Label", color="white", tag="labels")

# Single drawing operations use the returned id.
strategy.remove_drawing(line_id)
strategy.update_drawing(rect_id, {"color": "red"})

# Batch operations use tag as a layer/group label.
strategy.remove_drawings_by_tag("fvg")
strategy.update_drawings_by_tag("labels", {"color": "white"})

# tag is a group/layer label, not a unique drawing name.
```

t/t1/t2 are timestamps — use `c.time`, `c.time_arr[-2]`, etc.

---

## Functions — `crossover` / `crossunder`

```python
crossover(series, level)     # → bool — crossed ABOVE level
crossunder(series, level)    # → bool — crossed BELOW level
```

**Automatically handle NaN.** No need for `np.isfinite()`.

```python
if crossover(ema(c.close_arr, 12), ema(c.close_arr, 26)):
    strategy.long(...)

if crossover(c.close_htf_arr, sma(c.close_htf_arr, 20)):
    strategy.log("HTF bullish")
```

---

## Indicators

```python
from engine.core.indicators import sma, ema, atr, rsi, macd, donchian_channel

sma(c.close_arr, 20)                      # → np.ndarray, first 19 values NaN
ema(c.close_arr, 20)                      # → np.ndarray
atr(c.high_arr, c.low_arr, c.close_arr, 14)
rsi(c.close_arr, 14)
macd(c.close_arr, 12, 26, 9)              # → (macd_line, signal_line, histogram)
donchian_channel(c.high_arr, c.low_arr, 20)  # → (upper, lower)
```

First `period - 1` values are NaN. `crossover()`/`crossunder()` handle this automatically.

---

## Position object

```python
pos = c.position
if pos is not None:
    pos.direction       # "long" or "short"
    pos.entry_price     # float
    pos.volume          # float — lot size
    pos.sl              # float or None — current SL
    pos.tp              # float or None — current TP
    pos.pnl             # float — current PnL
    pos.bars_held       # int — bars since entry
    pos.tag             # str — tag set at entry
```

---

## Full example

```python
from engine.api import Strategy, crossover, crossunder
from engine.core.indicators import donchian_channel as donchian


strategy = Strategy(name="Donchian Breakout",
                    period=20,
                    risk=1.0,
                    rr=2.0)


@strategy.on_candle()
def _(c):
    upper, lower = donchian(c.high_arr, c.low_arr, strategy.period)

    if crossover(c.close, upper):
        strategy.long(sl=c.close - 50, tp=c.close + 50 * strategy.rr,
                      risk=strategy.risk)

    if crossunder(c.close, lower):
        strategy.short(sl=c.close + 50, tp=c.close - 50 * strategy.rr,
                       risk=strategy.risk)


@strategy.on_15m_close()
def _(c):
    if c.close_htf > c.close_htf_arr[-2]:
        strategy.log("HTF bullish continuation")
```

---

## Quick reference

| What | How |
|------|------|
| LONG entry | `strategy.long(sl=..., tp=..., risk=...)` |
| SHORT entry | `strategy.short(sl=..., tp=..., risk=...)` |
| Log | `strategy.log("...")` |
| Close position | `strategy.close()` |
| Cancel order | `strategy.cancel()` |
| Move SL/TP | `strategy.update_sl(97)`, `strategy.update_tp(110)` |
| SL to breakeven | `strategy.move_sl_to_be()` |
| Drawing by id | `strategy.update_drawing(id, updates)`, `strategy.remove_drawing(id)` |
| Drawings by tag | `strategy.update_drawings_by_tag(tag, updates)`, `strategy.remove_drawings_by_tag(tag)` |
| Last 1m close | `c.close` (float) |
| Last 1m high | `c.high` (float) |
| Previous close | `c.close_arr[-2]` |
| Array of closes | `c.close_arr` (np.ndarray) |
| Last HTF close | `c.close_htf` (float — inside on_15m_close) |
| HTF array | `c.close_htf_arr` (np.ndarray) |
| Bar timestamp | `c.time` (datetime), `c.time_arr[-2]` (previous) |
| Crossover | `crossover(ema(c.close_arr, 12), ema(c.close_arr, 26))` |
| Position | `c.position` |
| Equity | `c.equity` |
| SMA, EMA, RSI, ATR | `sma(c.close_arr, 20)`, `ema(c.close_arr, 20)` |
| Param access | `strategy.period` (auto-cast) |

---

## Runtime caveats

- Compiled `@strategy.on_candle()` callbacks are entry callbacks. They are not
  called while a position is already open.
- Use `on_candle` to detect and queue new entries. Do not rely on it for
  trailing stops, manual exits, or other per-bar open-position management.
- Management actions such as `strategy.close()`, `strategy.update_sl()`,
  `strategy.update_tp()`, and `strategy.move_sl_to_be()` are available only when
  the callback itself is invoked. In declarative `engine.api` strategies, use
  HTF callbacks (`on_15m_close`, `on_1h_close`, `on_1d_close`) for these actions,
  or use the class-based `engine.strategy.context.Strategy` API when management
  must run every bar while a position is open.

---

## Common invalid patterns

Do not generate these:

```python
# Wrong for the declarative API: class-based strategy.
from engine.strategy.context import Strategy

class MyStrategy(Strategy):
    ...
```

```python
# Wrong: declarative callbacks receive `c`, not `ctx`.
@strategy.on_candle()
def _(ctx):
    strategy.long(sl=ctx.close - 50)
```

```python
# Wrong: indicators need arrays, not scalar floats.
ema(c.close, 20)
```

```python
# Wrong: SL/TP passed to entry actions are absolute prices, not point distances.
strategy.long(sl=50, tp=100)
```

```python
# Wrong: unsafe previous-bar access without a length guard.
if c.close_arr[-1] > c.close_arr[-2]:
    strategy.long(...)
```

---

## Hard rules

1. **Actions → `strategy.xxx()`** — `strategy.long()`, `strategy.log()`, not global functions
2. **Data → `c.xxx`** — `c.close`, `c.high`, `c.close_arr`
3. **Arrays end with `_arr`** — `c.close_arr`, NOT `c.close.arr`
4. **SL/TP are absolute prices** — NOT distances, NOT percentages
5. **Points ≠ price** — `calculate_lot` takes points, `update_sl` takes price
6. **One position at a time** — `strategy.long()`/`strategy.short()` check automatically
7. **No hardcoded periods** — use `strategy.period` instead of literal numbers
8. **Always use `_arr` for indicators** — `sma(c.close_arr, 20)`, not `sma(c.close, 20)`
