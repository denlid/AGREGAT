# AGREGAT вЂ” Architecture Overview

## Project Identity

AGREGAT v7.78 вЂ” Algorithmic Graphical Reviewer Engine Genius Analytics Tester.  
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
в”њв”Ђв”Ђ app.py                 # Entry point (license, splash, theme, GPU)
в”њв”Ђв”Ђ engine/                # Headless core (no PySide6 dependency)
в”‚   в”њв”Ђв”Ђ core/              #   Iterative backtest core
в”‚   в”њв”Ђв”Ђ strategy/          #   Strategy API (Context, Param, loader)
в”‚   в”њв”Ђв”Ђ data/              #   Data loading, TF aggregation
в”‚   в”њв”Ђв”Ђ execution/         #   Bid/Ask model, slippage, execution costs
в”‚   в”њв”Ђв”Ђ models/            #   Data models (Position, Trade, AssetConfig)
в”‚   в”њв”Ђв”Ђ analytics/         #   Metrics, MC, equity curve, risk
в”‚   в”њв”Ђв”Ђ persistence/       #   .agbk save/load, integrity checks
в”‚   в”њв”Ђв”Ђ optimization/      #   Grid/random search, walk-forward
в”‚   в”њв”Ђв”Ђ report/            #   HTML tearsheet exporter
в”‚   в”њв”Ђв”Ђ security/          #   License verification
в”‚   в”њв”Ђв”Ђ adapters/          #   Qt signal bridge
в”‚   в”њв”Ђв”Ђ observability/     #   Sentry, audit log
в”‚   в””в”Ђв”Ђ metrics/           #   Advanced metrics (registry, pain, ratios)
в”њв”Ђв”Ђ gui/                   # PySide6 UI
в”‚   в”њв”Ђв”Ђ main_window.py     #   Orchestrator: engine + tabs + controllers
в”‚   в”њв”Ђв”Ђ chart_tab/         #   OpenGL-accelerated multi-timeframe chart
в”‚   в”њв”Ђв”Ђ tabs/              #   Stats, Trades, Analytics, Settings, Log
в”‚   в”њв”Ђв”Ђ controllers/       #   Session, replay, settings, theme, toolbar, menu
в”‚   в”њв”Ђв”Ђ widgets/           #   Bar replay, AI editor, metric cards, sparklines
в”‚   в”њв”Ђв”Ђ web/               #   WebEngine app assets (AI editor HTML/CSS/JS)
в”‚   в”њв”Ђв”Ђ dialogs/           #   Monte Carlo, custom asset, cheat sheet
в”‚   в”њв”Ђв”Ђ styling/           #   Theme engine (dark/light)
в”‚   в”њв”Ђв”Ђ components/        #   Toast, animations, quantum spinner
в”‚   в”њв”Ђв”Ђ workers/           #   AI worker, metrics worker
в”‚   в””в”Ђв”Ђ persistence/       #   Settings persist, recent files, drawings
в”њв”Ђв”Ђ strategies/            # User strategy .py files
в”њв”Ђв”Ђ tests/                 # Test suite (~90 files)
в”њв”Ђв”Ђ bench/                 # Benchmarking harness
в”њв”Ђв”Ђ config/                # Asset registry, AI prompt system prompts
в”њв”Ђв”Ђ docs/                  # Documentation
в””в”Ђв”Ђ notebooks/             # Quickstart notebooks
```

## Data Flow вЂ” Backtest Execution

```
Data Source (CSV/Parquet/Feather)
       в”‚
       в–ј
  DataLoader.load_*(path) в”Ђв”Ђв”Ђв–є pandas.DataFrame (OHLCV + spread)
       в”‚
       в–ј
  BacktestCore.load_data(df)
       в”‚  вЂў Binds NumPy columns (O(1), no copy)
       в”‚  вЂў Prebuilds TF aggregator arrays
       в”‚  вЂў Allocates bar-equity buffer (optional)
       в–ј
  BacktestCore.load_strategy(strategy)
       в”‚  вЂў StrategyLoader.load(filepath)
       в”‚  вЂў Supports engine.api.Strategy OR class-based Strategy
       в–ј
  BacktestCore.step() / step_n()
       в”‚  _process_one_candle(idx):
       в”‚    1. Advance TF aggregator
       в”‚    2. Fill pending order at open
       в”‚    3. Update MAE/MFE, check SL/TP
       в”‚    4. EOD close (if enabled)
       в”‚    5. Weekend close (if enabled)
       в”‚    6. Strategy HTF hooks (15m / 1h / 1d)
       в”‚    7. Strategy main on_candle(ctx)
       в”‚    8. Record bar-level equity (if enabled)
       в–ј
  Trade list + equity curve + stats
       в”‚
       в–ј
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

