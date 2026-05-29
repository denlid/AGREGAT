# Chart + Vectorized Performance Audit & Roadmap

## Executive summary

Ціль: отримати **10x прискорення** для:
- векторного прогону / optimizer-style runs
- рендерингу вкладки **Chart**
- стабільної роботи на **100k–1M+ bars** та великих наборах trades / overlays

Поточний стан системи вже містить сильну базу для performance work:
- `PreparedDataset` для array-first execution path
- vectorized engine з Numba fast-path
- chart-side array cache
- viewport cache
- batched trade markers
- LOD pyramid
- optional GL candlestick backend
- async data pager
- adaptive render budget

Але архітектура ще не доведена до рівня, де 10x гарантовано й системно досягається. Основні проблеми зараз — це монолітний orchestration, pandas/DataFrame work у hot path, full redraw behavior для частини overlays, і недостатньо жорстке розділення між immutable data preparation та frame-time rendering.

---

## Scope of audit

### Files directly reviewed
- `gui/chart_tab/tab.py`
- `gui/chart_tab/items.py`
- `gui/chart_tab/gl_candle.py`
- `gui/chart_tab/gpu_config.py`
- `engine/execution/prepared_dataset.py`
- `engine/execution/vectorized_engine.py`
- `engine/data/data_pager.py`

### Relevant tests validated
- `tests/test_chart_gpu.py`
- `tests/test_phase5_chart_backend_adaptation.py`
- `tests/test_phase1_chart_arrays_cache.py`
- `tests/test_phase3_chart_viewport_cache.py`
- `tests/test_vectorized_engine.py`
- `tests/test_phase2_prepared_dataset.py`
- `tests/test_run_vectorized_stats.py`
- `tests/test_chart_data_pager.py`

### Verified fixes during audit
1. Removed risky GL availability probing in `gui/chart_tab/gpu_config.py` that could trigger native crash on Windows / Python 3.14.
2. Fixed backend-detection regression in `gui/chart_tab/tab.py` caused by local `CandlestickItem` shadowing.

---

## Current architecture snapshot

## 1. Chart architecture

`ChartTab` currently combines several responsibilities in one class:
- toolbar/UI controls
- plot + axis configuration
- viewport/camera logic
- draw tool interactions
- timeframe switching
- chart data preparation
- trade markers
- strategy overlays
- user drawings
- async pager integration
- performance telemetry
- adaptive rendering policy

### Positive observations
- redraw telemetry already exists (`_last_perf_stats`, `_perf_stats_history`)
- chart arrays are cached
- markers are batched
- candlestick rendering has viewport culling + LOD
- GL path is backend-compatible with legacy item
- pager is opt-in and thread-safe

### Structural risks
- `ChartTab` is too monolithic for predictable performance evolution
- `redraw_chart()` is the main “god pipeline”
- hot path still contains pandas work and repeated conversions
- overlay invalidation is coarse-grained
- data paging still reconnects through `engine.load_data(df)` style flow

---

## 2. Candlestick rendering architecture

### `gui/chart_tab/items.py`
The software candle renderer is already better than a naive implementation:
- viewport culling via `_visible_slice()`
- batched `drawLines()` / `drawRects()`
- LOD cap through `downsample_ohlc`
- width recompute based on zoom level
- picture-based cached paint object

### Strengths
- paint complexity is bounded by visible slice, not full dataset
- batch drawing greatly reduces per-bar painter call overhead
- viewport cache is in place

### Weaknesses
- `_gen()` still rebuilds a full `QPicture` snapshot when repainting
- visible batch extraction uses Python lists + `itertools.compress`
- geometry caches are partially Python-object based (`QLineF`, `QRectF` lists)
- `get_ohlc_at()` can fall back to O(N) scan over `_raw_data`
- `_visible_slice()` overscans by one full screen on each side, which is smooth but may still overdraw a lot on wide views

### `gui/chart_tab/gl_candle.py`
The GL backend is conceptually correct:
- CPU packs instance data once
- GPU draws bodies/wicks through instancing
- QPainter fallback exists
- VBO capacity pooling already exists

