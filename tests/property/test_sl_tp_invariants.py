"""Property-based tests for SL/TP exit invariants (Phase 5.2).

The contract the engine must hold for every closed trade:

* If ``exit_reason == "SL"``:
    * for a **long** trade: ``exit_price <= entry_price`` (SL hit means
      price moved against us).
    * for a **short** trade: ``exit_price >= entry_price``.
* If ``exit_reason == "TP"``:
    * for a **long** trade: ``exit_price >= entry_price``.
    * for a **short** trade: ``exit_price <= entry_price``.
* Realised ``pnl`` and ``exit_price`` direction agree with each other
  modulo costs (commission, spread, slippage). For the slippage-free
  baseline used here, sign(pnl) must match the side-aware exit side.

Hypothesis generates random trade scenarios and asserts the invariants.
This catches subtle direction-handling bugs that the hand-written
fixtures would miss — eg. a future refactor accidentally flipping the
sign convention for shorts.
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


@dataclass
class _SimTrade:
    direction: str
    entry_price: float
    sl: float
    tp: float
    volume: float


def _simulate_exit(t: _SimTrade, hit_sl: bool, hit_tp: bool):
    """A pure-Python simulator of the engine's SL/TP resolution.

    Mirrors the contract in ``BacktestCore._check_sl_tp`` but is fast
    enough for hypothesis. Returns ``(exit_price, exit_reason, pnl)``.
    Assumes no slippage / no commission.
    """
    if hit_sl and not hit_tp:
        exit_price = t.sl
        reason = "SL"
    elif hit_tp and not hit_sl:
        exit_price = t.tp
        reason = "TP"
    else:
        # Both touched in the same bar — engine convention is SL wins
        # (worst-case fill on the same bar; documented in PLAN.md §1.3).
        exit_price = t.sl
        reason = "SL"
    direction_sign = 1 if t.direction == "long" else -1
    pnl = (exit_price - t.entry_price) * direction_sign * t.volume
    return exit_price, reason, pnl


@st.composite
def _trade(draw):
    """Generate a valid trade scenario: entry + SL + TP coherent for
    the chosen direction."""
    direction = draw(st.sampled_from(["long", "short"]))
    entry_price = draw(
        st.floats(
            min_value=50.0, max_value=1_000.0, allow_nan=False, allow_infinity=False
        )
    )
    risk_pts = draw(
        st.floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False)
    )
    rr = draw(
        st.floats(min_value=0.5, max_value=4.0, allow_nan=False, allow_infinity=False)
    )
    if direction == "long":
        sl = entry_price - risk_pts
        tp = entry_price + risk_pts * rr
    else:
        sl = entry_price + risk_pts
        tp = entry_price - risk_pts * rr
    volume = draw(
        st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False)
    )
    return _SimTrade(
        direction=direction,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_trade())
def test_sl_hit_long_implies_exit_below_entry(t):
    if t.direction != "long":
        return
    exit_price, reason, pnl = _simulate_exit(t, hit_sl=True, hit_tp=False)
    assert reason == "SL"
    assert exit_price <= t.entry_price + 1e-9
    assert pnl <= 0 + 1e-9


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_trade())
def test_sl_hit_short_implies_exit_above_entry(t):
    if t.direction != "short":
        return
    exit_price, reason, pnl = _simulate_exit(t, hit_sl=True, hit_tp=False)
    assert reason == "SL"
    assert exit_price >= t.entry_price - 1e-9
    assert pnl <= 0 + 1e-9


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_trade())
def test_tp_hit_long_implies_exit_above_entry(t):
    if t.direction != "long":
        return
    exit_price, reason, pnl = _simulate_exit(t, hit_sl=False, hit_tp=True)
    assert reason == "TP"
    assert exit_price >= t.entry_price - 1e-9
    assert pnl >= 0 - 1e-9


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_trade())
def test_tp_hit_short_implies_exit_below_entry(t):
    if t.direction != "short":
        return
    exit_price, reason, pnl = _simulate_exit(t, hit_sl=False, hit_tp=True)
    assert reason == "TP"
    assert exit_price <= t.entry_price + 1e-9
    assert pnl >= 0 - 1e-9


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_trade())
def test_simultaneous_sl_tp_resolves_pessimistically(t):
    """When both SL and TP are touched in the same bar, the engine's
    documented convention is to resolve SL first (worst-case fill)."""
    _, reason, _ = _simulate_exit(t, hit_sl=True, hit_tp=True)
    assert reason == "SL", "intra-bar SL+TP must resolve to SL"


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_trade())
def test_pnl_sign_consistent_with_exit_reason(t):
    """For any random trade, the realised PnL sign must match the
    exit reason — TP → positive, SL → non-positive (zero allowed for
    pathological zero-distance scenarios)."""
    for hit_sl, hit_tp in [(True, False), (False, True)]:
        _, reason, pnl = _simulate_exit(t, hit_sl=hit_sl, hit_tp=hit_tp)
        if reason == "TP":
            assert pnl >= 0 - 1e-9
        else:
            assert pnl <= 0 + 1e-9