## Backtest Engine

| Engine | File | Characteristics |
|--------|------|----------------|
| BacktestCore | `engine/core/backtest_core.py` | Iterative, per-bar step, pure Python, no Qt |

The `BacktestEngine` import is a shim (`engine/backtest_engine.py`) that re-exports `QtBacktestAdapter` (the adapter wrapping `BacktestCore` with Qt signals).

## GUI Architecture

```
MainWindow (thin orchestrator)
  в”њв”Ђв”Ђ engine (BacktestEngine / QtBacktestAdapter)
  в”њв”Ђв”Ђ ChartTab (OpenGL candles + LOD pyramid + markers)
  в”њв”Ђв”Ђ StatsTab
  в”њв”Ђв”Ђ TradesTab
  в”њв”Ђв”Ђ AnalyticsTab
  в”њв”Ђв”Ђ QuantTab
  в”њв”Ђв”Ђ SettingsTab
  в”њв”Ђв”Ђ LogConsole
  в”њв”Ђв”Ђ StrategyEditorTab (Monaco editor)
  в”њв”Ђв”Ђ AIAssistantEditor (WebEngine)
  в”њв”Ђв”Ђ Controllers:
  в”‚   в”њв”Ђв”Ђ SessionLifecycleController  вЂ” load/save .agbk
  в”‚   в”њв”Ђв”Ђ ReplayController            вЂ” step/step_n/rewind
  в”‚   в”њв”Ђв”Ђ SettingsController           вЂ” settings persistence
  в”‚   в”њв”Ђв”Ђ ThemeController              вЂ” dark/light theme
  в”‚   в”њв”Ђв”Ђ ToolbarController            вЂ” UI toolbar
  в”‚   в”њв”Ђв”Ђ MenuController               вЂ” file/edit menus
  в”‚   в”њв”Ђв”Ђ ModeController               вЂ” dashboard audience switching
  в”‚   в”њв”Ђв”Ђ StatusBarController          вЂ” status bar
  в”‚   в””в”Ђв”Ђ AssetController              вЂ” asset selection
  в””в”Ђв”Ђ SharedTradesCache (cross-tab shared data)
```

## Persistence Format

`.agbk` files are ZIP archives containing:
- `meta.json` вЂ” strategy path, equity curve hash, trade count, seeds, input SHA-256
- `equity.csv` вЂ” per-trade equity series
- `trades.csv` вЂ” full trade list
- Integrity verified via SHA-256 payload hashing

## Key Patterns

- **Zero-Future Rule**: `ctx.data` only exposes completed bars (current index - 1), preventing look-ahead bias
- **Lazy Imports**: Heavy modules (Monte Carlo, optimizer, report export) are deferred via `__getattr__` in `engine/__init__.py`
- **Bid Convention**: Charts plotted on Bid side. Ask = Bid + spread. Longs pay spread, shorts exit at Ask
- **Callback Hooks**: Engine uses lists of callables (`on_trade_completed`, `on_step_completed`, etc.) вЂ” adapters append listeners
- **Signal Suppression**: `step_n()` suppresses per-step signals, emits final state once
