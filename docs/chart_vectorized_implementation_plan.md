# Chart + Vectorized Implementation Plan

## Goal
Довести вкладку Chart і vectorized/optimizer path до стану, де приріст продуктивності досягається системно, а не серією локальних патчів.

Ключова ціль:
- 10x+ на optimizer-style repeated runs там, де це реально по архітектурі
- стабільний, передбачуваний, плавний Chart на 100k–1M+ bars
- чітка performance-дисципліна: telemetry, benchmarks, regression gates

---

## Working principles

1. **Measure first**
   Будь-яка оптимізація має бути підтверджена telemetry/bench.

2. **Immutable prepared data > repeated conversion**
   І для chart, і для vectorized path треба мінімізувати pandas/copy work у hot path.

3. **Viewport-first / summary-first execution**
   Віддавати рівно той обсяг роботи, який реально потрібен поточному UX або optimizer flow.

4. **Capability-driven acceleration**
   Найагресивніші прискорення типу batch execution застосовувати лише там, де стратегія структурно придатна.

5. **Do not over-refactor too early**
   Спочатку stage split і performance boundaries, потім глибша декомпозиція в нові класи.

---

## Master plan

### Phase A — Measurement discipline and baselines
**Priority:** P0  
**Purpose:** зафіксувати, де реально час/пам'ять витрачаються зараз

#### Tasks
1. Розширити perf telemetry для vectorized path.
2. Формалізувати KPI для chart redraw stages.
3. Додати/оновити benchmark scenarios для:
   - 10k / 100k / 500k / 1M bars
   - low/high trade count
   - markers on/off
   - overlays light/heavy
   - painter vs GL
4. Підготувати regression thresholds.

#### Target files
- `engine/execution/vectorized_engine.py`
- `gui/chart_tab/tab.py`
- `gui/chart_tab/items.py`
- `bench/*`
- `tests/perf/*`

#### Acceptance criteria
- telemetry дозволяє побачити stage-by-stage breakdown
- benchmarks відтворювані локально
- є baseline JSON/CSV для ключових сценаріїв

#### Notes
Це перший sprint, без нього інші зміни будуть менш керованими.

---

### Phase B — Vectorized optimizer fast path
**Priority:** P0  
**Purpose:** забрати найбільший реальний CPU overhead із repeated runs

#### Tasks
1. Довести `materialize_trades=False` до повноцінного optimizer mode:
   - `summary_only=True`
   - `compact_result=True`
   - optional `equity_curve=False`
2. Додати чіткий feature-cache contract:
   - `build_vectorized_features(...)`
   - `vectorized_signals_from_features(...)`
3. Розширити `PreparedDataset` до v2:
   - dataset key/hash
   - optional int64 time representation
   - memory metadata
   - shared-memory-ready bundle
4. Мінімізувати Python-side time conversion/materialization overhead.
5. Підготувати capability model для batch execution.

#### Target files
- `engine/execution/vectorized_engine.py`
- `engine/execution/prepared_dataset.py`
- `engine/optimization/*`
- strategy contract docs / adapters

#### Expected gain
- 2x–6x на repeated optimization workloads
- суттєво більше, якщо workload зараз упирається в `Trade` materialization

#### Acceptance criteria
- optimizer flow може працювати без `Trade` objects
- повторні runs на одному dataset не повторюють дорогий prep без потреби
- perf telemetry явно показує виграш окремо по `signals`, loop, summary, materialization

---

### Phase C — Batch execution for compatible strategies
**Priority:** P0/P1  
**Purpose:** дати справжній multiplicative gain на parameter sweeps

#### Tasks
1. Визначити capability contract для batch-compatible strategies.
2. Реалізувати PoC `run_vectorized_batch(...)`.
3. Підтримати block/chunk execution по axis параметрів.
4. Додати порівняння parity vs single-run path.
5. Окремо заміряти RAM pressure.

#### Risks
- не універсально для всіх стратегій
- може різко збільшити memory footprint при наївному `(bars, K)` packing
- потребує строгого capability fallback

#### Acceptance criteria
- batch path не ламає correctness
- є measurable gain на хоча б одному реальному sweep class
- fallback до single-run path прозорий

---

### Phase D — Chart redraw decomposition
**Priority:** P1  
**Purpose:** прибрати монолітність `ChartTab.redraw_chart()` і зробити redraw керованим

#### Tasks
1. Розбити redraw на stage methods:
   - `_resolve_render_frame()`
   - `_update_chart_arrays()`
   - `_update_candles()`
   - `_update_trade_markers()`
   - `_update_strategy_layer()`
   - `_update_user_layer()`
   - `_update_camera()`
   - `_collect_perf_stats()`
2. Ввести dirty-flag invalidation model.
3. Звести прості UX-actions до partial redraw, а не full redraw.

#### Target files
- `gui/chart_tab/tab.py`
- potential new helpers under `gui/chart_tab/`

#### Acceptance criteria
- `redraw_chart()` стає coordinator-методом
- stage timings видно окремо
- toggle markers / drawings / camera changes не запускають зайвий full work

---

### Phase E — Array-first chart prepared model
**Priority:** P1  
**Purpose:** прибрати pandas/DataFrame churn із redraw hot path

