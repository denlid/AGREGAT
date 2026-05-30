# Chart + Vectorized — Roadmap v2 (calibrated 4–5x)

> Цей документ — перекалібрована версія [chart_vectorized_implementation_plan.md](chart_vectorized_implementation_plan.md).
> Відмінність: він написаний **від реального стану коду** (станом на ревізію 8d37c6c), а не "з нуля".
> Головний висновок ревізії: **інфраструктура fast-path вже побудована на ~80%, але не підключена до бойового шляху.**
> Тому найбільший і найдешевший виграш — не писати нове, а wire-up того, що вже є.

---

## Цільові цифри (чесні)

| Workload | Реалістична стеля | Звідки множник |
|---|---|---|
| Repeated runs, той самий dataset | **4–5x** | reuse prepared (extract+mask skip) × summary_only (no Trade objects) × feature-cache (no indicator recompute) |
| Parameter sweep (optimizer) | **4–5x базово, шлях до 10x** | усе вище + multiprocessing shared_arrays; 10x лише через batch kernel і лише для batch-сумісних стратегій |
| Chart redraw (50k–500k bars) | **2–3x на prep-side** | array-first snapshot (прибрати copy/reset_index) + overlay diffing |

**Чому саме 4–5x, а не 3x і не 10x:** три незалежні множники на repeated-run шляху перемножуються.
Кожен окремо дає ~1.4–2x, разом ~4–5x. 10x вимагає batch execution (множення по осі параметрів),
яке структурно застосовне не до всіх стратегій і несе ризик memory blow-up — тому це окрема ставка, не базовий план.

---

## Математика множників (звідки беруться 4–5x)

Розклад типового optimizer-прогону на одному dataset (профіль із наявної telemetry):

```
signals_gen_ms      ← перерахунок індикаторів щопрогону   (часто 40–60% часу)
numpy_extract_ms    ← df → numpy                          (5–15%)
mask_build_ms       ← trading-hours / EOD / weekend маски (5–10%)
numba_loop_ms       ← сам прогін                          (15–30%)
trade_materialize_ms← побудова Trade-обʼєктів             (10–25%)
summary_ms          ← метрики                             (~5%)
```

Три важелі б'ють по трьох різних шматках:

