# Performance baseline (bench/)

Sprint 1 / item **1.6** ships a small bench harness that times the hot
paths the rest of the roadmap depends on:

| Bench script             | Targets                                                |
|--------------------------|---------------------------------------------------------|
| `bench_lsf.py`           | `linear_pivots_close`, `linear_cisd_series_close`, full LinearStructureFVG vectorised run |
| `bench_llc.py`           | `linear_pivots`, `linear_cisd_series`, full LinearLogicCore vectorised run |
| `bench_grid.py`          | `optimizer.grid_search` — both single-threaded and `n_workers=4` (Sprint 2.6) |

Run the full baseline (≈200k bars, three repeats):

```bash
python -m bench.run_all
```

CI / smoke run (≈5k bars, three repeats — do **not** commit the
result):

```bash
python -m bench.run_all --quick
```

The baseline is checked in as
[`bench/baseline.json`](./baseline.json). Sprint 4 will gate CI on a
`>5%` regression vs the committed baseline; until then the file is
treated as a reference snapshot — refresh it whenever a non-trivial
engine change ships and call out the diff in the PR description.

The baseline is not multi-machine portable: timings are inherently
host-dependent. The committed file therefore records the host
characteristics under `env` so future Devin sessions can detect when a
regression candidate is actually a hardware delta.

## Sprint 2.6 — multiprocessing speed-up target

The roadmap requires **≥3.5× speed-up on a 5×5×5 sweep on a 4-core
machine** for `grid_search(n_workers=4)`. The bench harness records
both `grid_search` (single-threaded) and `grid_search_parallel`
(`n_workers=4`) timings under the `grid` block; the ratio is exposed
as `grid.speedup_median`.

⚠ The 3.5× target is hardware-dependent. Spawn-based pools carry a
fixed ~0.3 s startup cost (Python's `multiprocessing` spawns a fresh
interpreter per worker on macOS / Linux Qt setups), so on a 2-core CI
runner the toy runner is too cheap to amortise spawn cost — the
parallel path can measure as *slower*. That is expected and does not
indicate a regression.

The correctness contract — bit-identical output between
`n_workers=1` and `n_workers=4` — is enforced unconditionally by
`tests/test_optimizer_multiprocessing.py`. The 3.5× speed-up is
verified offline on a 4-core developer machine; the committed
baseline records whatever the host that ran `bench/run_all.py`
measured, so the metric is observable across hardware refreshes.
