# AGREGAT Strategy Authoring Guide for AI

## Goal
Generate valid AGREGAT strategies that run without API misuse.

## Required Structure
- Prefer `from app import Strategy` and subclass `Strategy`.
- Strategy class should define `name = "..."`.
- Implement `on_candle(self, ctx)` for visual/live path.
- Optional: implement `vectorized_signals(self, df)` for quant path.

## Parameters
- Tunables must be declared in class-level `PARAMS` as `Param(...)`.
- Runtime values are in `self.params`.
- Do not invent custom parameter plumbing outside `PARAMS`/`self.params` unless explicitly requested.

## Context API (on_candle)
- Market data:
  - `ctx.candle` current candle dict.
  - `ctx.data` zero-future-safe data view (`open/high/low/close/...` arrays).
  - `ctx.get_tf(tf)` higher timeframe completed bars view.
- Position/account:
  - `ctx.position`, `ctx.equity`, `ctx.initial_equity`, `ctx.asset`.
- Entries:
  - `ctx.buy_at_open(volume, sl=None, tp=None, reason="", tag="")`
  - `ctx.sell_at_open(volume, sl=None, tp=None, reason="", tag="")`
  - `ctx.cancel_pending()`
- Position management:
  - `ctx.close(price=None, reason="MANUAL")`
  - `ctx.update_sl(new_sl)`, `ctx.update_tp(new_tp)`
  - `ctx.move_sl_to_be(offset_points=0.0)`
- Risk:
  - `ctx.calculate_lot(sl_points, risk_pct=None, strict=False)`
- Utility:
  - `ctx.log(message)`, `ctx.get_session()`, `ctx.is_trading_hours()`
- Drawings/layers:
  - `ctx.register_layer(tag, label, default_on=True)`
  - `ctx.draw_line(...)`, `ctx.draw_rect(...)`, `ctx.draw_text(...)`
  - `ctx.remove_drawing(id)`, `ctx.update_drawing(id, updates)`

## Hard Rules
- No future leak: do not read future bars or use unsafe indexing tricks.
- Do not use unsupported APIs or external broker SDKs.
- Do not rely on private engine internals (`ctx._engine`) unless user explicitly asks.
- Keep logic deterministic and backtest-safe.
- Always validate SL/TP distance before sending orders.
- Avoid opening new position if `ctx.position` exists.

## Vectorized Signals Contract
- Return a `pandas.DataFrame` aligned to input index.
- Include at least:
  - `signal` (`-1`, `0`, `1`)
  - `sl_points` (float, points distance)
  - `tp_points` (float, points distance)
  - optional `risk_pct`
- Do not mutate source dataframe.

## Output Quality
- Return complete runnable code.
- Keep imports minimal and valid.
- Preserve requested behavior exactly.
