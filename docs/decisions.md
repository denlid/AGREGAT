# AGREGAT — Architectural Decisions

## ADR-001: PySide6 over Tkinter / PyQt5

**Context**: Need a full-featured desktop GUI for interactive backtesting with charts.

**Decision**: Use PySide6 (Qt 6 official bindings).

**Rationale**:
- Native OpenGL support for GPU-accelerated charts
- QWebEngine for Monaco editor / AI panel
- Official Qt6 support, LGPL licensing
- QThread for background workers

**Consequence**: Headless engine must be importable without Qt. `engine/core/` has zero Qt imports. Qt lives only in `gui/` and `engine/adapters/`.

---

## ADR-002: Pure-Python Core Engine (no numba/Cython in hot loop)

**Context**: The per-bar simulation loop needs to be fast but maintainable.

**Decision**: Keep `BacktestCore._process_one_candle()` in pure Python. Use NumPy vectorization only where bulk operations exist (TF aggregation, OHLC validation).

**Rationale**: The iterative engine runs one candle at a time — Python overhead per iteration is acceptable (< 100k candles/sec). Numba/Cython would add build complexity.

**Consequence**: Backtest execution has a single authoritative step-by-step path.
Bulk NumPy optimizations are limited to supporting data prep and analytics, not
strategy execution.

---

## ADR-003: Bid Convention for All OHLC Data

**Context**: Chart prices must match broker MT5 conventions.

**Decision**: Every OHLC candle is plotted on the Bid side. Ask = Bid + spread.

**Rationale**:
- Long entry = buy at Ask (Bid + spread) — pay spread
- Long exit = sell at Bid — no spread
- Short entry = sell at Bid — no spread
- Short exit = buy at Ask (Bid + spread) — pay spread
- This matches real broker execution

**Consequence**: SL/TP comparisons on shorts must be adjusted to Ask side. `_check_sl_tp` handles this internally.

---

## ADR-004: Pending Orders Fill at Next Candle Open

**Context**: Strategy calls `buy_at_open()` during current bar — when does it actually execute?

**Decision**: Queue as `PendingOrder`, fill at the open of the **next** bar.

**Rationale**: Prevents look-ahead bias. The strategy sees the close of bar N, decides to enter, and the entry happens at bar N+1 open with realistic slippage/spread.

**Consequence**: One-bar delay between signal detection and entry. Same-bar exits (SL/TP) still happen on the same bar.

---

## ADR-005: Zero-Future Rule via DataView

**Context**: Strategies should never access future data.

**Decision**: `DataView` caps all array access to `current_idx - 1`. The engine position pointer advances before the strategy callback.

**Rationale**: Eliminates the class of bugs where `ctx.data.close[-1]` reads the candle being processed (which includes intra-bar high/low not yet known).

**Consequence**: Strategies that need the current bar's close as entry reference use `ctx.candle` dict, not `ctx.data`.

---

## ADR-006: Lazy Import Architecture

**Context**: `import engine` must be fast for CLI/headless processes.

**Decision**: Use PEP 562 `__getattr__` for heavy modules (Monte Carlo, optimization, report export, risk metrics, trade analytics).

**Rationale**: Cuts startup time for CLI and headless tests by ~60%.

**Consequence**: All lazy modules must be listed in `_LAZY_IMPORTS` dict in `engine/__init__.py`.

---

## ADR-007: Dual Strategy API (Declarative + Class-based)

**Context**: Users need a simple API for quick strategies, but power users need full control.

**Decision**: Provide both:
1. `engine.api.Strategy` — decorator-based, auto-parameter discovery, auto-builds class
2. `engine.strategy.context.Strategy` — class-based with full `PARAMS` control

**Rationale**: Lowers barrier for non-programmers while keeping extensibility.

**Consequence**: `StrategyLoader` tries declarative API first, then falls back to class-based.

---

## ADR-008: .agbk ZIP-Based Persistence

**Context**: Sessions must be saved and replayed deterministically.

**Decision**: Use ZIP archives containing `meta.json`, `equity.csv`, `trades.csv`.

**Rationale**:
- ZIP is universally readable
- JSON metadata is human-editable for debugging
- CSV equity curve is diff-able in git
- SHA-256 integrity checks prevent silent corruption

**Consequence**: `agregat replay` CLI can verify bit-identical equity curves.

---

## ADR-009: Worst-Case SL/TP Collision

**Context**: Both SL and TP may be touched in the same bar without tick data.

**Decision**: Assume SL hit before TP (conservative). SL fires, TP ignored.

**Rationale**: Safety-first. A live trader would always prefer a stop-out scenario over a TP-miss scenario for risk modeling.

**Consequence**: Win rate may be slightly underestimated; max adverse scenarios are not hidden.


---

## ADR-010: Risk-Based Lot Sizing with Strict Mode

**Context**: Position sizing must be deterministic and risk-aware.

**Decision**: `calculate_lot(sl_points, risk_pct)` sizes lot as `(equity * risk_pct%) / (sl_points * point_value)`. Clamped to equity-scaled `max_lots_per_100k`. `strict=True` cancels trade on cap violation instead of silent downsizing.

**Rationale**: Transparent position sizing. Strict mode matches institutional systems where undersized trades are considered strategy violations.

**Consequence**: Skipped trades are tracked in `engine.skipped_trades` for reporting.

---

## ADR-011: No asyncio

**Context**: Python asyncio is standard for async I/O.

**Decision**: Not used anywhere in the codebase.

**Rationale**: All I/O is file-based (data loading, persistence) or Qt-signal-based (GUI updates). There is no network streaming, WebSocket feed, or multi-service orchestration that would benefit from async.

**Consequence**: Workers use `QThread` (GUI) or synchronous execution (CLI/tests).
