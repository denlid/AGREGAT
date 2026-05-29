# AGREGAT — Coding Conventions & Architecture Rules

## Naming & Style

- **Python**: snake_case for functions/methods/variables, PascalCase for classes
- **Private attributes**: `_leading_underscore` (single). `__name_mangling` NOT used
- **Constants**: `UPPER_SNAKE`
- **Type hints**: All public APIs typed. Internal may skip but prefer annotations
- **Docstrings**: NumPy-style docstrings (`"""Summary.\n\nFields\n------\nname ..."""`) used in `engine/strategy/context.py` and data models
- **Comments**: Russian/Ukrainian in UI code (e.g. `app.py` activation dialog), English everywhere else

## Code Quality

- **F-strings** preferred over `.format()` or `%`
- **Lazy imports** for heavy modules: use `__getattr__` + `importlib.import_module` pattern (see `engine/__init__.py`)
- **No sandbox**: StrategyLoader uses raw `importlib`. Only load trusted strategies
- **Logging**: Use `logging.getLogger("StrategyTester.XXX.YYY")` hierarchy. Root logger name is `"StrategyTester"`
- **Exception handling**: GUI callbacks never abort engine — `_fire()` catches and logs all exceptions

## Async / Concurrency

- **No asyncio** anywhere in the codebase. All operations are synchronous
- **Threading**: GUI uses `QThread` via workers in `gui/workers/` (AI worker, metrics worker)
- **GPU rendering**: Synchronous OpenGL via QOpenGLWidget (pyqtgraph), not CUDA/OpenCL

## Engine Conventions

### Data Access

- **Zero-Future Rule**: Strategy code must only access `ctx.data.close[:-1]`, never `ctx.data.close[-1]`. The engine enforces this via `DataView` which caps visibility to `current_idx - 1`
- **NumPy preferred**: All internal data is `np.ndarray`. Dict-based access (`ctx.candle`) is only for the current bar
- **HTF access**: Via `ctx.get_tf(tf)` which returns `HTFView` — a NumPy-like object. Legacy `get_tf_dicts(tf)` returns `list[dict]`

### Order & Execution

- **Pending orders fill at next candle's open** (not current). Engine queues via `PendingOrder` and fills in `_fill_pending()` on next `_process_one_candle` call
- **Same-bar SL/TP**: Worst-case assumption — SL hit before TP. Both cannot fire in same bar
- **Bid model**: Chart = Bid. Long entry = Ask (Bid + spread). Long exit = Bid. Short entry = Bid. Short exit = Ask (Bid + spread)
- **Stop gaps**: If bar opens beyond SL, fill at open price + adverse slippage, reason = `"GAP_SL"`
- **SL/TP slippage**: Applied in adverse direction on stop fills. Non-stop closes (MANUAL/EOD/WEEKEND) use `adjust_close_price()`

### Risk Management

- **Lot sizing**: `calculate_lot(sl_points, risk_pct)` — risk % of equity divided by SL distance. Clamped to `max_lots_per_100k * (equity / 100k)` and absolute max lots (default 100)
- **Strict mode**: When `strict=True`, lot exceeding caps cancels trade instead of clamping
- **Skipped trades**: Reasons — `INVALID_SL`, `LOT_LIMIT`, `LOT_LIMIT_STRICT`, `ABS_LOT_LIMIT`, `ABS_LOT_LIMIT_STRICT`, `TRADING_HOURS`, `WEEKEND`, `EOD`
- **Lock/BE mechanism**: `move_sl_to_be()` never widens SL. Uses `lock_activated`/`be_activated` flags on Position

## Response / Return Conventions

- **Engine actions return `bool`**: `True` if action executed, `False` if skipped (position exists, pending exists, invalid params)
- **Ctx data properties return plain types**: `float`, `datetime`, `np.ndarray` — never lazy wrappers
- **Drawing methods return `int`**: drawing ID assigned by engine
- **Error handling**: `_fire()` catches all exceptions in callbacks; `_process_one_candle` returns `False` on strategy error

## GUI Conventions

- **MainWindow is thin** — construction delegated to controllers under `gui/controllers/`
- **Controllers reference each other** through `host._ctrl_name` attributes (no delegation boilerplate)
- **Callback lists** on engine (`on_trade_completed`, `on_step_completed`, etc.) — controllers/appenders subscribe. Qt adapter converts them to signals
- **GPU config**: Flips pyqtgraph to OpenGL. Auto-detects availability. Env var `AGREGAT_USE_GPU` (1/0) overrides
- **Themes**: Dark/light. Resolution order: (1) settings.json → (2) Qt ColorScheme → (3) fallback dark

## Persistence Conventions

- `.agbk` = ZIP with `meta.json`, `equity.csv`, `trades.csv`
- Integrity: SHA-256 of payload bytes, verified on load
- Seeds dict stored for deterministic replay

## Patterns to Follow

1. **Pure core**: `engine/core/` has zero Qt imports. Qt lives only in `gui/` and `engine/adapters/`
2. **Testability**: Headless tests use `BacktestCore` directly, not `BacktestEngine` (which pulls Qt)
3. **No global mutable state**: Exception — `engine.api._current_ctx` / `_current_strategy_instance` for declarative API routing
4. **Lazy heavy modules**: Monte Carlo, optimization, report export are all lazy-imported through `engine/__init__.py.__getattr__`