"""Phase 2.5 — zero-copy NumPy arrays across multiprocessing workers.

The GUI's grid-search optimizer routinely runs 8 worker processes
through ``mp.get_context('spawn')``. Today every worker receives its
own pickled copy of the OHLCV DataFrame the runner closes over —
on a 5 M-bar feed that's 8 × 5 000 000 × 6 cols × 8 bytes ≈ **1.9 GB**
of duplicated RAM with no semantic justification (the data is
read-only).

This module provides :class:`SharedArrayBundle`, a small context
manager around :mod:`multiprocessing.shared_memory` that:

1. allocates one named ``SharedMemory`` block per NumPy array,
2. publishes a tiny pickle-safe descriptor that workers reconstruct
   into a zero-copy ``np.ndarray`` view via :func:`attach`,
3. unlinks every block on context exit so a crashing parent never
   leaves orphaned ``/dev/shm`` blocks behind.

A worker reads the bundle via :func:`attached`, which returns the
dict of arrays the parent passed in. The ``Pool(initializer=...)``
hook handles the attachment exactly once per worker process.

Falls back to a quiet pickle copy if shared memory is unavailable
(rare — Windows < 3.8 was the documented gap and we're on 3.12).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

logger = logging.getLogger("StrategyTester.engine.optimization.shared_arrays")


@dataclass(frozen=True)
class SharedArrayDescriptor:
    """Pickle-safe descriptor of one shared array — the only thing that
    crosses the process boundary."""

    shm_name: str
    shape: Tuple[int, ...]
    dtype: str  # ``str(dtype)`` is round-trippable through ``np.dtype``


@dataclass(frozen=True)
class SharedBundleDescriptor:
    """Per-bundle descriptor: bundle name + per-key array descriptors."""

    bundle_name: str
    arrays: Tuple[Tuple[str, SharedArrayDescriptor], ...]


# Each worker maintains its own attached cache so multiple runner
# calls share the same NumPy view (no re-attach per call).
_WORKER_ATTACHED: Dict[str, Dict[str, np.ndarray]] = {}
_WORKER_HANDLES: Dict[str, List[shared_memory.SharedMemory]] = {}


class SharedArrayBundle:
    """Allocate ``SharedMemory`` blocks for a dict of NumPy arrays and
    expose a descriptor that workers can attach to.

    Use as a context manager so the blocks are unlinked on exit::

        arrays = {"close": close, "high": high, "low": low}
        with SharedArrayBundle(arrays, name="backtest") as bundle:
            grid_search(runner, grid, n_workers=8, shared_arrays=bundle)
    """

    def __init__(
        self, arrays: Mapping[str, np.ndarray], *, name: Optional[str] = None
    ) -> None:
        # Random suffix avoids name collisions if the same parent
        # opens two bundles concurrently.
        suffix = secrets.token_hex(4)
        self.bundle_name = f"agregat_{name or 'bundle'}_{suffix}"
        self._handles: List[shared_memory.SharedMemory] = []
        self._descriptors: List[Tuple[str, SharedArrayDescriptor]] = []
        # Local NumPy views into the shared blocks — kept alive so the
        # parent process can read/write the arrays directly while the
        # bundle is open.
        self.arrays: Dict[str, np.ndarray] = {}

        try:
            for key, arr in arrays.items():
                arr_c = np.ascontiguousarray(arr)
                shm = shared_memory.SharedMemory(
                    create=True,
                    size=arr_c.nbytes,
                    name=f"{self.bundle_name}_{key}",
                )
                view = np.ndarray(arr_c.shape, dtype=arr_c.dtype, buffer=shm.buf)
                view[...] = arr_c  # one-time copy into the shared block
                self._handles.append(shm)
                self._descriptors.append(
                    (
                        key,
                        SharedArrayDescriptor(
                            shm_name=shm.name,
                            shape=tuple(arr_c.shape),
                            dtype=str(arr_c.dtype),
                        ),
                    )
                )
                self.arrays[key] = view
        except Exception:
            # Best-effort cleanup if any allocation fails so we don't
            # leak ``/dev/shm`` entries.
            self._cleanup()
            raise

    def descriptor(self) -> SharedBundleDescriptor:
        return SharedBundleDescriptor(
            bundle_name=self.bundle_name, arrays=tuple(self._descriptors)
        )

    def __enter__(self) -> "SharedArrayBundle":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._cleanup()

    def close(self) -> None:
        """Idempotent shut-down (close + unlink). Safe to call twice."""
        self._cleanup()

    def _cleanup(self) -> None:
        for shm in self._handles:
            try:
                shm.close()
            except Exception:
                pass
            try:
                shm.unlink()
            except FileNotFoundError:
                # Another process may have already unlinked the block —
                # benign in cleanup paths.
                pass
            except Exception as exc:
                logger.debug("SharedMemory unlink failed (%s)", exc)
        self._handles.clear()
        self._descriptors.clear()
        self.arrays.clear()


# ---------------------------------------------------------------------------
# Worker-side API
# ---------------------------------------------------------------------------


def attach(descriptor: SharedBundleDescriptor) -> Dict[str, np.ndarray]:
    """Attach to every block in ``descriptor`` and return a dict of
    zero-copy NumPy views. Caller is responsible for keeping the
    returned views alive — releasing the dict closes the views.

    This function is the *worker-side* entry point: it never creates
    or unlinks shared memory, only attaches.
    """
    out: Dict[str, np.ndarray] = {}
    handles: List[shared_memory.SharedMemory] = []
    for key, desc in descriptor.arrays:
        shm = shared_memory.SharedMemory(name=desc.shm_name)
        handles.append(shm)
        out[key] = np.ndarray(desc.shape, dtype=np.dtype(desc.dtype), buffer=shm.buf)
    # Stash the SharedMemory handles globally — releasing them would
    # invalidate the NumPy views the runner is about to use.
    _WORKER_HANDLES[descriptor.bundle_name] = handles
    _WORKER_ATTACHED[descriptor.bundle_name] = out
    return out


def attached(bundle_name: str) -> Dict[str, np.ndarray]:
    """Return the worker's attached arrays for ``bundle_name``.

    Raises :class:`KeyError` if :func:`pool_initializer` wasn't run
    in this worker process — this is a programmer error, not a
    runtime condition.
    """
    return _WORKER_ATTACHED[bundle_name]


def pool_initializer(descriptor: SharedBundleDescriptor) -> None:
    """``multiprocessing.Pool(initializer=...)`` hook — called exactly
    once per worker process. Attaches the bundle, leaving the dict
    available via :func:`attached`."""
    attach(descriptor)


def detach_all() -> None:
    """Best-effort release of every cached attachment in the current
    process. Used by :func:`pool_initializer` consumers that want
    deterministic cleanup; the shared blocks themselves are unlinked
    by the bundle's parent context manager."""
    for handles in _WORKER_HANDLES.values():
        for shm in handles:
            try:
                shm.close()
            except Exception:
                pass
    _WORKER_HANDLES.clear()
    _WORKER_ATTACHED.clear()


__all__ = [
    "SharedArrayBundle",
    "SharedArrayDescriptor",
    "SharedBundleDescriptor",
    "attach",
    "attached",
    "detach_all",
    "pool_initializer",
]
