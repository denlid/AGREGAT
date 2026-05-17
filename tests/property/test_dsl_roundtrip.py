"""Property-based tests for DSL parse / serialise round-trip (Phase 5.2).

The contract that ``parse(serialise(x)) == x`` must hold for every
valid DSL document is checked end-to-end against the bundled fixtures
plus a hypothesis strategy that generates synthetic specs.

Why hypothesis on top of fixtures? Fixtures cover the realistic shapes
the AI authoring layer will produce, but the parser is also expected
to accept any *schema-valid* permutation — duplicate-id detection,
default fills, optional-field pruning. Hypothesis tries thousands of
permutations to surface edge cases the hand-written fixtures miss.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from engine.ai.dsl import parse, serialise

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "dsl" / "fixtures"


# ---------------------------------------------------------------------------
# Fixture-driven tests — start from the realistic specs the team uses.
# ---------------------------------------------------------------------------


def _all_fixtures() -> List[Path]:
    return sorted(p for p in FIXTURES_DIR.iterdir() if p.suffix == ".json")


def test_every_bundled_fixture_round_trips():
    """``serialise(parse(spec)) == parse(spec)`` for every shipped fixture.

    This is the *post-normalisation* identity — defaults filled in,
    IDs coerced to strings — so the round-trip applies after the first
    parse, which is what consumers will observe.
    """
    for fx in _all_fixtures():
        with open(fx, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
        normalised = parse(spec)
        # Round-trip from the normalised form.
        again = parse(serialise(normalised))
        assert again == normalised, f"Round-trip drift in {fx.name}"


# ---------------------------------------------------------------------------
# Hypothesis strategies for synthetic DSL specs
# ---------------------------------------------------------------------------

# Identifiers: snake_case alphanumeric, 1..16 chars.
_id_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_0123456789", min_size=1, max_size=16
)


def _id_strategy():
    return _id_chars.filter(lambda s: s[0].isalpha())


def _ema_or_sma_indicator(uid: str):
    return st.fixed_dictionaries(
        {
            "id": st.just(uid),
            "type": st.sampled_from(["sma", "ema"]),
            "period": st.integers(min_value=2, max_value=200),
        }
    )


def _atr_indicator(uid: str):
    return st.fixed_dictionaries(
        {
            "id": st.just(uid),
            "type": st.just("atr"),
            "period": st.integers(min_value=2, max_value=50),
        }
    )


def _indicator_strategy(uid: str):
    return st.one_of(_ema_or_sma_indicator(uid), _atr_indicator(uid))


@st.composite
def _spec_strategy(draw):
    """Generate a small but schema-valid DSL doc with 2-4 indicators."""
    n_inds = draw(st.integers(min_value=2, max_value=4))
    # Unique ids to avoid the duplicate-id rejection path.
    ids = draw(st.lists(_id_strategy(), min_size=n_inds, max_size=n_inds, unique=True))
    indicators = [draw(_indicator_strategy(i)) for i in ids]
    fast_id, slow_id = ids[0], ids[1]
    filters = [
        {"id": "bull", "type": "cross_above", "lhs": fast_id, "rhs": slow_id},
        {"id": "bear", "type": "cross_below", "lhs": fast_id, "rhs": slow_id},
    ]
    entries = [
        {"when": "bull", "do": "buy_at_next_open"},
        {"when": "bear", "do": "sell_at_next_open"},
    ]
    sl_pts = draw(st.integers(min_value=1, max_value=50))
    rr = draw(
        st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False)
    )
    risk_pct = draw(
        st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False)
    )
    return {
        "strategy": {
            "name": draw(_id_strategy()),
            "ltf": draw(st.sampled_from(["1m", "5m", "15m"])),
            "dsl_version": "1",
        },
        "indicators": indicators,
        "filters": filters,
        "entries": entries,
        "exits": [
            {"type": "fixed_pts_sl", "pts": sl_pts},
            {"type": "fixed_rr_tp", "rr": rr},
        ],
        "risk": {
            "risk_pct": risk_pct,
            "lot_cap": {"per_100k": draw(st.integers(min_value=1, max_value=50))},
            "max_open": 1,
        },
    }


@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(_spec_strategy())
def test_synthetic_specs_round_trip(spec):
    """``parse(serialise(parse(x))) == parse(x)`` over generated specs."""
    parsed = parse(spec)
    again = parse(serialise(parsed))
    assert again == parsed


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(_spec_strategy())
def test_serialise_preserves_required_keys(spec):
    """Serialise output must keep all top-level required sections — the
    normalised dict must remain *itself* schema-compliant after re-parse.
    """
    parsed = parse(spec)
    out = serialise(parsed)
    for k in ("strategy", "indicators", "filters", "entries", "exits", "risk"):
        assert k in out, f"serialise() dropped required key {k!r}"
    # Re-parse must succeed (no ValidationError raised).
    parse(out)


def test_round_trip_works_against_real_fixtures_via_serialise():
    """One more sweep: take the *normalised* form (post-parse), pass
    through serialise → parse — must equal the normalised form again.
    """
    for fx in _all_fixtures():
        with open(fx, "r", encoding="utf-8") as fh:
            normalised = parse(json.load(fh))
        again = parse(serialise(normalised))
        assert again == normalised, (
            f"Serialise drift detected against {os.path.basename(fx)}"
        )
