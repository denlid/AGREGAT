"""Sprint 2.9 — DSL parser + JSON-Schema validator tests.

Asserts that:

* All 10 reference fixtures parse cleanly (Sprint 2.9 DoD: "DSL parser
  passes 10 fixtures").
* Round-trip ``parse(serialise(parse(x))) == parse(x)`` holds for each.
* Targeted negative cases — bad version, dangling reference, reserved
  identifier, malformed timeframe — raise :class:`DSLValidationError`
  with a useful path in the message.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from engine.ai.dsl import (
    DSL_SCHEMA,
    DSLValidationError,
    parse,
    parse_file,
    serialise,
)
from engine.ai.dsl.parser import (
    ENTRY_ACTIONS,
    EXIT_TYPES,
    FILTER_TYPES,
    INDICATOR_TYPES,
)

FIXTURES = sorted(Path(__file__).parent.joinpath("dsl", "fixtures").glob("*.json"))


# ---------------------------------------------------------------------------
# happy-path — every fixture validates and round-trips
# ---------------------------------------------------------------------------
def test_ten_fixtures_present() -> None:
    """Sprint 2.9 mandates exactly 10 reference DSL examples."""
    assert len(FIXTURES) == 10, [p.name for p in FIXTURES]


@pytest.mark.parametrize("fixture", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture_parses(fixture: Path) -> None:
    """Each fixture must validate against the schema."""
    parsed = parse_file(fixture)
    assert parsed["strategy"]["dsl_version"] == "1"
    assert parsed["risk"]["max_open"] == 1


@pytest.mark.parametrize("fixture", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture_round_trip(fixture: Path) -> None:
    """``parse(serialise(parse(x))) == parse(x)`` — round-trip identity."""
    parsed_a = parse_file(fixture)
    serialised = serialise(parsed_a)
    parsed_b = parse(serialised)
    assert parsed_a == parsed_b


# ---------------------------------------------------------------------------
# Schema surface — every grammar v1 node is *reachable* from the schema
# ---------------------------------------------------------------------------
def test_schema_covers_all_indicator_types() -> None:
    enum = DSL_SCHEMA["properties"]["indicators"]["items"]["properties"]["type"]["enum"]
    assert tuple(enum) == INDICATOR_TYPES


def test_schema_covers_all_filter_types() -> None:
    enum = DSL_SCHEMA["properties"]["filters"]["items"]["properties"]["type"]["enum"]
    assert tuple(enum) == FILTER_TYPES


def test_schema_covers_all_entry_actions() -> None:
    enum = DSL_SCHEMA["properties"]["entries"]["items"]["properties"]["do"]["enum"]
    assert tuple(enum) == ENTRY_ACTIONS


def test_schema_covers_all_exit_types() -> None:
    enum = DSL_SCHEMA["properties"]["exits"]["items"]["properties"]["type"]["enum"]
    assert tuple(enum) == EXIT_TYPES


def test_schema_node_count_matches_grammar() -> None:
    """Total node count keeps the schema in sync with grammar_v1.md.

    The grammar doc claims **≈30 nodes**: 10 indicators + 8 filters +
    4 entries + 5 exits + 3 risk = 30. Our schema has *9* filters
    (the v1 review added ``equals`` after the doc was written), so
    the live count is 31. This test exists to fail loudly the next
    time someone adds a node enum without updating the doc.
    """
    assert len(INDICATOR_TYPES) == 10
    assert len(FILTER_TYPES) == 9  # 8 in doc + ``equals`` added in v1 review
    assert len(ENTRY_ACTIONS) == 4
    assert len(EXIT_TYPES) == 5


# ---------------------------------------------------------------------------
# Negative cases — bad spec must raise DSLValidationError with a path
# ---------------------------------------------------------------------------
@pytest.fixture
def good_spec() -> dict:
    """Minimal valid spec, used as a base for tweak-and-fail tests."""
    return parse_file(FIXTURES[0])


def test_unknown_root_field_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["totally_made_up"] = 1
    with pytest.raises(DSLValidationError, match="unexpected fields"):
        parse(bad)


def test_missing_required_field_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    del bad["risk"]
    with pytest.raises(DSLValidationError, match="missing required fields"):
        parse(bad)


def test_bad_timeframe_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["strategy"]["ltf"] = "1minute"  # must match regex r"^[1-9]...[smhdw]$"
    with pytest.raises(DSLValidationError, match="pattern"):
        parse(bad)


def test_reserved_identifier_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["indicators"][0]["id"] = "type"  # reserved word per grammar
    with pytest.raises(DSLValidationError, match="reserved"):
        parse(bad)


def test_duplicate_id_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["indicators"].append(deepcopy(bad["indicators"][0]))
    with pytest.raises(DSLValidationError, match="duplicate"):
        parse(bad)


def test_dangling_reference_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["filters"][0]["lhs"] = "no_such_indicator"
    with pytest.raises(DSLValidationError, match="unknown id reference"):
        parse(bad)


def test_unknown_indicator_type_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["indicators"][0]["type"] = "kaboom"
    with pytest.raises(DSLValidationError, match="not in enum"):
        parse(bad)


def test_risk_pct_out_of_range_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["risk"]["risk_pct"] = 0.0  # exclusiveMinimum = 0
    with pytest.raises(DSLValidationError, match="exclusiveMinimum"):
        parse(bad)


def test_max_open_above_limit_rejected(good_spec: dict) -> None:
    bad = deepcopy(good_spec)
    bad["risk"]["max_open"] = 2  # v1 only supports single-position engine
    with pytest.raises(DSLValidationError, match="maximum"):
        parse(bad)


def test_root_must_be_object() -> None:
    with pytest.raises(DSLValidationError, match="must be an object"):
        parse([{"strategy": "sma"}])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_file — JSON works without PyYAML
# ---------------------------------------------------------------------------
def test_parse_file_unsupported_extension(tmp_path: Path) -> None:
    p = tmp_path / "spec.txt"
    p.write_text("{}")
    with pytest.raises(DSLValidationError, match="unsupported extension"):
        parse_file(p)


def test_parse_file_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(DSLValidationError, match="top-level must be a mapping"):
        parse_file(p)