### Strengths
- good long-term direction for 100k+ bars
- incremental upload logic is already started
- backend can share LOD + viewport logic with legacy item

### Weaknesses / risks
- `_rebuild_gl_vertices()` currently packs the **entire** `_arr`, not just visible slice
- `paint()` still relies on CPU-prepared full instance buffer
- no strong visible-slice GPU upload policy yet
- no explicit pixel-binned extreme zoom-out representation
- fallback to painter may hide real GL regressions if telemetry is not surfaced clearly in UI/benchmarks

---

## 3. Vectorized execution architecture

### `engine/execution/prepared_dataset.py`
`PreparedDataset` is a strong foundation.

### Strengths
- immutable bundle
- pre-extracted OHLC arrays
- precomputed masks
- reusable across optimizer sweeps
- lower repeated pandas→NumPy overhead

### Weaknesses
- still stores `times` in datetime form, while some hot consumers repeatedly wrap them with `pd.Timestamp`
- mask-building still goes through pandas datetime conversion per dataset prep
- not all downstream consumers appear fully normalized to “array-only, no pandas in hot path”

### `engine/execution/vectorized_engine.py`
The engine has two modes:
- Numba fast path
- pure Python fallback

### Strengths
- clear Numba-accelerated core loop
- perf telemetry already emitted
- trade materialization is separated from inner loop
- drawdown tracking centralized

### Weaknesses
- even in Numba mode, Python-side post-processing may become bottleneck for high trade counts
- repeated `pd.Timestamp(...)` during trade materialization is expensive
- `Trade` object materialization cost can dominate once execution loop gets fast
- signals are still sourced from pandas columns (`to_numpy`) inside the run path
- summary + materialization path is optimized for usability, not necessarily optimizer-scale throughput

---

## 4. DataPager architecture

### Strengths
- proper async loading via `QThreadPool`
- lock-protected resident cache
- LRU eviction
- precomputed concatenated frame cache
- optional range slicing with binary search on cached sorted times
- clean opt-in integration

### Weaknesses
- `_handle_loaded()` does `pd.concat(...)` of resident chunks after each tile arrival
- it also sorts the concatenated frame on each update when `time` exists
- chart integration currently uses pager updates to feed a fresh frame into `engine.load_data(df)`
- this pattern can amplify cost into repeated downstream rebuilds

### Main implication
Pager hides disk I/O well, but may still pay too much **CPU reconciliation cost** after tile arrival.

---

## Key bottlenecks and likely root causes

## P0 bottlenecks

### 1. Monolithic `redraw_chart()` pipeline
Symptoms:
- one method owns data prep, render prep, overlays, camera, metrics
- hard to isolate stage costs
- hard to do partial invalidation

Impact:
- limits 10x potential because every improvement is partially canceled by unrelated stage work

### 2. Pandas/DataFrame in chart hot path
Examples:
- `res_df = ...copy()`
- rebuild of DataFrame for aggregated TF path
- repeated `pd.to_datetime(...)`
- repeated `DatetimeIndex(...).view(np.int64)`

Impact:
- CPU overhead
- memory churn
- cache miss amplification

### 3. Full overlay teardown/rebuild behavior
Examples:
- `_draw_user_items()` removes and recreates items
- `_draw_strategy()` removes and recreates items

Impact:
- scene churn
- Qt allocations/deallocations
- visible latency spikes when overlays are numerous

### 4. Full-array GL packing/upload orientation
`GLCandlestickItem` currently packs full array to `_gl_instances`.

Impact:
- unnecessary CPU work at large history sizes
- upload cost grows with resident bars, not visible bars

### 5. Pager reconciliation cost
`pd.concat + sort + engine.load_data(df)` after tile changes.

Impact:
- paging may reduce I/O stalls but still trigger expensive re-initialization waves

## P1 bottlenecks

### 6. Trade materialization overhead in vectorized engine
Even if loop is fast, Python object creation + timestamp conversion + string creation can dominate on massive trade counts.

