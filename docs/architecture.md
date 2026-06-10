# AGREGAT — Architecture Overview

## Project Identity

AGREGAT v7.78 — Algorithmic Graphical Reviewer Engine Genius Analytics Tester.  
Institutional-grade quantitative backtesting platform with PySide6 GUI.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.10+ |
| GUI Framework | PySide6 (Qt 6) |
| GPU Charts | PyQtGraph + QOpenGLWidget |
| Data | pandas, NumPy, Parquet / Feather / CSV |
| HTML Reports | Jinja2 + Plotly |
| AI Assistant | WebEngine + Monaco Editor |
| Persistence | ZIP-based `.agbk` archives |
| Security | Hardware-bound license via SHA-256 HWID |

## High-Level Directory Layout

```
AGREGAT/
├── app.py                 # Entry point (license, splash, theme, GPU)
├── engine/                # Headless core (no PySide6 dependency)
│   ├── core/              #   Iterative backtest core
│   ├── strategy/          #   Strategy API (Context, Param, loader)
│   ├── data/              #   Data loading, TF aggregation
│   ├── execution/         #   Bid/Ask model, slippage, vectorized engine
│   ├── models/            #   Data models (Position, Trade, AssetConfig)
│   ├── analytics/         #   Metrics, MC, equity curve, risk
│   ├── persistence/       #   .agbk save/load, integrity checks
│   ├── optimization/      #   Grid/random search, walk-forward
│   ├── report/            #   HTML tearsheet exporter
│   ├── security/          #   License verification
│   ├── adapters/          #   Qt signal bridge
│   ├── observability/     #   Sentry, audit log
│   └── metrics/           #   Advanced metrics (registry, pain, ratios)
├── gui/                   # PySide6 UI
│   ├── main_window.py     #   Orchestrator: engine + tabs + controllers
│   ├── chart_tab/         #   OpenGL-accelerated multi-timeframe chart
│   ├── tabs/              #   Stats, Trades, Analytics, Quant, Settings, Log
│   ├── controllers/       #   Session, replay, settings, theme, toolbar, menu
│   ├── widgets/           #   Bar replay, AI editor, metric cards, sparklines
│   ├── dialogs/           #   Monte Carlo, custom asset, cheat sheet
│   ├── styling/           #   Theme engine (dark/light)
│   ├── components/        #   Toast, animations, quantum spinner
│   ├── workers/           #   AI worker, metrics worker
│   └── persistence/       #   Settings persist, recent files, drawings
├── strategies/            # User strategy .py files
├── tests/                 # Test suite (~90 files)
├── bench/                 # Benchmarking harness
├── config/                # Asset registry, AI prompt system prompts
├── docs/                  # Documentation
└── notebooks/             # Quickstart notebooks
```

## Data Flow — Backtest Execution

```
Data Source (CSV/Parquet/Feather)
       │
       ▼
  DataLoader.load_*(path) ───► pandas.DataFrame (OHLCV + spread)
       │
       ▼
  BacktestCore.load_data(df)
       │  • Binds NumPy columns (O(1), no copy)
       │  • Prebuilds TF aggregator arrays
       │  • Allocates bar-equity buffer (optional)
       ▼
  BacktestCore.load_strategy(strategy)
       │  • StrategyLoader.load(filepath)
       │  • Supports engine.api.Strategy OR class-based Strategy
       ▼
  BacktestCore.step() / step_n()
       │  _process_one_candle(idx):
       │    1. Advance TF aggregator
       │    2. Fill pending order at open
       │    3. Update MAE/MFE, check SL/TP
       │    4. EOD close (if enabled)
       │    5. Weekend close (if enabled)
       │    6. Strategy HTF hooks (15m / 1h / 1d)
       │    7. Strategy main on_candle(ctx)
       │    8. Record bar-level equity (if enabled)
       ▼
  Trade list + equity curve + stats
       │
       ▼
  GUI tabs consume via Qt signals
```

## Two Strategy APIs

1. **Class-based** (`engine.strategy.context.Strategy`)
   - Subclass, override `on_candle(ctx)`
   - Declare `PARAMS = [Param(...), ...]` list
   - Access `self.params[name]` for tunable values

2. **Declarative** (`engine.api.Strategy`)
   - `strategy = Strategy(name="...", param=value, ...)`
   - `@strategy.on_candle()` decorator
   - `c.close`, `c.close_arr`, `c.close_htf` context object
   - Auto-compiled to class-based via `_build_class()`

## Two Engines

| Engine | File | Characteristics |
|--------|------|----------------|
| BacktestCore | `engine/core/backtest_core.py` | Iterative, per-bar step, pure Python, no Qt |
| VectorizedEngine | `engine/execution/vectorized_engine.py` | NumPy array operations, bulk signal evaluation |

The `BacktestEngine` import is a shim (`engine/backtest_engine.py`) that re-exports `QtBacktestAdapter` (the adapter wrapping `BacktestCore` with Qt signals).

## GUI Architecture

```
MainWindow (thin orchestrator)
  ├── engine (BacktestEngine / QtBacktestAdapter)
  ├── ChartTab (OpenGL candles + LOD pyramid + markers)
  ├── StatsTab
  ├── TradesTab
  ├── AnalyticsTab
  ├── QuantTab
  ├── SettingsTab
  ├── LogConsole
  ├── StrategyEditorTab (Monaco editor)
  ├── AIAssistantEditor (WebEngine)
  ├── Controllers:
  │   ├── SessionLifecycleController  — load/save .agbk
  │   ├── ReplayController            — step/step_n/rewind
  │   ├── SettingsController           — settings persistence
  │   ├── ThemeController              — dark/light theme
  │   ├── ToolbarController            — UI toolbar
  │   ├── MenuController               — file/edit menus
  │   ├── ModeController               — engine mode switching
  │   ├── StatusBarController          — status bar
  │   └── AssetController              — asset selection
  └── SharedTradesCache (cross-tab shared data)
```

## Persistence Format

`.agbk` files are ZIP archives containing:
- `meta.json` — strategy path, equity curve hash, trade count, seeds, input SHA-256
- `equity.csv` — per-trade equity series
- `trades.csv` — full trade list
- Integrity verified via SHA-256 payload hashing

## Key Patterns

- **Zero-Future Rule**: `ctx.data` only exposes completed bars (current index - 1), preventing look-ahead bias
- **Lazy Imports**: Heavy modules (Monte Carlo, optimizer, report export) are deferred via `__getattr__` in `engine/__init__.py`
- **Bid Convention**: Charts plotted on Bid side. Ask = Bid + spread. Longs pay spread, shorts exit at Ask
- **Callback Hooks**: Engine uses lists of callables (`on_trade_completed`, `on_step_completed`, etc.) — adapters append listeners
- **Signal Suppression**: `step_n()` suppresses per-step signals, emits final state once
