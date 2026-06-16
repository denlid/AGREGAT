You are fixing an ERROR in an existing AGREGAT trading strategy.

## Task type: FIX ERROR

The user ran a backtest and got this traceback:

```text
{error_traceback}
```

Your job:
1. Identify the root cause.
2. Fix only what is necessary.
3. Output the complete fixed strategy file.

## Canonical API check

Prefer the declarative API:

```python
from engine.api import Strategy, Param, crossover, crossunder

strategy = Strategy(name="My Strategy", ...)

@strategy.on_candle()
def _(c):
    ...
```

If the file already uses the declarative API, keep it declarative. Do not
convert it to a class.

If the file already uses the class-based legacy API, keep it class-based unless
the error is caused by mixing APIs.

## Common AGREGAT strategy errors to fix

- Wrong import: use `from engine.api import Strategy` for declarative files.
- Mixed APIs: `class ... Strategy` plus `@strategy.on_candle()` in one file.
- Using `ctx` inside declarative callbacks; the argument is `c`.
- Using `self.params[...]` in declarative files; use `strategy.param_name`.
- Passing scalar `c.close` to indicators; use `c.close_arr`.
- Missing length guard before `[-2]`, rolling windows, or HTF arrays.
- Invalid SL/TP direction:
  - long: `sl < c.close`, `tp > c.close`
  - short: `sl > c.close`, `tp < c.close`
- Treating SL/TP as distances in `strategy.long()` or `strategy.short()`.
  They must be absolute prices.
- Using `c.position` as the main state in new declarative strategies; prefer
  `c.position_size`, `c.opentrades`, and `c.positions`.
- Managing pyramiding with repeated `strategy.long()` calls when separate
  entry IDs are needed; prefer `strategy.entry(id, direction, ...)` and
  `strategy.exit(id, from_entry=...)`.

## Response format

1. Problem diagnosis, one or two sentences.
2. Complete fixed strategy file in one Python fenced code block.
3. Short note listing the exact fix.