### 7. O(N) fallback in `get_ohlc_at()`
Tooltips/crosshair should never degrade to linear scans when LOD is active.

### 8. Geometry caches stored as Python object lists
`QRectF` / `QLineF` object creation still creates pressure in software path.

---

## Target performance model

## Chart targets
- Chart open at 100k bars: **< 200 ms** cold, **< 80 ms** warm
- Pan/zoom redraw at 100k bars: **< 16 ms p50**, **< 24 ms p95**
- TF switch at 100k bars: **< 80 ms**
- 1M bars visible through LOD / representation switching without UI freeze
- Marker/overlay redraw isolated from candle redraw where possible

## Vectorized targets
- 10x vs current baseline on optimizer-like repeated runs
- dataset prep amortized across sweeps
- execution loop + summary path stable on 100k–1M bars
- trade materialization optional / lazy for optimization mode

---

## Recommended roadmap

# Phase 0 — Measurement discipline

## Goals
Make performance reproducible and enforceable.

## Actions
1. Add benchmark scenarios:
   - 10k / 100k / 500k / 1M bars
   - 100 / 1k / 10k trades
   - painter vs GL
   - overlays on/off
   - 1m vs aggregated TF
2. Persist benchmark outputs as JSON/CSV.
3. Add p50/p95/p99 timing aggregation.
4. Add memory snapshots.
5. Add CI regression thresholds for key scenarios.

## File suggestions
- `bench/bench_chart_*.py`
- `bench/bench_vectorized_*.py`
- `tests/perf/...`

## Success criteria
- every future optimization validated against the same scenarios
- regression budget defined in CI

---

# Phase 1 — Split orchestration from hot path

## Goals
Turn `ChartTab` from monolith into coordinator.

## Proposed components
### `ChartDataModel`
Responsible for:
- prepared base arrays
- TF-specific prepared arrays
- timestamp int64 arrays
- OHLC lookup maps

### `ChartViewportModel`
Responsible for:
- tracked x-range
- follow mode
- visible slice
- overscan policy

### `ChartRenderCoordinator`
Responsible for:
- full redraw vs partial redraw decisions
- dirty flags
- backend selection handoff

### `OverlayManager`
Responsible for:
- trade markers
- user drawings
- strategy drawings
- differential updates

### `RenderPolicy`
Responsible for:
- painter/GL/LOD policy
- adaptive budget thresholds
- level-of-representation switching

## Files to split first
- `gui/chart_tab/tab.py`
- likely new files under `gui/chart_tab/`

## Success criteria
- `redraw_chart()` becomes a short orchestration method
- stages benchmarkable independently

---

# Phase 2 — Remove pandas from frame-time chart path

## Goals
Use array views, not DataFrame copies, during redraw.

## Actions
1. Prebuild immutable arrays once on data load:
   - `ts_ns: int64[]`
   - `open/high/low/close: float64[]`
   - optional volume arrays
2. Build TF-specific prepared views once or incrementally.
3. Replace `res_df.iloc[...]` + `.copy()` in hot path with slice descriptors.
4. Replace repeated `pd.to_datetime(...)` in redraw with int64 search / lookup.
5. Store direct mapping for crosshair OHLC lookup even under LOD.

## Concrete changes
- add chart-prepared dataset object similar in spirit to `PreparedDataset`
- cache per-TF arrays outside UI repaint cycle

## Expected gain
- **2x–4x** on chart CPU preparation path

---

# Phase 3 — Viewport-first rendering

## Goals
Make rendering cost proportional to visible information density.

## Actions
1. Feed candle backend only visible slice + overscan buffer.
2. Separate “resident data” from “render window”.
3. For software path:
   - reduce Python-object geometry churn
   - consider vectorized primitive packing or preallocated geometry buffers
4. For GL path:
   - upload only visible slice unless interaction mode benefits from retained full-VBO
   - if retaining full VBO, avoid CPU repack when only camera changes
5. Add explicit representation tiers:
   - Tier A: full candles
   - Tier B: simplified candles
   - Tier C: OHLC ticks / thin bars
   - Tier D: min-max envelope / density band