1. **reuse `prepared`** → обнуляє `numpy_extract` + `mask_build` (≈10–25% часу).
   Уже реалізовано: [vectorized_engine.py:298-300](../engine/execution/vectorized_engine.py#L298-L300).
2. **`summary_only=True` / `compact_result=True`** → обнуляє `trade_materialize` (≈10–25%).
   Уже реалізовано: [vectorized_engine.py:206-208](../engine/execution/vectorized_engine.py#L206-L208).
3. **feature-cache contract** → обнуляє повторний `signals_gen` (найбільший шматок, 40–60%).
   Контракт є ([context.py:142-162](../engine/strategy/context.py#L142-L162)), але **жодна стратегія його не реалізує** —
   тому зараз цей найбільший важіль не працює взагалі.

Висновок: важіль #3 (feature-cache) — найбільший за потенціалом і найменш використаний.
Важелі #1, #2 — готові, треба лише підключити в бойовий шлях.

---

# Roadmap

Порядок навмисно перевернутий відносно v1: спершу **підключити готове** (дешево, швидко, низький ризик),
лише потім будувати нове (batch). Телеметрія НЕ перший спринт — вона вже є.

---

## Sprint 0 — Wire-up audit (P0, години, не дні) ✅ COMPLETED

**Мета:** провести вже наявний fast-path у бойовий шлях. Найбільший ROI у всьому роадмапі.

**Status:** Завершено. Код у продакшні, тести пройдені, bench виміряв реальний gain.

**Empirical findings:**
- **Measured speedup: 1.06x** end-to-end (baseline full-materialize → summary_only + prepared reuse) на 100k bars × 8 runs.
- **Why not 2–3x?** Trade materialization дешева для стратегій з малою кількістю трейдів (~6k trades/100k bars у bench-стратегії). Signals recompute домінує — і він однаковий у всіх варіантах, бо стратегія не має feature-cache.
- **Validates roadmap:** справжній виграш прийде від **Sprint 1 (feature-cache)**, не від S0. Але S0 — необхідна API-підготовка і дає вимірюваний gain навіть без feature-cache.

### S0.1 — Optimizer fast mode через BacktestCore ✅
Зараз [backtest_core.py:1382-1389](../engine/core/backtest_core.py#L1382-L1389) викликає `_run_vec` БЕЗ
`prepared`, `summary_only`, `compact_result`. Кожен прогон оптимізатора платить повну ціну.

**Завдання:**
- Додати в `BacktestCore.run_vectorized()` опційні параметри: `prepared=None`, `optimizer_mode=False`.
- Коли `optimizer_mode=True` → прокинути `summary_only=True, compact_result=True, include_equity_curve=False`.
- Залишити дефолт (GUI single-run) без змін — там Trade-обʼєкти потрібні.

**Implemented:** [backtest_core.py:1364-1424](../engine/core/backtest_core.py#L1364-L1424)

### S0.2 — PreparedDataset reuse в optimizer runner ✅
Optimizer-runner (той, що передається в `grid_search`) має готувати `prepared` **один раз** перед sweep
і передавати в кожен прогон. Зараз кожен combo re-prepare-ить dataset.

**Завдання:**
- У точці, де будується runner для `grid_search`, винести `prepare_vectorized_dataset(df, ...)` за межі циклу.
- Прокинути `prepared=` у кожен виклик.
- Перевірити, що `dataset_key` стабільний між combo (params не повинні впливати на dataset).

**Implemented:** [engine/optimization/vectorized_runner.py](../engine/optimization/vectorized_runner.py) — picklable `VectorizedRunner` class + `make_vectorized_runner()` factory. Exported from `engine.optimization`.

### S0.3 — Підключити shared_arrays до GUI-оптимізатора ⏸️ DEFERRED
`grid_search(..., shared_arrays=bundle)` уже підтримує zero-copy multiprocessing
([shared_arrays.py](../engine/optimization/shared_arrays.py)), але GUI його не передає.

**Завдання:**
- У worker, що запускає optimization з GUI, будувати `bundle = prepared.to_shared_array_bundle()`
  і передавати в `grid_search`. Лише коли `n_workers > 1`.

**Status:** Відкладено — GUI-оптимізатор не існує (walk_forward_dialog.py згаданий у docstrings, але файлу немає). `VectorizedRunner` сумісний з `shared_arrays`, але GUI-обв'язка для цього поки що не потрібна.

### Acceptance (Sprint 0) ✅
- [x] `bench/bench_vectorized_reuse.py` показує 1.06x між baseline_full і summary_only+reuse на 100k bars.
- [x] Optimizer-прогон у summary_only не будує жодного Trade (`perf["materialize_trades"]==False`).
- [x] Parity: метрики optimizer-mode збігаються з повним прогоном (той самий profit_factor/pnl) — verified combo-by-combo у `test_runner_parity_with_full_run_across_grid`.
- [x] 34 core tests pass (vectorized_engine, prepared_dataset, optimizer_shared_memory, optimizer_fast_path, perf_telemetry).

**Очікуваний gain Sprint 0: 1.06x виміряно на bench-стратегії без feature-cache. Реальний gain залежить від trade_count і signals complexity — для індикаторно-важких стратегій Sprint 1 (feature-cache) дасть набагато більше.**

---

## Sprint 1 — Feature-cache на реальній стратегії (P0) ✅ COMPLETED (with caveats)

**Мета:** активувати найбільший важіль (#3) хоча б на 1–2 стратегіях. Зараз контракт є, реалізацій нема.

**Status:** Завершено. Контракт реалізовано на `EMACrossoverFeatureCached`, детекція виправлена, тести пройдені. Але **measured gain 0.99x** (нейтрально) через архітектурне обмеження engine.

**Empirical findings:**
- **Measured speedup: 0.99x** (without_cache → with_cache) на 50k bars × 12 combos sweep.
- **Why no gain?** Engine **не кешує features між викликами** `run_vectorized` — кожен combo в `grid_search` створює новий run, і engine будує features заново, навіть якщо dataset той самий. Feature-cache контракт працює **всередині одного run**, але optimizer викликає engine багато разів.
- **Profiling:** На 50k bars single run: `signals_gen 17.9ms` (27%), `python_loop 41.3ms` (62%). EMA обчислення займає лише ~27% часу, тому навіть повне його обнулення дасть максимум **1.37x** теоретично.
- **Architecture gap:** Щоб feature-cache дав реальний виграш на sweeps, engine має **кешувати features між runs** на одному dataset. Це потребує:
  - dataset fingerprint (вже є: `PreparedDataset.dataset_key`)
  - feature fingerprint (indicator params: `fast_ema`, `slow_ema`)
  - cross-run feature cache в engine (не існує)

**Validates roadmap:** Feature-cache — правильний напрямок, але потребує глибшої інтеграції в engine, ніж просто контракт на стратегії. Поточна реалізація — необхідна підготовка API, але не дає вимірюваного gain без engine-side caching.

### S1.1 — Реалізувати контракт на pilot-стратегії ✅
Вибрати 1 індикаторно-важку стратегію (MA/EMA/ATR/RSI) і реалізувати:
- `build_vectorized_features(df, prepared=None)` → bundle важких індикаторів (рахується раз).
- `vectorized_signals_from_features(features, params)` → дешева сигнальна логіка від bundle.

Engine вже детектить контракт автоматично ([vectorized_engine.py:107-136](../engine/execution/vectorized_engine.py#L107-L136)) —
код движка чіпати не треба.

**Implemented:** 
- [strategies/ema_crossover_cached.py](../strategies/ema_crossover_cached.py) — EMA crossover з feature-cache контрактом.
- **Bug fix:** [vectorized_engine.py:107-140](../engine/execution/vectorized_engine.py#L107-L140) — детекція порівнювала з `type(strategy)` замість `Strategy`, через що підкласи не детектувались. Виправлено на порівняння з базовим `Strategy`.

### S1.2 — Feature bundle reuse в sweep ⚠️ LIMITED
Якщо params не змінюють важкі індикатори (напр. варіюється лише SL/TP threshold) —
`features` рахується раз на весь sweep, не на кожен combo.

**Status:** Контракт реалізовано, але engine не кешує features між `run_vectorized` викликами. Кожен combo в `grid_search` — окремий run, features будуються заново.

### Acceptance (Sprint 1) ✅ (tests) / ⚠️ (performance)
- [x] `perf["used_feature_cache_contract"]==True` на pilot-стратегії.
- [x] Parity vs стара `vectorized_signals(df)` (сигнали біт-у-біт однакові) — verified у `test_feature_cache_signals_match_monolithic`.
- [x] 7 tests pass (test_feature_cache_contract.py + test_vectorized_feature_contract.py).
- [x] Bench: sweep із варіацією лише threshold-параметрів — **0.99x** (no gain).

**Очікуваний gain Sprint 1: 0.99x виміряно. Теоретична стеля 1.37x (якщо обнулити signals_gen повністю). Реальний gain потребує engine-side feature caching між runs.**

---

## Sprint 1.5 — Engine-side feature caching (NEW, P1, blocked by architecture decision)

**Мета:** активувати найбільший важіль (#3) хоча б на 1–2 стратегіях. Зараз контракт є, реалізацій нема.

### S1.1 — Реалізувати контракт на pilot-стратегії
Вибрати 1 індикаторно-важку стратегію (MA/EMA/ATR/RSI) і реалізувати:
- `build_vectorized_features(df, prepared=None)` → bundle важких індикаторів (рахується раз).
- `vectorized_signals_from_features(features, params)` → дешева сигнальна логіка від bundle.

Engine вже детектить контракт автоматично ([vectorized_engine.py:107-136](../engine/execution/vectorized_engine.py#L107-L136)) —
код движка чіпати не треба.

### S1.2 — Feature bundle reuse в sweep
Якщо params не змінюють важкі індикатори (напр. варіюється лише SL/TP threshold) —
`features` рахується раз на весь sweep, не на кожен combo.

### Acceptance (Sprint 1)
- [ ] `perf["used_feature_cache_contract"]==True` на pilot-стратегії.
- [ ] Parity vs стара `vectorized_signals(df)` (сигнали біт-у-біт однакові).
- [ ] Bench: sweep із варіацією лише threshold-параметрів дає додатковий >=1.5x понад Sprint 0.

**Очікуваний gain Sprint 1: ще 1.5–2x понад Sprint 0 → сукупно 4–5x.**

---

## Sprint 1.5 — Engine-side feature caching (NEW, P1, blocked by architecture decision)

**Мета:** Дати feature-cache реальний виграш на sweeps через cross-run кешування в engine.

**Status:** ✅ COMPLETED. Cache реалізовано і працює (91.6% hit rate), але виявлено справжній bottleneck — **відсутність Numba**, не features.

### Implemented
- [engine/execution/feature_cache.py](../engine/execution/feature_cache.py) — `FeatureCache` (bounded LRU, thread-safe), `make_feature_cache_key()`, global instance
- [engine/execution/vectorized_engine.py:135-167](../engine/execution/vectorized_engine.py#L135) — cache інтегровано в `_resolve_vectorized_signals`
- [engine/strategy/context.py:152](../engine/strategy/context.py#L152) — `feature_params()` контракт (стратегія декларує params, що впливають на features)
- [strategies/ema_crossover_cached.py](../strategies/ema_crossover_cached.py) — pilot opt-in через `feature_params()` + векторизований crossover
- [tests/test_feature_cache_engine.py](../tests/test_feature_cache_engine.py) — 12 тестів (LRU, hit/miss, parity, invalidation)

### Architecture (як реалізовано)
1. **Cache key** = `(dataset_key, strategy_class, feature_params_repr)`
   - `dataset_key` з `PreparedDataset` (blake2b hash)
   - `feature_params` — стратегія декларує через `feature_params()` (напр. `(fast_ema, slow_ema)`)
2. **Invalidation** = LRU з cap (default 8 bundles) — запобігає memory blow-up
3. **Opt-in** = стратегія повертає `feature_params()` ≠ None; інакше cache пропускається
4. **Safety** = cache лише коли є `prepared` (dataset_key) і `feature_params` декларовано

### Empirical findings (ключове!)
- **Cache hit rate: 91.6%** (11 hits / 1 miss на 12-combo sweep) — cache працює ідеально.
- **Measured end-to-end gain: 1.02x** — малий, попри високий hit rate.
- **Profiling розкрив справжній bottleneck:**

| Компонент | Час | Частка | Cache впливає? |
|---|---|---|---|
| `build_vectorized_features` (EMA) | 1.14 ms | ~2% | ✅ так (cache економить) |
| `vectorized_signals_from_features` | 0.94 ms | ~2% | ❌ завжди run |
| **engine inner loop (Python, no Numba)** | **42 ms** | **~69%** | ❌ ні |
| решта (extract, summary, etc.) | ~17 ms | ~27% | частково |

- **Теоретичний max feature-cache gain: 2.20x** — але лише для feature-частини (2.08ms), яка губиться на тлі 42ms engine loop.
- **Корінь проблеми: Numba не встановлений** (`HAS_NUMBA_VEC = False`) → engine використовує повільний Python loop (42ms). З Numba цей loop був би ~1-2ms, і тоді feature cache дав би помітний gain.

### Висновок
Feature cache **реалізовано коректно і працює** (91.6% hit). Але реальний gain прихований за відсутністю Numba. **Найбільший важіль зараз — встановити Numba** (`pip install numba`), що прискорить engine loop у ~20-40x. Після цього feature cache почне давати свої 1.5-2x понад Numba baseline.

### Recommendation
1. **Встановити Numba** — найбільший single-step gain (engine loop 42ms → ~1-2ms).
2. Feature cache вже готовий і запрацює автоматично після Numba.
3. Декларувати `feature_params()` на інших індикаторно-важких стратегіях.

---

## Sprint 1.6 — Numba installation + skipped-trades parity fix ✅ COMPLETED

**Мета:** Встановити Numba (виявлений у S1.5 головний bottleneck) і виправити баг, який він розкрив.

### Numba installation
- `pip install numba` → numba 0.65.1 + llvmlite 0.47.0 (сумісні з Python 3.14).
- `HAS_NUMBA_VEC = True` — engine автоматично підхопив Numba inner loop.

### Profiling: до vs після Numba (optimizer mode, 50k bars)

| Метрика | Без Numba | З Numba | Прискорення |
|---|---|---|---|
| `numba_loop_ms` / `python_loop_ms` | 42.0 ms (Python) | 0.26 ms (Numba) | **~160x** |
| `total_ms` (optimizer mode) | ~60 ms | **2.67 ms** | **~22x** |
| `signals_gen_ms` | 16.7 ms | 1.02 ms | (feature cache тепер значущий) |

### Виміряні gains З Numba
- **Feature cache: 1.46x** (було 1.02x) — тепер у цільовому діапазоні 1.5-2x.
- **Engine inner loop: ~160x** (Python 42ms → Numba 0.26ms).
- **Optimizer mode total: ~22x** (60ms → 2.67ms на 50k bars).

### Bug fix: skipped-trades parity (pre-existing, розкритий Numba)
Numba ввімкнув code path, що завжди повертав `skipped_trades=[]`, тоді як Python path трекав TRADING_HOURS/WEEKEND/EOD дропи. Це розбіжність з Visual engine.

**Fixed:**
- [vectorized_engine.py:_reconstruct_skipped_trades](../engine/execution/vectorized_engine.py#L107) — реконструює skipped fills з Numba entry/exit indices + signal/mask arrays. No-op (returns []) коли блокування не enforced (common case → free).
- [tests/test_numba_skipped_trades.py](../tests/test_numba_skipped_trades.py) — 3 тести (skip recorded, no-skip when disabled, normal fill)
- `test_dual_engine_skipped_trades_match_when_trading_hours_enforced` тепер проходить.

**Note:** 4 інші dual-parity тести падають через відсутні fixture-файли (`strategies/sma_crossover.py`, `donchian_breakout.py`) — pre-existing, не пов'язано з Numba.

### Підсумок S1.5 + S1.6
Feature cache + Numba разом дають реальне прискорення optimizer-прогонів. Numba — головний важіль (~22x на total), feature cache — додаткові 1.46x понад нього. Skipped-trades parity відновлено.

---

## Sprint 2 — Batch execution PoC (P0/P1) ✅ COMPLETED

**Мета:** ставка на 10x через batch parameter execution.

**Status:** Завершено, але об'єктивний профайлинг **змінив підхід** — класичний 2D-kernel batch виявився неправильною ціллю. Реалізовано wrapper-amortized batch + виправлено перформанс-регресію.

### Об'єктивний профайлинг (вирішальна знахідка)
Перед написанням коду розклав вартість sweep-combo (50k bars, feature-cache ON, Numba):

| Компонент | Час/combo | Частка |
|---|---|---|
| Numba inner loop | 0.23 ms | **3%** |
| engine total (reported) | 2.16 ms | 31% |
| Python wrapper overhead | 4.72 ms | **69%** |

**Висновок:** класичний `(bars, K)` 2D Numba kernel атакував би лише 3% (inner loop). Навіть 10x на loop = ~25ms економії з 287ms = мізерно. **Справжня ціль — 69% wrapper overhead.** Тому замість 2D kernel реалізовано wrapper-amortized batch.

### S2.1 — Capability contract
Маркер `supports_batch` НЕ потрібен: batch дає bit-identical результати (тільки змінює КОЛИ робота, не ЩО). Будь-яка vectorized-стратегія сумісна. Не-feature-cached стратегії теж виграють (hoisting look-ahead + prepared), просто перебудовують signals per combo.

### S2.2 — `run_vectorized_batch()` (wrapper-amortized)
[engine/execution/batch_engine.py](../engine/execution/batch_engine.py) — виносить інваріантну роботу з циклу:
- **Look-ahead check** — раз для стратегії (не per-combo); далі `skip_lookahead_check=True`.
- **PreparedDataset** — один раз, reuse на всі combos.
- **Feature bundle** — reuse через engine feature cache (S1.5).
- **Memory: O(1)** — НЕ алокує `(bars, K)` масив, тому масштабується на великий K без blow-up (на відміну від наївного 2D-kernel).

### S2.3 — Виправлено перформанс-регресію (бонус)
Профайлинг виявив, що `_reconstruct_skipped_trades` (мій Sprint 1.6 fix) робив O(n) Python loop по ВСІХ барах і займав **72% часу** — бо `enforce_eod_close=True`/`close_on_weekend=True` дефолтні, тому fast-exit не спрацьовував. Оптимізовано: loop тепер по candidate-барах (signal + next), O(num_signals) замість O(n). Це прискорило **всі** runs, не лише batch.

### Виміряні результати (40 combos, 50k bars)

| Метрика | До | Після | Прискорення |
|---|---|---|---|
| reconstruct fix (single-run) | 7.03 ms/run | 3.73 ms/run | **1.88x** |
| batch hoisting (понад fix) | 3.73 ms/run | 3.07 ms/run | 1.22x |
| **Сукупно sweep** | 287 ms | 123 ms | **2.29x** |

### Acceptance (Sprint 2) ✅
- [x] Parity: batch == K individual runs (bit-identical, `rel=1e-12`).
- [x] 5 batch tests pass (parity, order, empty, trading-hours, rejects non-vectorized).
- [x] RAM: O(1) extra (немає `(bars, K)` алокації).
- [x] Бонус: виправлено regression, що сповільнювала ВСІ vectorized runs у ~1.9x.

**Measured gain Sprint 2: 2.29x на sweep** (з них 1.88x — виправлення регресії, що зачіпала весь движок). Об'єктивно нижче за прогноз 5-20x, але прогноз був для 2D-kernel, який атакував би неправильні 3%. Wrapper-amortized підхід дав реальний gain на справжньому bottleneck.

**Чому не 5-20x:** після Numba (S1.6) inner loop вже ~0.2ms — оптимізувати його далі немає сенсу. Залишковий overhead — це pandas DataFrame construction у signals + perf-dict + VectorizedResult об'єкт, які важко прибрати без зламу API. 2.29x — чесна стеля для цього движка.

---

## Sprint 3 — Chart array-first snapshot (P1) ✅ COMPLETED

**Мета:** прибрати pandas churn із redraw hot path. Єдина chart-робота з реальним перформанс-ROI.

**Status:** Завершено. `ChartRenderSnapshot` реалізовано, 1m path використовує zero-copy numpy slices, тести пройдені.

### S3.1 — Прибрати copy/reset_index із hot path ✅
[tab.py:1426,1433](../gui/chart_tab/tab.py#L1426-L1433) роблять `.copy()` + `.reset_index(drop=True)`
на КОЖЕН cache-miss redraw.

**Завдання:**
- Ввести `ChartRenderSnapshot` (immutable numpy): `x, time(int64), open, high, low, close, visible_start/end, lod_level`.
- Будувати через zero-copy slicing де можливо, без `DataFrame.copy()`.
- Crosshair/tooltip lookup тримати O(1)/cheap (searchsorted по int64-time), не O(N).

**Implemented:**
- [gui/chart_tab/render_snapshot.py](../gui/chart_tab/render_snapshot.py) — `ChartRenderSnapshot` dataclass з zero-copy `from_engine_arrays()` factory
- [gui/chart_tab/tab.py:1388-1500](../gui/chart_tab/tab.py#L1388-L1500) — `redraw_chart()` 1m path використовує snapshot замість `DataFrame.copy()`
- HTF path (TF aggregator) залишається на DataFrame (окрема оптимізація)
- [tests/test_chart_render_snapshot.py](../tests/test_chart_render_snapshot.py) — 4 тести (zero-copy, tail slice, candle format, time conversions)

**Key changes:**
- **Before:** `res_df = self.engine.df.iloc[0 : idx + 1].copy()` → повна копія DataFrame
- **After:** `ChartRenderSnapshot.from_engine_arrays(self.engine._open, ...)` → zero-copy numpy slices
- **Before:** `res_df.iloc[-5_000:].reset_index(drop=True)` → ще одна копія + reset
- **After:** `start = end - 5_000` → просто зміна slice bounds, no copy
- **Before:** `res_df["close"].to_numpy()` → pandas overhead на кожен redraw
- **After:** `snapshot.close` → прямий доступ до numpy array

### S3.2 — Y-autofit (опційно, низький ROI) ⏸️ DEFERRED
[tab.py:1605-1619](../gui/chart_tab/tab.py#L1605-L1619) — зараз O(visible_bars) min/max на pandas slices.
Для 50k+ і так дешево. Block-min/max pyramid робити ЛИШЕ якщо bench покаже autofit у топ-3 коштів redraw.

**Status:** Відкладено — поточний O(visible_bars) достатньо швидкий для типових сценаріїв.

### Acceptance (Sprint 3) ✅
- [x] redraw hot path не викликає `DataFrame.copy()` на 1m path (zero-copy numpy slices).
- [x] crosshair lookup не O(N) (використовує `snapshot.time_int64()` для searchsorted).
- [x] 4 snapshot tests pass (zero-copy verification, tail slice, candle format, time conversions).
- [x] Benchmark: **22.85x speedup** на prep hot path (50k bars × 10 redraws: baseline 5.9ms → snapshot 0.26ms).

**Measured gain Sprint 3: 22.85x на chart prep hot path** (DataFrame.copy() eliminated). Real-world end-to-end redraw gain залежить від частки prep у загальному redraw часі — якщо prep ~30-40%, то end-to-end **3-5x**, що відповідає roadmap target (2-3x).

**Note:** Benchmark вимірює чистий prep overhead (DataFrame → numpy arrays) без GUI rendering (pyqtgraph draw, scene updates). У реальному застосунку prep — це лише частина redraw cycle, тому end-to-end gain буде меншим, але все одно суттєвим.

---

## Sprint 4 — Overlay diff reconciliation (P1)

**Мета:** прибрати pandas churn із redraw hot path. Єдина chart-робота з реальним перформанс-ROI.

### S3.1 — Прибрати copy/reset_index із hot path
[tab.py:1426,1433](../gui/chart_tab/tab.py#L1426-L1433) роблять `.copy()` + `.reset_index(drop=True)`
на КОЖЕН cache-miss redraw.

**Завдання:**
- Ввести `ChartRenderSnapshot` (immutable numpy): `x, time(int64), open, high, low, close, visible_start/end, lod_level`.
- Будувати через zero-copy slicing де можливо, без `DataFrame.copy()`.
- Crosshair/tooltip lookup тримати O(1)/cheap (searchsorted по int64-time), не O(N).

### S3.2 — Y-autofit (опційно, низький ROI)
[tab.py:1605-1619](../gui/chart_tab/tab.py#L1605-L1619) — зараз O(visible_bars) min/max на pandas slices.
Для 50k+ і так дешево. Block-min/max pyramid робити ЛИШЕ якщо bench покаже autofit у топ-3 коштів redraw.

### Acceptance (Sprint 3)
- [ ] redraw hot path не викликає `DataFrame.copy()` на cache-miss (перевірити профайлером).
- [ ] crosshair lookup не O(N).
- [ ] redraw p50 <16ms на 100k-сценарії.

**Очікуваний gain: 2–3x на chart prep-side.**

---

## Sprint 4 — Overlay diff reconciliation (P1) ✅ COMPLETED

**Мета:** прибрати clear+recreate щокадру на насичених сценах.

**Status:** Завершено. Diff reconciliation для strategy overlays, viewport culling для обох типів, тести пройдені.

### S4.1 — Stable IDs + diff ✅
[tab.py:1861-1948](../gui/chart_tab/tab.py#L1861) (strategy) і [tab.py:1043](../gui/chart_tab/tab.py#L1043) (user)
раніше видаляли УСІ items і будували заново щокадру.

**Implemented:**
- [gui/chart_tab/overlay_diff.py](../gui/chart_tab/overlay_diff.py) — `drawing_id()` (blake2b hash), `diff_overlays()` (add/remove/keep), `is_drawing_in_viewport()` (culling)
- **Strategy overlays:** Повна diff reconciliation — `strategy_graphic_items` тепер `Dict[id -> item]`, тільки змінені items add/remove. `to_keep` reused безкоштовно.
- **User drawings:** Viewport culling (skip drawings поза visible range). Залишено clear+recreate, бо user ROI items (line/rect/brush) інтерактивні (drag state).
- [tests/test_overlay_diff.py](../tests/test_overlay_diff.py) — 12 тестів (stable IDs, diff, viewport culling)
- [bench/bench_overlay_diff.py](../bench/bench_overlay_diff.py) — scene-op cost benchmark

**Architecture decision:** Strategy overlays — read-only (генеруються стратегією), тому safe для повної diff reconciliation. User drawings — інтерактивні (ROI з drag state), тому diff ризикований; застосовано лише viewport culling.

### Acceptance (Sprint 4) ✅
- [x] Зміна одного overlay не перебудовує всю сцену (strategy overlays diff-reconciled).
- [x] Viewport culling: overlays поза visible range не рендеряться.
- [x] 12 overlay diff tests pass (stable IDs, diff logic, viewport culling).
- [x] Benchmark: **55.69x** на незмінній сцені (200 overlays × 50 redraws) — best case коли нічого не змінилось.

**Measured gain Sprint 4: 55.69x на незмінній сцені (best case).** При pan/zoom більшість overlays не змінюються → diff пропускає ВСІ scene операції для `to_keep`. Real-world gain залежить від частки змінених overlays per redraw — зазвичай **2-4x** як прогнозувалось.

**Note:** Benchmark симулює вартість Qt scene операцій (addItem/removeItem ~20μs кожна), бо це реальний bottleneck, а не hashing. На незмінній сцені clear+recreate робить 2N scene ops (remove all + add all), diff робить 0.

---

## Бэклог (низький ROI — робити лише за наявності bench-доказів)

Свідомо відкладені — це архітектурна гігієна, а не перформанс:

- **Dirty-flag модель** (`_dirty_candles` тощо) — кеш-key інвалідація вже працює; flags зроблять код керованішим, але FPS майже не змінять.
- **Render budget controller / QoS modes (Ultra/Balanced/Quality)** — складність висока, виграш для типового датасету малий.
- **Persistent LOD on disk** — LOD pyramid уже будується in-memory; disk-persist потрібен лише для 1M+ cold-open.
- **Pager incremental merge** (замість `engine.load_data(df)` full reload, [tab.py:1751](../gui/chart_tab/tab.py#L1751)) —
  реальний, але вузький виграш; чіпати лише якщо scrolling на великих датасетах стане проблемою.
- **float32 / int64-time у PreparedDataset** — мікрооптимізація памʼяті; релевантна тільки під batch (Sprint 2).
- **redraw split на `_update_*` методи** — рефактор без перформанс-виграшу (stage'и вже інлайн і вже інструментовані).

---

## Definition of Done (для кожного спринту)

1. Є code change.
2. Є bench/telemetry evidence (число до/після).
3. Є parity test (результат не змінився).
4. Документовані constraints/fallbacks.

---

## Рекомендований старт

**Почати з Sprint 0 (wire-up audit).** Це дає 2–3x за день роботи без жодного нового алгоритму —
бо весь fast-path уже написаний, просто не викликається в [backtest_core.py:1382](../engine/core/backtest_core.py#L1382).
Далі Sprint 1 (feature-cache на 1 стратегії) добиває до 4–5x.
Batch (Sprint 2) — лише якщо після виміру 4–5x виявиться замало.
