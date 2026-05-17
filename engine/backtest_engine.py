"""
Compatibility shim for the historical ``BacktestEngine`` import path.

The engine has been split into two modules:

* :mod:`engine.core.backtest_core` — pure-Python backtest core, no
  PySide6 / Qt dependencies. This is the headless heart of AGREGAT and
  is what every other module in :mod:`engine` should depend on.
* :mod:`engine.adapters.qt_adapter` — Qt façade that re-exports each
  core notification as a Qt signal. Imported only when the GUI is in
  play.

Existing code that does ``from engine.backtest_engine import BacktestEngine``
continues to work: the name resolves to :class:`QtBacktestAdapter`, which
is feature-equivalent to the pre-split class.
"""

from __future__ import annotations

from engine.adapters.qt_adapter import QtBacktestAdapter as BacktestEngine
from engine.core.backtest_core import BacktestCore, _round_to_step

__all__ = ["BacktestCore", "BacktestEngine", "_round_to_step"]
