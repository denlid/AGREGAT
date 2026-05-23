"""TZ Phase 2b — headless test of the async CSV load worker.

We can't drive the full ``MainWindow`` headlessly without a real
``QApplication`` event loop, but we can drive a ``ComputeWorker`` (which
the async CSV path reuses) end to end on a tiny CSV file. The worker
runs a real ``QThread``; the test waits for ``finished``/``error`` via a
short event-loop spin so a regression in the worker contract surfaces.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from engine.data.data_loader import DataLoader
from gui.workers.workers import ComputeWorker


@pytest.fixture(scope="module")
def qapp():
    inst = QCoreApplication.instance()
    return inst if inst is not None else QCoreApplication([])


def _wait_for_signal(worker, signal_name: str, timeout_ms: int = 5000):
    """Spin a local ``QEventLoop`` until ``signal_name`` fires on
    ``worker`` or until the timeout expires. Returns the payload (or
    ``None`` on timeout)."""
    loop = QEventLoop()
    captured: list = []

    def on_emit(*args):
        captured.append(args[0] if args else None)
        loop.quit()

    getattr(worker, signal_name).connect(on_emit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return captured[0] if captured else None


def _write_csv(path: Path) -> None:
    path.write_text(
        "time,open,high,low,close,volume\n"
        "2024-01-02 09:00,100,101,99,100,1\n"
        "2024-01-02 09:01,100,102,99,101,1\n"
        "2024-01-02 09:02,101,103,100,102,1\n"
    )


def test_compute_worker_runs_csv_load_off_main_thread(qapp, tmp_path: Path) -> None:
    csv = tmp_path / "tiny.csv"
    _write_csv(csv)

    worker = ComputeWorker(DataLoader.load_csv, str(csv))
    worker.start()
    df = _wait_for_signal(worker, "finished")
    worker.wait(2000)

    assert df is not None, "ComputeWorker.finished never fired"
    assert len(df) == 3
    assert list(df.columns[:5]) == ["time", "open", "high", "low", "close"]


def test_compute_worker_emits_error_on_bad_path(qapp, tmp_path: Path) -> None:
    bad = tmp_path / "missing.csv"
    worker = ComputeWorker(DataLoader.load_csv, str(bad))
    worker.start()
    exc = _wait_for_signal(worker, "error")
    worker.wait(2000)

    assert exc is not None, "ComputeWorker.error never fired"
    assert isinstance(exc, Exception)


def test_compute_worker_cancel_flag_is_observable(qapp) -> None:
    """``cancel`` flips ``is_cancelled`` so callables that poll it can
    exit early. The CSV path itself can't observe the flag mid-stream
    (``pd.read_csv`` is not interruptible), so we just lock the
    contract used by the GUI's late-cancel handling.
    """
    worker = ComputeWorker(lambda: None)
    assert not worker.is_cancelled()
    worker.cancel()
    assert worker.is_cancelled()
