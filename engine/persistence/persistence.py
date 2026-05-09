"""
Persistence — Save/load engine state to a portable `.agbk` archive.

`.agbk` is a zip containing:
    meta.json     — version, asset, equity, costs, settings snapshot
    trades.csv    — closed trades (round-trip safe ISO-8601 timestamps)
    equity.csv    — equity curve (time → equity)
    drawings.json — chart annotations
    settings.json — chart preset / theme / font / etc. (best-effort)

Loading reconstructs `engine.trades`, `engine.equity`, `engine.max_*`,
`engine.drawings`, and `engine.execution_model` so resuming a session
picks up exactly where it left off — without re-running the simulation.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import zipfile
from datetime import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from engine.persistence.integrity import (
    IntegrityError,
    hash_archive_payload,
    verify_archive,
)
from engine.models.models import AssetConfig, Trade

if TYPE_CHECKING:
    from engine.backtest_engine import BacktestEngine

logger = logging.getLogger("StrategyTester.persistence")

# Legacy ``version`` field still written for backwards-compat with
# pre-Sprint-2 readers. ``format_version`` is the new gate; bump it
# when the on-disk layout changes (Sprint 3 will introduce a
# migration framework that uses it).
AGBK_VERSION = 1
AGBK_FORMAT_VERSION = 1
AGBK_EXTENSION = ".agbk"


def _time_to_str(t: time) -> str:
    """Serialise a :class:`datetime.time` as ``HH:MM:SS`` for ``meta.json``."""
    return t.strftime("%H:%M:%S")


def _str_to_time(s: Any, fallback: time) -> time:
    """Inverse of :func:`_time_to_str`. Accepts ``HH:MM`` or ``HH:MM:SS``."""
    if isinstance(s, time):
        return s
    if not isinstance(s, str) or not s:
        return fallback
    try:
        parts = s.split(":")
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
        if len(parts) >= 3:
            return time(int(parts[0]), int(parts[1]), int(parts[2]))
    except (TypeError, ValueError):
        pass
    return fallback


def _asset_to_dict(asset: AssetConfig) -> Dict[str, Any]:
    """Serialise an :class:`AssetConfig` so :func:`_dict_to_asset` can rebuild it."""
    return {
        "name": asset.name,
        "point_value": asset.point_value,
        "tick_size": asset.tick_size,
        "buffer": asset.buffer,
        "max_lots_per_100k": asset.max_lots_per_100k,
        "currency": asset.currency,
        "min_lot": asset.min_lot,
        "lot_step": asset.lot_step,
        "spread_points": asset.spread_points,
        "commission_per_lot": asset.commission_per_lot,
        "slippage_points": asset.slippage_points,
        "trading_hours_start": _time_to_str(asset.trading_hours_start),
        "trading_hours_end": _time_to_str(asset.trading_hours_end),
        "eod_close": _time_to_str(asset.eod_close),
        "enforce_trading_hours": asset.enforce_trading_hours,
        "enforce_eod_close": asset.enforce_eod_close,
        "close_on_weekend": asset.close_on_weekend,
    }


def _dict_to_asset(data: Dict[str, Any], fallback: AssetConfig) -> AssetConfig:
    """Inverse of :func:`_asset_to_dict`. Missing keys fall back to ``fallback``."""
    return AssetConfig(
        name=str(data.get("name", fallback.name)),
        point_value=float(data.get("point_value", fallback.point_value)),
        tick_size=float(data.get("tick_size", fallback.tick_size)),
        buffer=float(data.get("buffer", fallback.buffer)),
        max_lots_per_100k=int(
            data.get("max_lots_per_100k", fallback.max_lots_per_100k)
        ),
        currency=str(data.get("currency", fallback.currency)),
        min_lot=float(data.get("min_lot", fallback.min_lot)),
        lot_step=float(data.get("lot_step", fallback.lot_step)),
        spread_points=float(data.get("spread_points", fallback.spread_points)),
        commission_per_lot=float(
            data.get("commission_per_lot", fallback.commission_per_lot)
        ),
        slippage_points=float(data.get("slippage_points", fallback.slippage_points)),
        trading_hours_start=_str_to_time(
            data.get("trading_hours_start"), fallback.trading_hours_start
        ),
        trading_hours_end=_str_to_time(
            data.get("trading_hours_end"), fallback.trading_hours_end
        ),
        eod_close=_str_to_time(data.get("eod_close"), fallback.eod_close),
        enforce_trading_hours=bool(
            data.get("enforce_trading_hours", fallback.enforce_trading_hours)
        ),
        enforce_eod_close=bool(
            data.get("enforce_eod_close", fallback.enforce_eod_close)
        ),
        close_on_weekend=bool(data.get("close_on_weekend", fallback.close_on_weekend)),
    )


def _trade_to_row(t: Trade) -> Dict[str, Any]:
    """Stable column order for trades.csv."""
    return {
        "id": t.id,
        "direction": t.direction,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "pnl": t.pnl,
        "pnl_points": t.pnl_points,
        "entry_index": t.entry_index,
        "exit_index": t.exit_index,
        "entry_time": pd.Timestamp(t.entry_time).isoformat(),
        "exit_time": pd.Timestamp(t.exit_time).isoformat(),
        "volume": t.volume,
        "duration_minutes": t.duration_minutes,
        "exit_reason": t.exit_reason,
        "mae": t.mae,
        "mfe": t.mfe,
        "mae_dollars": getattr(t, "mae_dollars", 0.0),
        "mfe_dollars": getattr(t, "mfe_dollars", 0.0),
        "session": t.session,
        "initial_sl": getattr(t, "initial_sl", 0.0),
        "rr": getattr(t, "rr", None),
        "tag": getattr(t, "tag", ""),
        "commission": getattr(t, "commission", 0.0),
        "spread_cost": getattr(t, "spread_cost", 0.0),
        "slippage_cost": getattr(t, "slippage_cost", 0.0),
    }


def _row_to_trade(row: Dict[str, str]) -> Trade:
    def _f(name: str, default: float = 0.0) -> float:
        v = row.get(name)
        if v is None or v == "" or v == "None":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _i(name: str, default: int = 0) -> int:
        v = row.get(name)
        if v is None or v == "":
            return default
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    rr_raw = row.get("rr")
    rr: Optional[float] = None
    if rr_raw not in (None, "", "None") and rr_raw is not None:
        try:
            rr = float(rr_raw)
        except (TypeError, ValueError):
            rr = None

    return Trade(
        id=_i("id"),
        direction=row.get("direction", "long"),
        entry_price=_f("entry_price"),
        exit_price=_f("exit_price"),
        pnl=_f("pnl"),
        pnl_points=_f("pnl_points"),
        entry_index=_i("entry_index"),
        exit_index=_i("exit_index"),
        entry_time=row.get("entry_time", ""),
        exit_time=row.get("exit_time", ""),
        volume=_f("volume", 1.0),
        duration_minutes=_i("duration_minutes"),
        exit_reason=row.get("exit_reason", ""),
        mae=_f("mae"),
        mfe=_f("mfe"),
        mae_dollars=_f("mae_dollars"),
        mfe_dollars=_f("mfe_dollars"),
        session=row.get("session", ""),
        initial_sl=_f("initial_sl"),
        rr=rr,
        tag=row.get("tag", ""),
        commission=_f("commission"),
        spread_cost=_f("spread_cost"),
        slippage_cost=_f("slippage_cost"),
    )


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
def save_session(engine: "BacktestEngine", path: str) -> None:
    """Save the current engine state to `path` (`.agbk` extension auto-added).

    Sprint 2.2 / 2.3 / 2.4: ``meta.json`` carries ``format_version``,
    ``agbk_sha256`` (computed below over the canonicalised archive
    payload), ``input_csv_sha256`` / ``input_parquet_sha256`` (when the
    engine knows the input file), and ``seeds`` (when set via
    :meth:`BacktestCore.set_seeds`).
    """
    if not path.endswith(AGBK_EXTENSION):
        path += AGBK_EXTENSION

    asset = engine.asset_config
    exec_model = getattr(engine, "execution_model", None)

    meta: Dict[str, Any] = {
        "version": AGBK_VERSION,
        "format_version": AGBK_FORMAT_VERSION,
        "schema": "agregat-backtest",
        "saved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "asset": _asset_to_dict(asset),
        "initial_equity": engine.initial_equity,
        "equity": engine.equity,
        "max_equity": engine.max_equity,
        "max_drawdown_abs": engine.max_drawdown_abs,
        "max_drawdown_pct": engine.max_drawdown_pct,
        "current_idx": engine.current_idx,
        "risk_pct": getattr(engine, "risk_pct", 1.0),
        "execution_model": exec_model.to_dict() if exec_model is not None else None,
        "trade_count": len(engine.trades),
    }

    # Sprint 2.3 — track the input file hash if the engine recorded one.
    csv_sha = getattr(engine, "input_csv_sha256", None)
    if csv_sha:
        meta["input_csv_sha256"] = csv_sha
    pq_sha = getattr(engine, "input_parquet_sha256", None)
    if pq_sha:
        meta["input_parquet_sha256"] = pq_sha

    # Sprint 2.4 — persist any RNG seeds the engine knows about so a
    # later replay (`agregat replay`) can reproduce the run bit-for-bit.
    seeds = getattr(engine, "seeds", None)
    if isinstance(seeds, dict) and seeds:
        # Deep-copy + coerce numpy ints → Python ints so JSON stays clean.
        meta["seeds"] = {str(k): int(v) for k, v in seeds.items() if v is not None}

    # Build trades.csv in memory
    trades_csv = io.StringIO()
    if engine.trades:
        rows = [_trade_to_row(t) for t in engine.trades]
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(trades_csv, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Build equity.csv (time → equity, derived from trades)
    equity_csv = io.StringIO()
    eq = engine.initial_equity
    eq_rows: List[Dict[str, Any]] = []
    if engine.trades:
        # Anchor at first entry
        try:
            anchor = pd.Timestamp(engine.trades[0].entry_time).isoformat()
        except Exception:
            anchor = pd.Timestamp.now(tz="UTC").isoformat()
        eq_rows.append({"time": anchor, "equity": eq})
        for t in engine.trades:
            eq += float(t.pnl)
            try:
                ts = pd.Timestamp(t.exit_time).isoformat()
            except Exception:
                continue
            eq_rows.append({"time": ts, "equity": eq})
    if eq_rows:
        ew = csv.DictWriter(equity_csv, fieldnames=["time", "equity"])
        ew.writeheader()
        ew.writerows(eq_rows)

    # Drawings — strip non-serialisable numpy timestamps
    drawings_clean: List[Dict[str, Any]] = []
    for d in engine.drawings:
        cleaned = {k: v for k, v in d.items() if not k.startswith("_")}
        drawings_clean.append(cleaned)

    # Sprint 2.2 — Compute the integrity hash *before* serialising
    # ``meta.json`` so the digest itself is part of the on-disk meta.
    # The hash covers the canonical bytes of the data members plus a
    # stable subset of meta fields (see ``engine.integrity``).
    trades_csv_text = trades_csv.getvalue()
    equity_csv_text = equity_csv.getvalue()
    drawings_json_text = json.dumps(drawings_clean, default=str)
    meta["agbk_sha256"] = hash_archive_payload(
        meta=meta,
        trades_csv=trades_csv_text,
        equity_csv=equity_csv_text,
        drawings_json=drawings_json_text,
    )

    # Write zip
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2, default=str))
        zf.writestr("trades.csv", trades_csv_text)
        zf.writestr("equity.csv", equity_csv_text)
        zf.writestr("drawings.json", drawings_json_text)

    logger.info(
        f"💾 Saved session → {path}: {len(engine.trades)} trades, "
        f"equity={engine.equity:.2f}, max_dd_abs={engine.max_drawdown_abs:.2f}"
    )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_session(engine: "BacktestEngine", path: str) -> Dict[str, Any]:
    """Restore engine state from `path`. Returns the meta dict for inspection.

    Sprint 2.2 — Verifies ``meta['agbk_sha256']`` against a freshly
    recomputed payload digest. Mismatch raises
    :class:`engine.integrity.IntegrityError`. Archives saved before
    Sprint 2.2 (no ``format_version`` and no ``agbk_sha256``) load
    best-effort with a single warning toast.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"AGBK file not found: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        meta: Dict[str, Any] = json.loads(zf.read("meta.json").decode("utf-8"))
        trades_text = (
            zf.read("trades.csv").decode("utf-8")
            if "trades.csv" in zf.namelist()
            else ""
        )
        equity_text = (
            zf.read("equity.csv").decode("utf-8")
            if "equity.csv" in zf.namelist()
            else ""
        )
        drawings_raw = (
            zf.read("drawings.json").decode("utf-8")
            if "drawings.json" in zf.namelist()
            else "[]"
        )

    # Sprint 3.3 — Run format-version migrations so legacy archives
    # get the fields that downstream code assumes (e.g. ``seeds``).
    from engine.persistence.migrations import run_migrations

    meta, _was_migrated = run_migrations(meta)

    # Sprint 2.2 — Integrity check / legacy migration warning.
    if "agbk_sha256" in meta:
        verify_archive(
            meta=meta,
            trades_csv=trades_text,
            equity_csv=equity_text,
            drawings_json=drawings_raw,
        )
    elif "format_version" not in meta:
        logger.warning(
            "📂 Loaded legacy .agbk (no format_version / agbk_sha256) — "
            "integrity check skipped. Re-save to migrate to format_version=1."
        )

    if meta.get("version", 0) > AGBK_VERSION:
        logger.warning(
            f"AGBK file version {meta.get('version')} is newer than supported "
            f"({AGBK_VERSION}). Some fields may be ignored."
        )

    # --- restore asset_config ---
    # ``save_session`` serialised the asset metadata; without restoring it,
    # the engine silently keeps whatever asset was bound in the GUI when the
    # session was loaded — so PnL re-quantisation, lot sizing, and the spread
    # cache would all use the wrong instrument's parameters.
    asset_meta = meta.get("asset")
    if isinstance(asset_meta, dict) and hasattr(engine, "asset_config"):
        engine.asset_config = _dict_to_asset(asset_meta, engine.asset_config)
        # Spread cache in ``ExecutionModel`` is keyed by ``id(asset)`` — bust
        # it now so the new asset doesn't re-use a stale cached spread.
        exec_model_inst = getattr(engine, "execution_model", None)
        if exec_model_inst is not None and hasattr(
            exec_model_inst, "invalidate_caches"
        ):
            exec_model_inst.invalidate_caches()

    # --- restore risk/equity ---
    engine.initial_equity = float(meta.get("initial_equity", engine.initial_equity))
    engine.equity = float(meta.get("equity", engine.initial_equity))
    engine.max_equity = float(meta.get("max_equity", engine.equity))
    engine.max_drawdown_abs = float(meta.get("max_drawdown_abs", 0.0))
    engine.max_drawdown_pct = float(meta.get("max_drawdown_pct", 0.0))
    engine.current_idx = int(meta.get("current_idx", -1))
    engine.risk_pct = float(meta.get("risk_pct", 1.0))

    # --- restore execution model ---
    em_data = meta.get("execution_model")
    if em_data is not None and hasattr(engine, "execution_model"):
        from engine.execution.execution import ExecutionModel

        engine.execution_model = ExecutionModel.from_dict(em_data)

    # --- restore trades ---
    engine.trades = []
    engine._next_trade_id = 0
    # Reset the centralised accumulator so TZ Phase 4 migrations stay in sync
    # — also resets ``_stats_*`` mirror fields below from the same source so
    # everything reflects the loaded trades exactly. (TZ Phase 1 / A5.)
    engine.stats.reset()

    if trades_text.strip():
        reader = csv.DictReader(io.StringIO(trades_text))
        for row in reader:
            t = _row_to_trade(row)
            engine.trades.append(t)
            engine._next_trade_id = max(engine._next_trade_id, t.id)
            engine.stats.update(t)

    engine._stats_wins = engine.stats.wins
    engine._stats_losses = engine.stats.losses
    engine._stats_gross_profit = engine.stats.gross_profit
    engine._stats_gross_loss = engine.stats.gross_loss
    engine._stats_longs = engine.stats.longs
    engine._stats_shorts = engine.stats.shorts
    engine._stats_dirty = True

    # --- rebuild equity_track (BUG-03 fix) ---
    engine.equity_track = [engine.initial_equity]
    eq_running = engine.initial_equity
    for t in engine.trades:
        eq_running += t.pnl
        engine.equity_track.append(eq_running)

    # --- replay equity through the centralised DD tracker ---
    # (TZ Phase 4 / Etap 4.1.) Keeps ``engine.drawdown`` in sync with the
    # legacy ``max_drawdown_*`` mirror fields restored from ``meta`` above.
    if hasattr(engine, "drawdown"):
        engine.drawdown.reset(engine.initial_equity)
        for eq in engine.equity_track:
            engine.drawdown.update(float(eq))
        # The replay is the source of truth — overwrite the meta-mirrored
        # legacy fields so they cannot drift from the tracker.
        engine.max_equity = engine.drawdown.peak_equity
        engine.max_drawdown_abs = engine.drawdown.max_drawdown_abs
        engine.max_drawdown_pct = engine.drawdown.max_drawdown_pct

    # --- restore drawings ---
    try:
        drawings = json.loads(drawings_raw)
        engine.drawings = list(drawings) if isinstance(drawings, list) else []
        engine._next_drawing_id = max(
            (d.get("_id", 0) for d in engine.drawings), default=0
        )
    except json.JSONDecodeError:
        engine.drawings = []
        engine._next_drawing_id = 0

    logger.info(
        f"📂 Loaded session ← {path}: {len(engine.trades)} trades, equity={engine.equity:.2f}"
    )
    return meta


def load_session_raw(path: str) -> Dict[str, Any]:
    """Read an .agbk archive without mutating any engine.

    Returns ``{"meta": dict, "trades": list[Trade]}`` — used by the
    Quant → Compare tab to inspect a saved session side-by-side.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"AGBK file not found: {path}")
    with zipfile.ZipFile(path, "r") as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        trades_text = (
            zf.read("trades.csv").decode("utf-8")
            if "trades.csv" in zf.namelist()
            else ""
        )
    trades: List[Trade] = []
    if trades_text.strip():
        reader = csv.DictReader(io.StringIO(trades_text))
        for row in reader:
            trades.append(_row_to_trade(row))
    return {"meta": meta, "trades": trades}


__all__ = [
    "save_session",
    "load_session",
    "load_session_raw",
    "AGBK_VERSION",
    "AGBK_FORMAT_VERSION",
    "AGBK_EXTENSION",
    "IntegrityError",
]