#### Tasks
1. Створити `ChartRenderSnapshot` або близький immutable prepared object.
2. Винести per-TF arrays/cache за межі repaint cycle.
3. Замінити DataFrame `.copy()`, `reset_index()`, repeated datetime conversion на NumPy/int64 path.
4. Закріпити O(1)/cheap OHLC lookup contract під crosshair і tooltips.

#### Target files
- `gui/chart_tab/tab.py`
- `gui/chart_tab/items.py`
- можливо новий `gui/chart_tab/render_snapshot.py`

#### Expected gain
- 2x–4x на chart CPU prep side

#### Acceptance criteria
- redraw hot path працює через prepared arrays, а не через повторну DataFrame-підготовку
- crosshair/tooltip lookup не скочується в O(N)

---

### Phase F — Overlay diff reconciliation
**Priority:** P1  
**Purpose:** знизити scene churn у важких сценах

#### Tasks
1. Для strategy overlays перейти з clear/recreate на diff update.
2. Те саме для user drawings.
3. Додати stable IDs / identity contract.
4. Додати overlay telemetry: counts, rebuild/update timings.

#### Acceptance criteria
- зміна одного overlay не перебудовує все
- heavy annotated scenes перестають давати великі latency spikes

---

### Phase G — Viewport-first candle backend
**Priority:** P1/P2  
**Purpose:** зробити ціну рендера пропорційною реально видимій інформації

#### Tasks
1. Для software path зменшити Python-object churn.
2. Для `CandlestickItem` прибрати дорогі fallback behaviors:
   - O(N) lookup fallback
   - надмірний overscan
3. Для GL path визначити policy:
   - retained VBO vs visible-slice VBO
   - upload only when needed
4. Для extreme zoom-out додати representation tiers:
   - candles
   - simplified candles
   - min/max envelope / density band

#### Target files
- `gui/chart_tab/items.py`
- `gui/chart_tab/gl_candle.py`
- `gui/chart_tab/gpu_config.py`

#### Acceptance criteria
- visible-window path clearly dominates full-history work
- extreme zoom-out більше не намагається “чесно малювати свічки всюди”

---

### Phase H — Pager reconciliation redesign
**Priority:** P2  
**Purpose:** зберегти async load benefits без CPU spikes після tile arrival

#### Tasks
1. Зменшити роль `engine.load_data(df)` як глобального reload path.
2. Ввести incremental merge/update semantics.
3. Кешувати prepared arrays per tile / per stitched window.
4. Уникати full concat/sort/reprepare на кожному оновленні.

#### Target files
- `engine/data/data_pager.py`
- chart integration points

#### Acceptance criteria
- tile arrival не викликає великих downstream rebuild хвиль
- scrolling/load на великих датасетах стає відчутно рівнішим

---

## Recommended sprint order

### Sprint 1
- telemetry hardening
- vectorized perf v2 counters
- chart stage KPI skeleton
- benchmark baseline

### Sprint 2
- optimizer fast mode
- compact/no-trade result path
- PreparedDataset v2 design

### Sprint 3
- feature cache contract
- strategy decomposition PoC
- repeated-run benchmarks

### Sprint 4
- ChartTab redraw split
- dirty flags
- partial redraw wiring

### Sprint 5
- array-first render snapshot
- O(1) OHLC lookup
- overlay diff reconciliation

### Sprint 6
- batch execution PoC
- shared-memory optimizer dataset reuse

### Sprint 7
- GL visible-slice policy
- representation tiers
- pager incremental integration

---

## First execution batch (start now)

### Batch 1A — instrumentation and fast-path formalization
1. Expand vectorized perf payload:
   - `prepared_reused`
   - `materialize_trades`
   - `summary_only`
   - `signal_memory_mb`
   - `strategy_name`
2. Formalize optimizer result mode in `run_vectorized()`.
3. Add/update tests for summary-only / no-trade mode.
4. Add or extend benchmark script for repeated vectorized runs.

### Why this first
Бо це найменш ризиковий і найшвидший шлях отримати:
- реальні числа
- швидкий optimizer improvement
- базу для наступних архітектурних рішень

---

## Practical coding entry points

### Highest-value code entry points right now
- `engine/execution/vectorized_engine.py`
- `engine/execution/prepared_dataset.py`
- `tests/test_run_vectorized_stats.py`
- `tests/test_vectorized_engine.py`
- `bench/` related vectorized scripts

### Next chart entry points
- `gui/chart_tab/tab.py`
- `gui/chart_tab/items.py`
- chart perf / cache tests

---

## KPI targets

### Vectorized
- repeated runs same dataset: 3x+ improvement
- optimizer summary path: 2x+ improvement
- batch-compatible sweeps: path to 10x+

### Chart
- pan/zoom redraw: <16 ms p50 on 100k-class scenarios
- TF switch: <80 ms on warm cache paths
- no visible freeze on large LOD-backed datasets

---

## Definition of done

Фаза вважається завершеною лише якщо:
1. є code change;
2. є telemetry/bench evidence;
3. є tests/parity coverage;
4. є документовані constraints/fallbacks.

---

## Immediate next step
Починаємо з **Batch 1A**: formalize optimizer fast mode + expand vectorized telemetry, бо це дає найкращий risk/reward і створює вимірювальну базу для всіх наступних прискорень.
