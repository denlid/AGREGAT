# AGREGAT Strategy Authoring Guide for AI

## Goal
Generate **valid AGREGAT strategies** that run without API misuse, respect the **Zero‑Future Rule**, and integrate cleanly with the GUI (params form + persistence).

## Canonical imports
Use the real engine API surface:

- `from engine.strategy.context import Strategy, Param`

Avoid legacy/incorrect imports like `from core.strategy import Strategy`.

## Required structure (minimal)
- One public strategy class that **inherits `Strategy`**
- Class attribute: `name = "..."` (short human readable name)
- Implement at least one execution API:
  - **Visual (step-by-step)**: `on_candle(self, ctx)`
  - **Quant (vectorized)**: `vectorized_signals(self, df)`

### Optional hooks (higher timeframe close)
Strategies may implement methods like:
- `on_15m_close(self, ctx)`
- `on_1h_close(self, ctx)`

These are called right when the HTF bucket completes (before the 1m `on_candle` on the same bar).

## Parameters (GUI + persistence contract)
- Tunables MUST be declared in class-level `PARAMS: list[Param]`
- Runtime values MUST be read from `self.params[name]`
- The GUI builds the Settings → Strategy form from `PARAMS`
- Persisted values are applied via `Strategy.apply_params(overrides)` before backtests

Example:

```python
from engine.strategy.context import Strategy, Param


class MyStrategy(Strategy):
    name = "My Strategy"
    PARAMS = [
        Param("fast", "Fast MA", "int", 20, min=2, max=400, step=1),
        Param("slow", "Slow MA", "int", 50, min=2, max=800, step=1),
        Param("risk_pct", "Risk %", "float", 1.0, min=0.01, max=10.0, step=0.1),
        Param("use_sessions", "Use sessions", "bool", True),
    ]

    def on_candle(self, ctx):
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        # ...
```

## Context API (on_candle)
### Data access (Zero‑Future safe)
- `ctx.candle`: current 1m candle dict (`time/open/high/low/close/volume/...`)
- `ctx.data`: read-only NumPy-backed slices respecting the Zero‑Future Rule
- `ctx.get_tf(tf)`: HTF view (also Zero‑Future safe)

### Position / equity
- `ctx.position`
- `ctx.equity`, `ctx.initial_equity`
- `ctx.asset`

### Entries (queued at next candle open)
- `ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="", tag="")`
- `ctx.sell_at_open(volume=1.0, sl=None, tp=None, reason="", tag="")`
- `ctx.cancel_pending()`

### Position management
- `ctx.close(price=None, reason="MANUAL")`
- `ctx.update_sl(new_sl)`
- `ctx.update_tp(new_tp)`
- `ctx.move_sl_to_be(offset_points=0.0)`

### Risk sizing
- `ctx.calculate_lot(sl_points, risk_pct=None, strict=False)`

### Utility
- `ctx.log(message)`
- `ctx.get_session(time=None)`
- `ctx.is_trading_hours()`

### Drawings / layers
- `ctx.register_layer(tag, label, default_on=True)`
- `ctx.draw_line(...)`, `ctx.draw_rect(...)`, `ctx.draw_text(...)`
- `ctx.remove_drawing(id)`, `ctx.update_drawing(id, updates)`

## Hard rules (do not break)
- **No future leak**: never read bars ahead of current index; no unsafe indexing tricks.
- Keep logic deterministic and backtest-safe.
- Do not call private internals (`ctx._engine`) unless user explicitly asks.
- Validate SL/TP sanity and don’t open a new position if `ctx.position` exists (unless user explicitly wants pyramiding).

## Vectorized signals contract
- Return a `pandas.DataFrame` aligned to input index.
- Include at least:
  - `signal` in `{-1, 0, 1}`
  - `sl_points` (float, points distance)
  - `tp_points` (float, points distance)
  - optional `risk_pct`
- Do not mutate the source dataframe.

## Editing mode (patches)
When modifying existing strategy code, prefer **patches** instead of rewriting the whole file.
Output a single fenced block in Cursor-like diff style:

```agregat_patch
-from core.strategy import Strategy  # Updated import for AGREGAT platform
+from engine.strategy.context import Strategy, Param
@@@
-x = 1
+x = 2
```

Rules:
- Use `-` for removed lines and `+` for added lines.
- Separate independent patch chunks with a line containing exactly `@@@`.
- Removed (`-`) lines must match exact existing code fragments (including indentation).
