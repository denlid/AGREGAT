# Chart + Engine Core — Глибокий аналіз і Roadmap до «грааля»

> Синтез глибокого аудиту (4 паралельні дослідження): chart rendering, chart UX, engine execution, engine architecture.
> Ревізія коду після Sprint 0–4 + Numba. Дата: 2026-05-30.
> Доповнює [chart_vectorized_roadmap_v2.md](chart_vectorized_roadmap_v2.md) (виконані спринти).

---

## TL;DR — Загальна оцінка

| Підсистема | Оцінка | Стан |
|---|---|---|
| Vectorized engine (throughput) | 9/10 | Numba + feature cache + batch + shared-mem. Майже стеля. |
| Chart rendering (perf) | 8/10 | Zero-copy snapshot, LOD, overlay diff, GL. Кілька дрібних хвостів. |
| Chart UX | 7/10 | Професійний core, але бракує grail-фіч (measure tool, volume pane, per-drawing delete). |
| Engine architecture | 6/10 | Чистий GUI/core split, але BacktestCore — god-class, circular import, global state. |
| Dual-engine parity | 8/10 | Спільний `_process_one_candle`, але feature-handling може тихо розходитись. |
| Look-ahead correctness | 6/10 | Евристика ловить грубі випадки, тонкі — ні. |

**Корінь стану:** перформанс уже майже вичерпано (Numba дав ~22x, chart zero-copy дав ~22x prep). Подальший «грааль» — це **не швидкість, а архітектурна якість + UX-повнота + correctness**.

---

## 🔴 Знайдені баги (виправити негайно)

