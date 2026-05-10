"""Phase 3.1 (E4) — ``Context.move_sl_to_be`` tests.

The helper has to:
- handle missing positions gracefully,
- flip sign correctly between long and short,
- multiply by ``tick_size`` so callers can speak in points,
- never widen an existing SL (we don't want a "BE" call to undo a
  protective trail).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.strategy.context import Context


class _Pos:
    def __init__(self, direction: str, entry_price: float, sl=None):
        self.direction = direction
        self.entry_price = entry_price
        self.sl = sl
        self.tp = None


def _ctx(*, direction: str, entry: float, tick: float = 1.0, sl=None) -> Context:
    pos = _Pos(direction, entry, sl)
    eng = SimpleNamespace(
        position=pos,
        asset_config=SimpleNamespace(tick_size=tick),
    )
    c = Context(eng)
    c.candle = {"time": "2024-01-01 09:00"}
    return c


def test_returns_false_when_no_position():
    eng = SimpleNamespace(position=None, asset_config=SimpleNamespace(tick_size=1.0))
    c = Context(eng)
    c.candle = {"time": "2024-01-01 09:00"}
    assert c.move_sl_to_be() is False


def test_long_zero_offset_moves_sl_to_entry():
    c = _ctx(direction="long", entry=100.0)
    assert c.move_sl_to_be(0.0) is True
    assert c._engine.position.sl == pytest.approx(100.0)


def test_short_zero_offset_moves_sl_to_entry():
    c = _ctx(direction="short", entry=100.0)
    assert c.move_sl_to_be(0.0) is True
    assert c._engine.position.sl == pytest.approx(100.0)


def test_long_positive_offset_moves_above_entry():
    c = _ctx(direction="long", entry=100.0, tick=0.5)
    # offset_points=2 with tick=0.5 -> price offset of +1.0 for longs.
    assert c.move_sl_to_be(2.0) is True
    assert c._engine.position.sl == pytest.approx(101.0)


def test_short_positive_offset_moves_below_entry():
    c = _ctx(direction="short", entry=100.0, tick=0.5)
    assert c.move_sl_to_be(2.0) is True
    assert c._engine.position.sl == pytest.approx(99.0)


def test_long_refuses_to_widen_existing_sl():
    """A trail at 99 must NOT be widened back to 95 by a careless BE call."""
    c = _ctx(direction="long", entry=100.0, sl=99.0)
    # Trying to move SL to 95 (entry - 5*tick) — looser than current 99.
    assert c.move_sl_to_be(-5.0) is False
    assert c._engine.position.sl == pytest.approx(99.0)


def test_short_refuses_to_widen_existing_sl():
    c = _ctx(direction="short", entry=100.0, sl=101.0)
    # Trying to move SL to 105 — looser than current 101 for a short.
    assert c.move_sl_to_be(-5.0) is False
    assert c._engine.position.sl == pytest.approx(101.0)


def test_uses_default_tick_when_unset():
    """Asset configs without tick_size fall back to 1.0 (price == points)."""
    pos = _Pos("long", 100.0)
    eng = SimpleNamespace(position=pos, asset_config=SimpleNamespace())  # no tick_size
    c = Context(eng)
    c.candle = {"time": "2024-01-01 09:00"}
    assert c.move_sl_to_be(3.0) is True
    assert pos.sl == pytest.approx(103.0)