## Note
For 1M bars, true 10x often requires switching from “draw every candle” to “draw what each pixel column can represent”.

## Expected gain
- **3x–10x** depending on dataset size and zoom level

---

# Phase 4 — Differential overlays

## Goals
Stop rebuilding overlays that did not change.

## Actions
1. Split overlay invalidation domains:
   - candles dirty
   - markers dirty
   - strategy drawings dirty
   - user drawings dirty
   - camera-only dirty
2. Reuse existing `QGraphicsItem`s where possible.
3. Move from full remove/add to patch updates.
4. Batch strategy overlays similarly to trade markers where practical.
5. Add overlay count telemetry.

## High-priority areas
- `_draw_user_items()`
- `_draw_strategy()`
- trade focus visuals

## Expected gain
- major latency reduction on annotation-heavy charts
- less scene churn and GC pressure

---

# Phase 5 — Pager reconciliation redesign

## Goals
Keep async loading benefits without repeated global rebuilds.

## Actions
1. Replace pager -> `engine.load_data(df)` full-cycle where possible.
2. Introduce incremental dataset merge/update API.
3. Maintain chart-prepared arrays per resident tile or per stitched window.
4. Rebuild pyramid only for affected span or window, not whole dataset.
5. Store sorted int64 times in pager-native form, not only `DatetimeIndex`.

## Risks addressed
- tile landing spikes
- background load causing visible redraw hitching

## Expected gain
- smoother large-history scrolling
- better scaling with tiled data sources

---

# Phase 6 — Vectorized engine throughput

## Goals
Push optimizer and repeated-run throughput much higher.

## Actions
1. Ensure optimizer path always reuses `PreparedDataset`.
2. Move all signal columns to prevalidated NumPy arrays before entering run path.
3. Add a **summary-only mode** / **no-trade-materialization mode** for optimizer sweeps.
4. Defer `Trade` object construction unless user explicitly opens trade details.
5. Replace repeated `pd.Timestamp(...)` in materialization with precomputed int64/epoch arrays.
6. If trade detail is needed, materialize in a second pass or lazy iterator.
7. Review `DrawdownTracker` and summary aggregation for vectorized alternatives.

## Expected gain
- **2x–6x** on optimization workloads with many repeated runs
- much larger gain if current workload is dominated by trade object creation

---

# Phase 7 — Memory & representation strategy for 1M+ bars

## Goals
Avoid “fast but memory-fragile” behavior.

## Actions
1. Prefer compact dtypes where safe:
   - float32 for render-only buffers
   - int32 indices where range allows
2. Keep execution precision separate from render precision.
3. Build pixel-binned / min-max aggregates for extreme zoom-out.
4. Add memory watermark telemetry.
5. Add resident dataset budget rules for pager + chart caches.

---

## Prioritized backlog

## P0 — do first
1. Benchmark baseline and telemetry hardening.
2. Extract chart-prepared immutable arrays from `ChartTab` redraw path.
3. Split `redraw_chart()` into staged coordinator + dirty flags.
4. Introduce summary-only / lazy-trade-materialization mode in vectorized engine.
5. Redesign pager integration to avoid repeated full data reload semantics.

## P1 — next
1. Visible-slice-first GL upload/render policy.
2. Differential overlay updates.
3. O(1) OHLC lookup under LOD.
4. Geometry/object churn reduction in software backend.
5. Better explicit representation tiers for extreme zoom-out.

## P2 — polish / scale-up
1. aggressive memory tuning
2. GPU telemetry surfaced in UI/debug overlay
3. adaptive policies auto-tuned from benchmark data
4. cross-tab shared prepared chart caches where safe

---

## File-level action map

### `gui/chart_tab/tab.py`
**Problems**
- monolithic orchestration
- pandas/DataFrame work in redraw
- coarse overlay invalidation

**Actions**
- extract chart data model
- extract render coordinator
- replace `res_df` copies with array views
- add dirty-flag redraw graph
- isolate camera-only updates from data updates