### BUG-1: `_autofit_y` зламаний на 1m path ✅ FIXED
[tab.py:1654](../gui/chart_tab/tab.py#L1654) приймав `res_df`, але Sprint 3 замінив його на `snapshot`. У 1m path `res_df` не існує → `NameError` → autofit-Y мовчки падає через try/except. **Виправлено** під час цього аудиту: `_autofit_y(vb, snapshot, n)` + `snapshot.high/low` slices.

### BUG-2: Повторні `pd.to_datetime` у user drawings (perf)
[tab.py:_draw_user_items](../gui/chart_tab/tab.py#L1043) — кожен drawing конвертує час 5-10 разів на КОЖЕН redraw через `pd.to_datetime(d["t"])`. З 50 drawings = 200-400ms на redraw. Strategy drawings вже кешують `_t_np` — user drawings ні.
**Fix:** кешувати `_t_np`/`_t2_np` у dict на першому використанні (10 хв).

### BUG-3: Dead OHLC tooltip код
[tab.py:_on_mouse_move](../gui/chart_tab/tab.py#L614) — `get_ohlc_at(idx)` викликається на кожен mouse-move (O(log N)), але результат ховається (`ohlc_label.hide()`). Або видалити, або увімкнути tooltip (це grail-фіча, див. UX нижче).

---

## ЧАСТИНА 1 — Chart: швидкість

### Що вже оптимально (НЕ чіпати)
- **Zero-copy snapshot** ([render_snapshot.py](../gui/chart_tab/render_snapshot.py)) — O(1) slices, не copy.
- **Trade markers** — batch searchsorted, 481 trades: 610ms → 2ms ([markers.py](../gui/chart_tab/markers.py)).
- **Viewport culling + batched QPainter** — ~6 draw calls замість 4×N ([items.py](../gui/chart_tab/items.py)).
- **LOD pyramid + hysteresis** — ±10% band проти thrashing, OHLC min/max correctness perfect.
- **Overlay diff** (Sprint 4) — add/remove only changed.
- **Deferred redraw** — 60ms debounce, adaptive LOD при >24ms.
- **Crosshair** — O(log N), throttled 40ms, no redraw on hover.

### Хвости (ranked by ROI)
| # | Проблема | ROI | LOE |
|---|---|---|---|
| 1 | BUG-2: user drawing `pd.to_datetime` cache | 200-400ms/redraw при багатьох drawings | 10 хв |
| 2 | User drawing time lookups не векторизовані (loop searchsorted) | 2-5x при >20 drawings | 20 хв |
| 3 | Strategy drawing time-conversion у diff-add loop | 1-3ms при багатьох нових | 15 хв |
| 4 | BUG-3: dead OHLC fetch on mouse-move | <1ms (чистота) | 5 хв |
| 5 | Memoize `_prev_res_times` shift | 50µs/frame | 10 хв |

**Висновок perf:** chart prep уже швидкий. Найбільший реальний хвіст — BUG-2 (user drawings), і то лише на насичених сценах. Все інше — мікро.

### GL backend — недовикористаний важіль
GL candle rendering ([gl_candle.py](../gui/chart_tab/gl_candle.py)) **за замовчуванням ВИМКНЕНО** (opt-in через `AGREGAT_GL_CANDLES=1`). 2 GPU draw calls замість CPU loop. Варто:
- Авто-вмикати GL коли bars > поріг (напр. >5000 visible) + graceful fallback (вже є).
- Задокументувати/протестувати на кількох GPU перед дефолтним вмиканням.

---

## ЧАСТИНА 2 — Chart: UX (до «грааля» = TradingView-class)

### Що є і сильне
- Pan/zoom/crosshair з незалежними осями (Wheel=X, Shift+Wheel=Y, Ctrl=both), zoom anchoring на курсор.
- Drawing tools: line/hline/rect/marker/text/brush + color/width picker, persist у `saves/drawings.json` (atomic write), undo/redo (50 рівнів), ROI drag.
- Trade markers batched + `focus_trade()` з анімацією (OutCubic) + breadcrumb.
- Replay: play/pause/speed (1-200x), TF-aware stepping, backward step, run-to-end (headless + quant).
- Adaptive LOD + perf telemetry (30+ метрик/frame).

### Grail-розриви (ranked by user value)
| # | Фіча | Стан | Value | LOE |
|---|---|---|---|---|
| 1 | **OHLC tooltip** на crosshair | код є, вимкнений (BUG-3) | Висока | 1 год |
| 2 | **Measuring tool** (відстань/% між точками) | відсутній | Висока | 1 день |
| 3 | **Volume pane** (окремий subplot) | відсутній | Висока | 1-2 дні |
| 4 | **Per-drawing delete/edit** (context menu) | лише "Clear All" | Середня | 0.5 дня |
| 5 | **Smooth TF-switch transition** | jump (animate існує для focus_trade, не для TF) | Середня | 0.5 дня |
| 6 | **Keyboard shortcuts dialog** | розкидані по tooltips | Середня | 0.5 дня |
| 7 | **Multi-pane indicators UI** | лише raw strategy overlays | Висока | 2-3 дні |
| 8 | **Save/load layouts** (viewport+zoom) | лише drawings persist | Низька | 0.5 дня |
| 9 | **Price alerts** на чарті | відсутні | Низька | 1 день |

### Half-done / tech debt у chart
- User drawings не re-render коректно після агресивного TF-swap (time-based coords, але не тестовано).
- DataPager (async tiles) wired, але без UI toggle; tile arrival = повний `engine.load_data(df)` reload.
- Quant run-mode code є ([bar_replay.py](../gui/widgets/bar_replay.py)), але не виставлений у UI.

---

## ЧАСТИНА 3 — Engine core: швидкість

### Що вже оптимально
- **Numba JIT** (`@njit(cache=True)`) — inner loop 0.23ms на 50k bars (3% часу). НЕ паралелізується (sequential equity state) — і не треба.
- **Feature cache** (S1.5) — 91.6% hit, reuse важких індикаторів.
- **Batch engine** (S2) — hoisting wrapper overhead, 2.29x sweep.
- **Shared-memory bundle** — 39x RAM reduction на 8 workers.
- **PreparedDataset** — immutable, hashable, zero-copy reuse.

### Реальний bottleneck тепер: НЕ inner loop
Розклад sweep-combo (50k bars, після Numba):
- Signal generation: **30-50%** (strategy-dependent, feature cache допомагає)
- Python wrapper overhead: **20%** (validation, perf-dict 20+ keys, VectorizedResult)
- Trade materialization: 10-20% (уникається в summary_only)
- Numba loop: **3%** (вичерпано)

| # | Можливість | ROI | Стан |
|---|---|---|---|
| 1 | Signal-gen profiling + tuning на feature-heavy стратегіях | 50ms→10ms можливо | не зроблено |
| 2 | Worker-local feature cache prepopulation | 20-30% на MP sweeps | не зроблено |
| 3 | Vectorize trade materialization (batch dict→DataFrame) | 10-20ms/1K trades | складно |
| 4 | float32 OHLC опція | 16MB/1M bars | низький ROI |
| 5 | 2D Numba kernel | ~0 (атакує 3%) | НЕ робити |

**Висновок:** throughput майже на стелі. Залишок — signal-gen (залежить від стратегій користувача, не від движка).

---

## ЧАСТИНА 4 — Engine core: архітектура (найбільший простір для «грааля»)

### 🔴 ARCH-1: BacktestCore — god-class
[backtest_core.py](../engine/core/backtest_core.py) — **1666 рядків, 45 методів, 13 public mutable полів**. Відповідає за: дані, стратегію, stepping, ордери, трейди, equity, drawings, stats, reset. Робить unit-тестування дорогим, регресії частими.

**Рекомендований split:**
- `OrderManagementEngine` — pending/position/SL/TP (~200 LOC, висока reuse, об'єднує on-candle + vectorized логіку).
- `EquityTracker` — equity/drawdown/bar_equity (~150 LOC).
- `DrawingEngine` — drawings/layers (~100 LOC).
- ROI: 3-5x test isolation.

### 🔴 ARCH-2: Circular import prepared_dataset ↔ optimization
[prepared_dataset.py](../engine/execution/prepared_dataset.py) імпортує `optimization.shared_arrays`, а optimization залежить від execution. Я вже обходив це lazy-import у Sprint 0. Правильний фікс: інвертувати залежність — dataset prep у runner-wrapper, optimizer приймає generic `Callable[[params], metrics]`.

### 🔴 ARCH-3: Stats mirror fields duplication
8 полів `_stats_*` ([backtest_core.py](../engine/core/backtest_core.py)) дублюють `StatsAccumulator`. Два code path оновлюють ті самі дані → ризик drift. Fix: deprecate direct access, expose `engine.stats.wins` property.

### 🟡 ARCH-4: Strategy loader — no sandbox
[context.py StrategyLoader](../engine/strategy/context.py) виконує arbitrary код з повними привілеями. OK для single-user desktop, критично для multi-user/cloud. Fix (якщо потрібен): RestrictedPython або subprocess isolation.

### 🟡 ARCH-5: Global mutable feature cache
[feature_cache.py](../engine/execution/feature_cache.py) — `_GLOBAL_FEATURE_CACHE`. Thread-safe (RLock), але sharing між непов'язаними sweeps сумнівний. Fix: передавати cache явно параметром.

### 🟡 ARCH-6: Duplicated `_round_to_step`
Байт-ідентична копія у backtest_core + vectorized_engine (щоб уникнути circular import). Прийнятно тимчасово, але flag для shared util.

### Спостережуваність
- Структуроване логування (JSON+trace-id) є лише в optimizer, не в engine core.
- Немає perf-instrumentation у `_process_one_candle` / HTF agg / strategy hooks.
- Тести: добра integration coverage, слабка unit isolation (через god-class), немає property-based/fuzzing.

---

## ⚠️ CORRECTNESS — найбільш недооцінений ризик

### Dual-engine parity drift
Visual (`on_candle`) і Vectorized (`vectorized_signals`) — два різні описи стратегії. Якщо вони розходяться, користувач отримує різні результати в Replay vs Optimizer. Спільний `_process_one_candle` тримає execution-логіку, АЛЕ **signal-логіка дублюється** і може тихо розійтись.

**Рекомендація:** просувати feature-cache контракт (`build_vectorized_features` + `vectorized_signals_from_features`) як **єдине джерело істини**, з якого on-candle теж читає. Або: автоматичний parity-тест (запустити обидва движки на тій самій стратегії, порівняти трейди) у CI.

### Look-ahead bias — слабка евристика
Ловить лише грубі випадки (truncate 10 bars). Тонкі (MA з неправильним lookback, завтрашній close) не ловить. Це **тихий killer** для backtest-валідності.

---

## 🎯 Roadmap до «грааля» (ranked)

### Phase A — Bugfixes + correctness (P0, дні)
- [x] BUG-1: `_autofit_y` snapshot fix (зроблено в аудиті)
- [ ] BUG-2: user drawing `pd.to_datetime` cache
- [ ] BUG-3: OHLC tooltip — увімкнути (а не видаляти) → grail UX-фіча
- [ ] **Dual-engine parity CI test** — найважливіше для довіри до результатів

### Phase B — Architecture hygiene (P1, 1-2 тижні)
- [ ] Extract `OrderManagementEngine` з BacktestCore
- [ ] Unify stats (прибрати `_stats_*` mirrors)
- [ ] Invert optimizer/prepared_dataset coupling
- [ ] Structured logging + trace-id у engine core

### Phase C — Chart UX до grail (P1, 1-2 тижні)
- [ ] OHLC tooltip (Phase A) + measuring tool
- [ ] Volume pane (окремий subplot)
- [ ] Per-drawing delete/edit (context menu)
- [ ] Smooth TF-switch (reuse `_animate_view_range`)
- [ ] Keyboard shortcuts dialog

### Phase D — Engine perf хвости (P2, опційно)
- [ ] Signal-gen profiling на feature-heavy стратегіях
- [ ] Auto-enable GL backend за порогом bars
- [ ] Worker-local feature cache prepopulation

### Phase E — Multi-pane indicators (P2, grail-фіча, 2-3 дні)
- [ ] Built-in indicator UI (RSI/MACD/Volume у subpanes)

### Backlog (низький ROI)
- float32 OHLC, 2D Numba kernel, persistent LOD on disk, save/load layouts, price alerts, strategy sandbox (якщо не multi-user).

---

## Підсумкова рекомендація

Швидкість **майже вичерпана** — далі гнатись за ms немає сенсу (Numba дав 22x, chart zero-copy дав 22x). Реальний шлях до «грааля» тепер:

1. **Correctness першочергово** — dual-engine parity CI test + кращий look-ahead detector. Без цього вся швидкість безглузда, якщо результати можуть тихо розходитись.
2. **Архітектурна гігієна** — розбити god-class, прибрати дублювання. Це робить майбутні фічі дешевими.
3. **UX-повнота** — OHLC tooltip, measuring, volume pane. Це те, що відрізняє «швидкий чарт» від «грааль-чарта».

Я б почав з **Phase A** (баги + parity test), бо це найдешевше і найвищий ризик/довіра.
