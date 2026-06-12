r"""Line-level profiler for the visual headless engine path.

This runner intentionally lives outside ``engine/*``. It profiles the existing
``BacktestCore.run_visual_headless()`` call chain and writes structured JSON
with per-line timings for engine code, API wrappers, strategy callbacks,
indicators, data views, HTF aggregation, execution helpers, and models.

Examples
--------
    .\.venv\Scripts\python.exe bench\profile_visual_headless_line.py
    .\.venv\Scripts\python.exe bench\profile_visual_headless_line.py --bars 50000
    .\.venv\Scripts\python.exe bench\profile_visual_headless_line.py --data data.csv
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import linecache
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from line_profiler import LineProfiler
except ImportError as exc:  # pragma: no cover - depends on local env
    raise SystemExit(
        "line_profiler is not installed. Install it into the active env, e.g. "
        r".\.venv\Scripts\python.exe -m pip install line_profiler"
    ) from exc

from bench._synthetic import synthetic_ohlcv
from engine.core.backtest_core import BacktestCore
from engine.data.data_loader import DataLoader
from engine.strategy.context import StrategyLoader


HEADLESS_MODULES = (
    "engine.core.backtest_core",
    "engine.core.data_view",
    "engine.data.htf_view",
    "engine.data.tf_aggregator",
    "engine.strategy.context",
    "engine.api",
    "engine.core.indicators",
    "engine.execution.execution",
    "engine.models.models",
    "engine.analytics.stats_accumulator",
)


def _load_frame(path: Path | None, bars: int, seed: int) -> pd.DataFrame:
    if path is None:
        return synthetic_ohlcv(bars, seed=seed, pascal_case=False)
    ext = path.suffix.lower()
    if ext in (".parquet", ".pq"):
        return DataLoader.load_parquet(str(path))
    if ext in (".feather", ".ipc"):
        return DataLoader.load_feather(str(path))
    return DataLoader.load_csv(str(path), use_cache=False)


def _iter_module_callables(module: ModuleType) -> Iterable[FunctionType]:
    for _, obj in inspect.getmembers(module):
        if inspect.isfunction(obj):
            yield obj
            continue
        if not inspect.isclass(obj):
            continue
        for _, member in inspect.getmembers(obj):
            if inspect.isfunction(member):
                yield member
            elif isinstance(member, (staticmethod, classmethod)):
                func = member.__func__
                if inspect.isfunction(func):
                    yield func
            elif isinstance(member, property) and inspect.isfunction(member.fget):
                yield member.fget


def _iter_closure_callables(func: FunctionType) -> Iterable[FunctionType]:
    seen: set[int] = set()

    def walk(value: Any) -> Iterable[FunctionType]:
        obj_id = id(value)
        if obj_id in seen:
            return
        seen.add(obj_id)
        if inspect.isfunction(value):
            yield value
            closure = value.__closure__ or ()
            for cell in closure:
                try:
                    cell_value = cell.cell_contents
                except ValueError:
                    continue
                yield from walk(cell_value)
        elif isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from walk(item)

    yield from walk(func)


def _is_project_function(func: FunctionType) -> bool:
    try:
        filename = Path(inspect.getsourcefile(func) or "").resolve()
    except (OSError, RuntimeError):
        return False
    try:
        filename.relative_to(ROOT)
    except ValueError:
        return False
    return True


def _add_function(
    profiler: LineProfiler,
    func: FunctionType,
    added: dict[str, str],
    *,
    reason: str,
) -> None:
    if not _is_project_function(func):
        return
    try:
        filename = inspect.getsourcefile(func) or ""
        line = inspect.getsourcelines(func)[1]
    except (OSError, TypeError):
        return
    key = f"{Path(filename).resolve()}:{line}:{func.__qualname__}"
    if key in added:
        return
    profiler.add_function(func)
    added[key] = reason


def _register_profile_targets(
    profiler: LineProfiler,
    strategy: Any,
    extra_modules: Iterable[str],
) -> dict[str, str]:
    added: dict[str, str] = {}
    for module_name in (*HEADLESS_MODULES, *extra_modules):
        module = importlib.import_module(module_name)
        for func in _iter_module_callables(module):
            _add_function(profiler, func, added, reason=module_name)

    strategy_cls = strategy.__class__
    for _, member in inspect.getmembers(strategy_cls):
        if inspect.isfunction(member):
            _add_function(profiler, member, added, reason="strategy_class")
            for callback in _iter_closure_callables(member):
                _add_function(profiler, callback, added, reason="strategy_closure")

    return added


def _stats_to_json(
    profiler: LineProfiler,
    added: dict[str, str],
    run_meta: dict[str, Any],
) -> dict[str, Any]:
    stats = profiler.get_stats()
    unit = float(stats.unit)
    functions: list[dict[str, Any]] = []
    for key, timings in stats.timings.items():
        filename, start_line, func_name = key
        abs_file = str(Path(filename).resolve())
        total_time = sum(time_value for _, _, time_value in timings) * unit
        lines = []
        for lineno, hits, time_value in timings:
            seconds = float(time_value * unit)
            lines.append(
                {
                    "line": int(lineno),
                    "hits": int(hits),
                    "time_seconds": seconds,
                    "per_hit_seconds": seconds / hits if hits else 0.0,
                    "code": linecache.getline(filename, lineno).rstrip("\n"),
                }
            )
        functions.append(
            {
                "file": os.path.relpath(abs_file, ROOT),
                "abs_file": abs_file,
                "start_line": int(start_line),
                "function": func_name,
                "registered_reason": added.get(
                    f"{abs_file}:{start_line}:{func_name}",
                    "",
                ),
                "total_time_seconds": float(total_time),
                "lines": sorted(lines, key=lambda item: item["line"]),
            }
        )
    functions.sort(key=lambda item: item["total_time_seconds"], reverse=True)
    return {
        "schema": "visual_headless_line_profile.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "timer_unit_seconds": unit,
        "run": run_meta,
        "registered_function_count": len(added),
        "profiled_function_count": len(functions),
        "functions": functions,
        "top_functions": [
            {
                "file": item["file"],
                "function": item["function"],
                "total_time_seconds": item["total_time_seconds"],
            }
            for item in functions[:30]
        ],
    }


def profile_visual_headless(args: argparse.Namespace) -> dict[str, Any]:
    data_path = Path(args.data).resolve() if args.data else None
    strategy_path = Path(args.strategy).resolve()
    output_path = (
        Path(args.output).resolve()
        if args.output
        else ROOT
        / "logs"
        / f"visual_headless_line_profile_{datetime.now():%Y%m%d_%H%M%S}.json"
    )

    df = _load_frame(data_path, bars=args.bars, seed=args.seed)
    if args.limit_bars is not None:
        df = df.iloc[: args.limit_bars].reset_index(drop=True)

    strategy = StrategyLoader.load(str(strategy_path))
    core = BacktestCore()
    core.load_data(df)
    core.load_strategy(strategy)

    extra_modules: list[str] = []
    profiler = LineProfiler()
    added = _register_profile_targets(profiler, strategy, extra_modules)

    started = time.perf_counter()
    error: str | None = None
    result: Any = None
    profiler.enable_by_count()
    try:
        result = core.run_visual_headless()
    except Exception as exc:  # Keep partial line stats for failing strategies.
        error = repr(exc)
    finally:
        profiler.disable_by_count()
    elapsed = time.perf_counter() - started

    run_meta = {
        "strategy": str(strategy_path.relative_to(ROOT))
        if strategy_path.is_relative_to(ROOT)
        else str(strategy_path),
        "data": str(data_path) if data_path is not None else "synthetic",
        "bars": int(len(df)),
        "elapsed_seconds": float(elapsed),
        "bars_per_second": float(len(df) / elapsed) if elapsed > 0 else 0.0,
        "trades": int(len(core.trades)),
        "drawings": int(len(core.drawings)),
        "final_equity": float(core.equity),
        "result_summary": getattr(result, "summary", None),
        "error": error,
    }
    payload = _stats_to_json(profiler, added, run_meta)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"output": str(output_path), **run_meta, "profiled_function_count": payload["profiled_function_count"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default=str(ROOT / "strategies" / "15_1.py"))
    parser.add_argument("--data", default=None, help="Optional CSV/Parquet/Feather OHLCV file.")
    parser.add_argument("--bars", type=int, default=25_000, help="Synthetic bars when --data is omitted.")
    parser.add_argument("--limit-bars", type=int, default=None, help="Trim loaded data before profiling.")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    summary = profile_visual_headless(args)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
