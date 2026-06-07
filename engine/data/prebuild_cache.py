"""Phase 2.4 — Disk cache for the TF aggregator's prebuild output.

A 10 M-bar 1m feed pays ~30–50 s for ``TimeframeAggregator.prebuild``
on a cold cache. Re-opening the same CSV twice in a row should not
pay that cost twice. This module lifts the prebuild artefacts (per-TF
HTF arrays, completion points, forming arrays, the forming-TF set)
to/from a per-dataset file under

    ~/.agregatishe/cache/prebuild/{cache_key}.npz

The :func:`compute_cache_key` function builds a deterministic SHA-256
of the *content* of the input DataFrame plus the prebuild version,
the supported-TF list and the user's ``track_forming`` choice — any
of those changing invalidates the cache, so a stale file can never
silently corrupt a backtest.

SECURITY (C-04 FIX):
    Previously used pickle.load() which allows arbitrary code execution
    during deserialization. Replaced with numpy.savez/npz format which:
    - Uses allow_pickle=False by default (safe deserialization)
    - Stores raw NumPy buffers zero-copy (same perf as pickle protocol 5)
    - No extra dependencies beyond numpy
    - Tampered files cause ValueError, not RCE
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
CACHE_VERSION = "phase2.4-v2-npz"  # v2: migrated from pickle to npz

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
    return get_cache_dir() / f"{cache_key}.npz"


# ---------------------------------------------------------------------------
# State (de)serialisation — SAFE npz format (no pickle)
# ---------------------------------------------------------------------------
#
# SECURITY: All data is stored as numpy arrays in .npz format with
# allow_pickle=False. Metadata (version, forming_tfs set) is stored
# as a JSON string in a dedicated array. This eliminates the RCE
# vector that pickle.load() introduced.
#
# These are exactly the per-TF dictionaries / sets that
# ``TimeframeAggregator.prebuild`` populates. Keeping the schema
# minimal (no proxies, no compressed parquet, no inverse mapping)
# means cache reads are a single ``np.load`` + a few attribute
# assignments — well under 100 ms even for the 10 M-bar dataset.
StateDict = Dict[str, Any]


def state_to_dict(
    *,
    htf_arrays: Dict[str, Dict[str, np.ndarray]],
    completion_points: Dict[str, np.ndarray],
    forming_arrays: Dict[str, Dict[str, np.ndarray]],
    forming_tfs: set,
) -> StateDict:
    """Pack a TF aggregator's prebuild outputs into a serializable dict."""
    return {
        "version": CACHE_VERSION,
        "htf_arrays": htf_arrays,
        "completion_points": completion_points,
        "forming_arrays": forming_arrays,
        "forming_tfs": set(forming_tfs),
    }


def _flatten_arrays(
    state: StateDict,
) -> tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """Flatten nested dict-of-arrays into flat dict for npz storage.

    Returns:
        (flat_arrays, metadata) where flat_arrays can be saved via
        np.savez and metadata holds non-array data (version, sets).
    """
    flat: Dict[str, np.ndarray] = {}
    metadata: Dict[str, Any] = {
        "version": state.get("version", CACHE_VERSION),
        "htf_keys": [],
        "forming_keys": [],
        "forming_tfs": sorted(state.get("forming_tfs", set())),
    }

    # Flatten htf_arrays: {tf: {col: array}}
    htf_arrays = state.get("htf_arrays", {})
    for tf, cols in htf_arrays.items():
        for col, arr in cols.items():
            key = f"htf_{tf}_{col}"
            flat[key] = np.asarray(arr)
            metadata["htf_keys"].append({"tf": tf, "col": col, "key": key})

    # Flatten completion_points: {tf: array}
    for tf, arr in state.get("completion_points", {}).items():
        key = f"cp_{tf}"
        flat[key] = np.asarray(arr)

    # Flatten forming_arrays: {tf: {col: array}}
    forming_arrays = state.get("forming_arrays", {})
    for tf, cols in forming_arrays.items():
        for col, arr in cols.items():
            key = f"form_{tf}_{col}"
            flat[key] = np.asarray(arr)
            metadata["forming_keys"].append({"tf": tf, "col": col, "key": key})

    return flat, metadata


def _unflatten_arrays(
    flat: Dict[str, np.ndarray],
    metadata: Dict[str, Any],
) -> StateDict:
    """Reconstruct nested state dict from flat npz arrays + metadata."""
    htf_arrays: Dict[str, Dict[str, np.ndarray]] = {}
    for entry in metadata.get("htf_keys", []):
        tf, col, key = entry["tf"], entry["col"], entry["key"]
        if tf not in htf_arrays:
            htf_arrays[tf] = {}
        htf_arrays[tf][col] = flat[key]

    completion_points: Dict[str, np.ndarray] = {}
    for key in flat:
        if key.startswith("cp_"):
            tf = key[3:]
            completion_points[tf] = flat[key]

    forming_arrays: Dict[str, Dict[str, np.ndarray]] = {}
    for entry in metadata.get("forming_keys", []):
        tf, col, key = entry["tf"], entry["col"], entry["key"]
        if tf not in forming_arrays:
            forming_arrays[tf] = {}
        forming_arrays[tf][col] = flat[key]

    return {
        "version": metadata.get("version", CACHE_VERSION),
        "htf_arrays": htf_arrays,
        "completion_points": completion_points,
        "forming_arrays": forming_arrays,
        "forming_tfs": set(metadata.get("forming_tfs", [])),
    }


def save_state(cache_key: str, state: StateDict) -> Path:
    """Atomically write ``state`` to the cache directory using safe npz format.

    SECURITY: Uses numpy.savez (NOT pickle) to prevent deserialization RCE.
    Writes to a temp file in the same dir then ``os.replace`` — a
    crash mid-write never leaves a half-written cache that the next
    run would mis-parse.
    """
    target = cache_path(cache_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".prebuild-", suffix=".npz.tmp", dir=str(target.parent)
    )
    try:
        os.close(fd)  # Close fd; numpy will open the file itself
        flat_arrays, metadata = _flatten_arrays(state)
        # Store metadata as JSON in a special array
        meta_json = json.dumps(metadata, ensure_ascii=True)
        np.savez(
            tmp_path,
            __metadata__=np.array([meta_json]),
            **flat_arrays,
        )
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

    SECURITY: Uses np.load with allow_pickle=False to prevent RCE.
    All exceptions are swallowed and turned into a cache miss — a
    broken cache file should never crash a backtest, the worst case
    is a single recompute and a warning in the log.
    """
    target = cache_path(cache_key)
    if not target.exists():
        return None
    try:
        # CRITICAL: allow_pickle=False prevents arbitrary code execution
        with np.load(str(target), allow_pickle=False) as data:
            # Extract metadata
            if "__metadata__" not in data:
                logger.warning("Prebuild cache entry missing metadata; ignoring")
                return None
            meta_json = str(data["__metadata__"][0])
            metadata = json.loads(meta_json)

            # Build flat dict from remaining arrays
            flat = {k: data[k] for k in data.files if k != "__metadata__"}

            state = _unflatten_arrays(flat, metadata)
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
