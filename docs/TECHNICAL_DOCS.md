# AGREGAT Platform - Technical Documentation

## Overview
AGREGAT (Algorithmic Graphical Reviewer Engine Genius Analytics Tester) is an institutional-grade quantitative trading backtesting platform built in Python using PySide6 for the GUI, Pandas/NumPy for performance-critical vectorized operations, and PyQtGraph for real-time chart rendering.

## System Architecture

The application follows an advanced Model-View-Controller (MVC) architecture combined with an event-driven engine:
1. **Engine (Model)**: Handles data processing, strategy execution, and statistics calculation.
2. **GUI Tabs & Widgets (View)**: Present data visually (charts, stats, code editors).
3. **Controllers (Controller)**: Manage communication between the Engine and Views, decoupling the core logic from UI interactions.

---

## Component Details

### 1. Application Entry Point (`app.py` & `gui/main_window.py`)
- **`app.py`**: Initializes the PySide6 `QApplication`, configures High-DPI scaling, and bootstraps the main window and theme engine.
- **`MainWindow` (`main_window.py`)**: Acts as the central orchestrator. It instantiates the `BacktestEngine`, delegates component lifecycle to specific controllers, sets up keyboard shortcuts, and mounts all application tabs (Chart, Stats, Trades, Analytics, Quant, Logs, Strategy Editor, Settings).

### 2. The Backtest Engine (`engine/`)
The core processing layer is designed to run asynchronously or step-by-step for visualization.
- **`BacktestEngine`**: Manages the main execution loop. It takes historical data (OHLCV) and iterates through it, feeding state to the current strategy. It computes ongoing metrics like account balance, open positions, PnL, and drawdown.
- **Data Loaders**: Load data from CSV formats (like JForex/MT5 exports), normalize columns (Time, Open, High, Low, Close, Volume), and populate engine buffers.
- **`Strategy` & `Context`**: The base classes for user strategies. Strategies react to events (e.g., `on_candle()`) and use the `Context` object to place trades, manage risk, and query historical buffers.

### 3. Controllers (`gui/controllers/`)
Controllers handle cross-cutting concerns and UI state without bloating `MainWindow`:
- **`SessionLifecycleController`**: Manages saving and loading of backtest sessions and strategy configurations. Supports async data loading to prevent UI freezing.
- **`ReplayController`**: Handles play/pause mechanics, visual stepping (candle-by-candle), and drawing tools on the chart.
- **`AssetController`**: Manages symbol/asset selections and their respective data feeds.
- **`SettingsController`**: Reads/writes configuration to `saves/settings.json`.
- **`ThemeController`**: Applies styling (`.qss`), switching between dark, light, or auto themes dynamically.

### 4. User Interface Tabs (`gui/tabs/`)
- **`ChartTab`**: The primary visualization surface built on PyQtGraph. Supports real-time candle rendering, zooming, panning, and rendering trade markers (entries/exits).
- **`StatsTab`**: Displays a comprehensive performance summary (Sharpe Ratio, Win Rate, Max Drawdown, Net Profit).
- **`TradesTab`**: A tabular view of all executed trades, with filtering capabilities.
- **`AnalyticsTab` & `QuantTab`**: Advanced statistical breakdowns, return distributions, and equity curve analysis.
- **`StrategyEditorTab`**: An integrated code editor (Monaco Editor via QWebEngine) for writing Python-based trading strategies. Supports direct sync to the engine.

### 5. AI Assistant Integration (`gui/widgets/ai_editor_web.py` & `gui/workers/ai_worker.py`)
- **`AIAssistantEditor`**: A web-based AI chat interface integrated into the platform. Allows users to request strategy modifications, code explanations, and chart analysis.
- **`AIAssistantWorker`**: A `QThread` subclass that handles asynchronous communication with the LLM API. It streams responses back to the UI to maintain responsiveness.
- **Chart Context Injection**: Users can select areas on the chart, and the coordinates/data are injected directly into the LLM context for visual data analysis.

---

## Build System (Nuitka)
The project is compiled into a standalone executable using Nuitka. Key compile considerations include:
- `--enable-plugin=pyside6`: Essential for shipping Qt binaries.
- `--include-data-dir`: GUI web assets, themes (`.qss`), config JSONs, and strategies must be explicitly packaged.

## Event Flow Lifecycle
1. User loads data via UI -> `SessionLifecycleController`.
2. Data parsed into Pandas DataFrame -> Loaded into `BacktestEngine`.
3. User runs strategy -> `ReplayController` signals engine.
4. Engine processes candle -> Emits `step_completed` and `position_changed` signals.
5. `ChartTab` & `StatsTab` update incrementally to provide visual feedback.
6. Execution completes -> Full statistics generated in `StatsTab`.
