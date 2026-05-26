"""Phase 2.4 — Disk cache for the TF aggregator's prebuild output.

A 10 M-bar 1m feed pays ~30–50 s for ``TimeframeAggregator.prebuild``
on a cold cache. Re-opening the same CSV twice in a row should not
pay that cost twice. This module lifts the prebuild artefacts (per-TF
HTF arrays, completion points, forming arrays, the forming-TF set)
to/from a per-dataset pickle file under

    ~/.agregatishe/cache/prebuild/{cache_key}.pkl

The :func:`compute_cache_key` function builds a deterministic SHA-256
of the *content* of the input DataFrame plus the prebuild version,
the supported-TF list and the user's ``track_forming`` choice — any
of those changing invalidates the cache, so a stale pickle can never
silently corrupt a backtest.

Pickle protocol 5 is used because it stores raw NumPy buffers
zero-copy (no per-array fan-out, no parquet schema overhead), and
the pickle format is part of the Python standard library — no extra
dependency for Phase 2.4.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("StrategyTester.engine.data.prebuild_cache")

# Bumped whenever the prebuild output format changes — any in-flight
# cache entries with a different version string are silently ignored
# and rebuilt. Keep this monotonically increasing; never recycle.
CACHE_VERSION = "phase2.4-v1"

# Cache root override. Tests use this to point the module at a tmpdir
# instead of the user's home directory; production code never sets it.
_CACHE_DIR_ENV = "AGREGATISHE_PREBUILD_CACHE_DIR"


def get_cache_dir() -> Path:
    """Return the directory that holds prebuild cache files.

    Honours the ``AGREGATISHE_PREBUILD_CACHE_DIR`` environment
    variable so tests / CI can isolate. Defaults to
    ``~/.agregatishe/cache/prebuild``.
    """
    override = os.environ.get(_CACHE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".agregatishe" / "cache" / "prebuild"


def _df_fingerprint(df: pd.DataFrame) -> bytes:
    """Cheap, content-sensitive digest of a 1m OHLCV DataFrame.

    We hash:
    * the row count,
    * the first and last ``time`` values (string form),
    * a bytewise SHA-256 of every numeric column the prebuild reads.

    Hashing the raw bytes of every numeric column is fast (NumPy's
    contiguous memory walks at a few GB/s) and catches the case where
    two CSVs have identical timestamps but different prices — which
    is exactly the failure mode an index-only fingerprint would miss.
    """
    h = hashlib.sha256()
    h.update(f"rows={len(df)}".encode())
    if "time" in df.columns and len(df) > 0:
        # Keep this resilient to TZ-aware vs naive — both serialise
        # via str() the same way for cache-key purposes.
        h.update(f"first={df['time'].iloc[0]}".encode())
        h.update(f"last={df['time'].iloc[-1]}".encode())
    # Hash numeric columns bytewise — order matters so we sort.
    for col in sorted(df.columns):
        if col == "time":
            continue
        ser = df[col]
        if not np.issubdtype(ser.dtype, np.number):
            continue
        arr = np.ascontiguousarray(ser.to_numpy())
        h.update(f"col={col}|dtype={arr.dtype}|".encode())
        h.update(arr.tobytes())
    return h.digest()


def compute_cache_key(
    df: pd.DataFrame,
    tfs: Iterable[str],
    *,
    track_forming: Optional[Iterable[str]] = None,
    version: str = CACHE_VERSION,
) -> str:
    """Deterministic SHA-256 of the prebuild inputs as a hex string.

    Changing ``df`` content, the TF list, ``track_forming`` or the
    prebuild version produces a different key — so a cache hit can
    only happen on byte-identical inputs.
    """
    h = hashlib.sha256()
    h.update(version.encode())
    h.update(b"|tfs=")
    for tf in sorted(tfs):
        h.update(tf.encode())
        h.update(b",")
    h.update(b"|track_forming=")
    if track_forming is None:
        h.update(b"<none>")
    else:
        for tf in sorted(track_forming):
            h.update(tf.encode())
            h.update(b",")
    h.update(b"|df=")
    h.update(_df_fingerprint(df))
    return h.hexdigest()


def cache_path(cache_key: str) -> Path:
    """Return the on-disk path for a given cache key."""
    return get_cache_dir() / f"{cache_key}.pkl"


# ---------------------------------------------------------------------------
# State (de)serialisation
# ---------------------------------------------------------------------------


# These are exactly the per-TF dictionaries / sets that
# ``TimeframeAggregator.prebuild`` populates. Keeping the schema
# minimal (no proxies, no compressed parquet, no inverse mapping)
# means cache reads are a single ``pickle.load`` + a few attribute
# assignments — well under 100 ms even for the 10 M-bar dataset.
StateDict = Dict[str, Any]


def state_to_dict(
    *,
    htf_arrays: Dict[str, Dict[str, np.ndarray]],
    completion_points: Dict[str, np.ndarray],
    forming_arrays: Dict[str, Dict[str, np.ndarray]],
    forming_tfs: set,
) -> StateDict:
    """Pack a TF aggregator's prebuild outputs into a pickle-able dict."""
    return {
        "version": CACHE_VERSION,
        "htf_arrays": htf_arrays,
        "completion_points": completion_points,
        "forming_arrays": forming_arrays,
        "forming_tfs": set(forming_tfs),
    }


def save_state(cache_key: str, state: StateDict) -> Path:
    """Atomically write ``state`` to the cache directory.

    Writes to a temp file in the same dir then ``os.replace`` — a
    crash mid-write never leaves a half-written cache that the next
    run would mis-parse.
    """
    target = cache_path(cache_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".prebuild-", suffix=".pkl.tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(state, fh, protocol=5)
        os.replace(tmp_path, target)
    except Exception:
        # Best-effort cleanup on failure; never raise from the cache
        # path — the caller will recompute the prebuild instead.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def load_state(cache_key: str) -> Optional[StateDict]:
    """Return the cached state for ``cache_key`` or ``None`` on miss
    / version mismatch / corruption.

    All exceptions are swallowed and turned into a cache miss — a
    broken cache file should never crash a backtest, the worst case
    is a single recompute and a warning in the log.
    """
    target = cache_path(cache_key)
    if not target.exists():
        return None
    try:
        with target.open("rb") as fh:
            state = pickle.load(fh)
    except Exception as exc:
        logger.warning("Prebuild cache read failed (%s); ignoring entry", exc)
        return None
    if not isinstance(state, dict):
        logger.warning("Prebuild cache entry has wrong type; ignoring")
        return None
    if state.get("version") != CACHE_VERSION:
        return None
    return state


def clear_cache() -> int:
    """Delete every prebuild cache entry. Returns the number of bytes
    freed. Safe to call when the cache directory does not exist."""
    root = get_cache_dir()
    if not root.exists():
        return 0
    freed = 0
    for entry in root.iterdir():
        try:
            freed += entry.stat().st_size
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            continue
    return freed


def cache_size_bytes() -> int:
    """Total size of the prebuild cache directory in bytes. ``0`` when
    the directory does not exist. Useful for the GUI's "Clear cache"
    button to display."""
    root = get_cache_dir()
    if not root.exists():
        return 0
    total = 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


__all__ = [
    "CACHE_VERSION",
    "cache_path",
    "cache_size_bytes",
    "clear_cache",
    "compute_cache_key",
    "get_cache_dir",
    "load_state",
    "save_state",
    "state_to_dict",
]