### `gui/chart_tab/items.py`
**Problems**
- Python-object geometry caches
- linear fallback lookup
- picture regeneration cost

**Actions**
- O(1) OHLC lookup map for visible/rendered + full data
- reduce object churn
- benchmark picture regeneration vs direct primitive path
- refine overscan strategy

### `gui/chart_tab/gl_candle.py`
**Problems**
- full-array pack/upload orientation
- no explicit visible-slice GPU strategy

**Actions**
- benchmark retained full-VBO vs visible-window VBO
- introduce explicit upload policy
- add extreme zoom-out representation path
- expose backend/fallback counters in perf telemetry

### `engine/data/data_pager.py`
**Problems**
- concat/sort on each tile load
- full downstream reload semantics

**Actions**
- cache per-tile prepared arrays
- incremental stitch plan
- reduced-cost resident window extraction
- optional background pre-aggregation per tile

### `engine/execution/prepared_dataset.py`
**Problems**
- good base, but could carry more precomputed representations

**Actions**
- add optional int64 time representation
- add reusable session mask caches keyed by asset/session config
- ensure arrays are contiguous/read-only where beneficial

### `engine/execution/vectorized_engine.py`
**Problems**
- post-loop Python work may dominate
- object materialization cost not optional enough

**Actions**
- lazy/no-trade mode
- precomputed time conversion path
- optimizer-specific result mode
- tighter perf counters around materialization vs execution loop

---

## Suggested KPI dashboard

### Chart KPI
- `data_prepare_ms`
- `array_pack_ms`
- `candles_set_data_ms`
- `candles_gen_ms`
- `candles_paint_ms`
- `markers_ms`
- `strategy_ms`
- `user_draw_ms`
- `camera_ms`
- `bars_total`
- `bars_visible`
- `backend`
- `gl_uploads`
- `viewport_cache_hit`
- `adaptive_lod_applied`

### Vectorized KPI
- `signals_gen_ms`
- `signals_validate_ms`
- `numpy_extract_ms`
- `mask_build_ms`
- `numba_loop_ms`
- `trade_materialize_ms`
- `summary_ms`
- `total_ms`
- `trade_count`
- `used_numba`

---

## What will most likely unlock the first real 10x

If I had to choose the highest-return changes:

1. **Chart**: remove pandas/DataFrame from redraw path and switch to strict viewport/render-window arrays.
2. **Chart**: differential redraw / dirty-flag system so pan/zoom does not rebuild unrelated overlays.
3. **Chart**: pixel-aware representation tiers for extreme zoom-out instead of “still candles, just fewer”.
4. **Vectorized**: lazy trade materialization for optimizer paths.
5. **Pager**: stop routing tile arrivals through full data reload semantics.

These five changes are the most likely to produce real, measurable 10x gains on large datasets.

---

## Recommended implementation order

### Sprint A
- benchmark harness
- perf thresholds
- chart prepared arrays
- vectorized summary-only mode

### Sprint B
- ChartTab decomposition
- dirty flags
- overlay differential redraw

### Sprint C
- visible-window render pipeline
- GL upload policy refinement
- OHLC lookup map

### Sprint D
- pager incremental integration
- per-tile prepared cache
- partial pyramid rebuild / window rebuild

### Sprint E
- extreme zoom-out representation mode
- memory optimization pass
- final benchmark comparison vs baseline

---

## Final recommendation

Архітектурно у вас уже є добра performance foundation, але зараз система виглядає як **кілька сильних optimization ideas, зібраних навколо ще занадто важкого orchestration layer**. Тобто потенціал для 10x є, але він майже точно не буде досягнутий одним “ще трохи підкрутити GL / LOD” кроком.

Правильна стратегія:
- спочатку зафіксувати вимірювання,
- потім відокремити immutable prepared data від frame-time render work,
- потім перейти на viewport-first rendering + differential overlays,
- і окремо спростити optimizer/vectorized path через lazy materialization.

У такому порядку 10x виглядає реалістично, особливо на великих даних.
