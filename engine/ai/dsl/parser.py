"""DSL parser + JSON-Schema validator — Sprint 2.9.

This module is the executable form of the grammar paper-designed in
``docs/dsl/grammar_v1.md``. It exposes three public entry points:

* :func:`parse` — accept a Python dict (already YAML/JSON-decoded),
  validate it against the JSON Schema, and return a *normalised*
  dict that's safe to consume downstream. "Normalised" means default
  values are filled in and ID fields are coerced to strings.
* :func:`parse_file` — convenience wrapper that decodes a YAML or
  JSON file from disk and routes through :func:`parse`.
* :func:`serialise` — re-emit a parsed spec as a plain dict suitable
  for re-encoding to YAML/JSON. Round-trip identity (``parse(serialise(x))
  == x``) is part of the contract and is asserted by the unit tests.

We use the stdlib :mod:`re` for identifier validation and a tiny
hand-rolled JSON Schema validator (we avoid pulling in ``jsonschema``
to keep the engine package dependency-free — the schema we accept is
narrow enough that a few hundred lines of validation logic suffice).

The grammar covers the **30 v1 nodes** from ``grammar_v1.md``:

* indicators (10): ema, sma, atr, donchian, ott, pivots, pivots_close,
  cisd, fvg, time_of_day
* filters (8): cross_above, cross_below, break_above, break_below,
  in_zone, tf_window, weekday, not_eod, equals
* entries (4): buy_at_next_open, sell_at_next_open, cancel_pending,
  tag_trade
* exits (5): fixed_pts_sl, atr_mult_sl, pivot_sl, fixed_rr_tp,
  move_to_be
* risk (3): risk_pct, lot_cap, max_open
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence, Tuple, Union

# Identifier rule, lifted verbatim from grammar_v1.md §"Decisions to
# confirm" #3 — ASCII lowercase + digits + underscore, max 32 chars,
# can't shadow the reserved words used as DSL keywords elsewhere.
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_RESERVED_IDS = frozenset({"id", "type", "on", "do", "when"})

# Allowed values for enum-style fields. Kept as module constants so
# unit tests can introspect them without re-parsing the schema.
INDICATOR_TYPES = (
    "ema",
    "sma",
    "atr",
    "donchian",
    "ott",
    "pivots",
    "pivots_close",
    "cisd",
    "fvg",
    "time_of_day",
)
FILTER_TYPES = (
    "cross_above",
    "cross_below",
    "break_above",
    "break_below",
    "in_zone",
    "tf_window",
    "weekday",
    "not_eod",
    "equals",
)
ENTRY_ACTIONS = (
    "buy_at_next_open",
    "sell_at_next_open",
    "cancel_pending",
    "tag_trade",
)
EXIT_TYPES = (
    "fixed_pts_sl",
    "atr_mult_sl",
    "pivot_sl",
    "fixed_rr_tp",
    "move_to_be",
)
TIMEFRAME_RE = re.compile(r"^[1-9][0-9]{0,2}[smhdw]$")
DSL_VERSION = "1"


class DSLValidationError(ValueError):
    """Raised when a DSL document fails schema validation.

    Distinct from :class:`ValueError` so the AI Writer / GUI can
    intercept *just* DSL errors and route them to a structured
    diagnostics panel instead of a generic stack trace.
    """


# ---------------------------------------------------------------------------
# JSON Schema — narrow enough to validate by hand, broad enough to
# match every v1 example in ``docs/dsl/grammar_v1.md``.
# ---------------------------------------------------------------------------
DSL_SCHEMA: Dict[str, Any] = {
    "$id": "https://agregat.dev/schemas/dsl/v1.json",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AGREGAT DSL v1",
    "type": "object",
    "required": ["strategy", "indicators", "filters", "entries", "exits", "risk"],
    "additionalProperties": False,
    "properties": {
        "strategy": {
            "type": "object",
            "required": ["name", "ltf"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 64},
                "htf": {"type": "string", "pattern": TIMEFRAME_RE.pattern},
                "ltf": {"type": "string", "pattern": TIMEFRAME_RE.pattern},
                "dsl_version": {"type": "string", "const": DSL_VERSION},
            },
        },
        "indicators": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"enum": list(INDICATOR_TYPES)},
                    "on": {"enum": ["ltf", "htf"]},
                },
            },
        },
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"enum": list(FILTER_TYPES)},
                },
            },
        },
        "entries": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["when", "do"],
                "properties": {
                    "when": {},
                    "do": {"enum": list(ENTRY_ACTIONS)},
                },
            },
        },
        "exits": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {"enum": list(EXIT_TYPES)},
                },
            },
        },
        "risk": {
            "type": "object",
            "required": ["risk_pct"],
            "additionalProperties": False,
            "properties": {
                "risk_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "lot_cap": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "per_100k": {"type": "integer", "minimum": 1},
                    },
                },
                "max_open": {"type": "integer", "minimum": 1, "maximum": 1},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Tiny hand-rolled validator. Supports the JSON-Schema subset we use
# above: type, enum, const, minimum/maximum (exclusive variants),
# minLength/maxLength, pattern, required, additionalProperties,
# minItems, properties, items.
# ---------------------------------------------------------------------------
def _validate(node: Any, schema: Dict[str, Any], path: str = "$") -> None:
    """Walk ``node`` against ``schema`` and raise on the first error.

    The error message includes the JSON-pointer-like ``path`` that
    led to the failing node, so the AI Writer can highlight the
    exact field in its UI without re-walking the spec.
    """
    if "enum" in schema:
        if node not in schema["enum"]:
            raise DSLValidationError(f"{path}: value {node!r} not in enum {schema['enum']!r}")
    if "const" in schema and node != schema["const"]:
        raise DSLValidationError(f"{path}: expected const {schema['const']!r}, got {node!r}")
    if "type" in schema:
        _check_type(node, schema["type"], path)

    if isinstance(node, dict):
        if schema.get("required"):
            missing = [k for k in schema["required"] if k not in node]
            if missing:
                raise DSLValidationError(f"{path}: missing required fields {missing!r}")
        if schema.get("additionalProperties") is False:
            extras = set(node.keys()) - set((schema.get("properties") or {}).keys())
            if extras:
                raise DSLValidationError(f"{path}: unexpected fields {sorted(extras)!r}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in node and sub:
                _validate(node[key], sub, f"{path}.{key}")

    elif isinstance(node, list):
        items_schema = schema.get("items")
        if "minItems" in schema and len(node) < schema["minItems"]:
            raise DSLValidationError(f"{path}: array shorter than minItems {schema['minItems']}")
        if items_schema:
            for i, child in enumerate(node):
                _validate(child, items_schema, f"{path}[{i}]")

    elif isinstance(node, str):
        if "minLength" in schema and len(node) < schema["minLength"]:
            raise DSLValidationError(f"{path}: string shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(node) > schema["maxLength"]:
            raise DSLValidationError(f"{path}: string longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.match(schema["pattern"], node):
            raise DSLValidationError(
                f"{path}: string {node!r} does not match pattern {schema['pattern']!r}"
            )

    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in schema and node < schema["minimum"]:
            raise DSLValidationError(f"{path}: value {node!r} < minimum {schema['minimum']}")
        if "maximum" in schema and node > schema["maximum"]:
            raise DSLValidationError(f"{path}: value {node!r} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and node <= schema["exclusiveMinimum"]:
            raise DSLValidationError(
                f"{path}: value {node!r} <= exclusiveMinimum {schema['exclusiveMinimum']}"
            )


def _check_type(node: Any, expected: Union[str, Sequence[str]], path: str) -> None:
    """Map JSON-Schema type names to Python isinstance checks."""
    types = (expected,) if isinstance(expected, str) else tuple(expected)
    py_types: Tuple[type, ...] = ()
    allow_bool = False
    for t in types:
        if t == "string":
            py_types += (str,)
        elif t == "number":
            py_types += (int, float)
        elif t == "integer":
            py_types += (int,)
        elif t == "boolean":
            py_types += (bool,)
            allow_bool = True
        elif t == "object":
            py_types += (dict,)
        elif t == "array":
            py_types += (list,)
        elif t == "null":
            if node is None:
                return
        else:
            raise DSLValidationError(f"{path}: unknown type in schema: {t!r}")
    # Reject ``True``/``False`` when only "number"/"integer" is allowed —
    # Python treats bool as a subclass of int, but DSL semantics
    # don't.
    if isinstance(node, bool) and not allow_bool:
        raise DSLValidationError(f"{path}: boolean not allowed; expected {expected!r}")
    if not isinstance(node, py_types):
        raise DSLValidationError(f"{path}: expected type {expected!r}, got {type(node).__name__}")


# ---------------------------------------------------------------------------
# Identifier checks. The schema validates string shape; this catches
# the cross-cutting rules (uniqueness, dangling references).
# ---------------------------------------------------------------------------
def _validate_ids(spec: Dict[str, Any]) -> None:
    """Ensure every ``id`` is unique, well-formed, and not a reserved word."""
    seen: set[str] = set()
    for section in ("indicators", "filters"):
        for i, node in enumerate(spec.get(section, [])):
            ident = node.get("id")
            if not isinstance(ident, str) or not _ID_RE.match(ident):
                raise DSLValidationError(
                    f"$.{section}[{i}].id: {ident!r} is not a valid identifier "
                    "(must match [a-z][a-z0-9_]{0,31})"
                )
            if ident in _RESERVED_IDS:
                raise DSLValidationError(f"$.{section}[{i}].id: {ident!r} is reserved")
            if ident in seen:
                raise DSLValidationError(f"$.{section}[{i}].id: duplicate {ident!r}")
            seen.add(ident)


def _collect_id_refs(node: Any) -> Iterable[str]:
    """Yield every string value under keys named ``lhs``, ``rhs``, ``when``,
    ``anchor``, ``zone``, ``source`` — the spots a DSL spec references an
    earlier indicator/filter id by name. Lists and nested dicts are
    walked transparently."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key in ("lhs", "rhs", "anchor", "zone", "source", "when") and isinstance(val, str):
                yield val
            else:
                yield from _collect_id_refs(val)
    elif isinstance(node, list):
        for child in node:
            yield from _collect_id_refs(child)


def _validate_id_refs(spec: Dict[str, Any]) -> None:
    """Every ``lhs`` / ``rhs`` / ``anchor`` / ``zone`` / ``when`` / ``source``
    string must point at a previously declared id.

    Numeric / time literals show up in the same fields, so we tolerate
    non-id strings that look like literals (start with a digit, ``+``,
    ``-`` or contain ``:``)."""
    declared = {
        node["id"] for section in ("indicators", "filters") for node in spec.get(section, [])
    }
    declared.update(("close", "open", "high", "low", "volume"))
    for ref in _collect_id_refs(spec):
        # Treat literals (anything not matching our identifier rule)
        # as "intentionally not an id" — they're handled by the
        # downstream lowering pass, not by us.
        if not _ID_RE.match(ref):
            continue
        if ref not in declared:
            raise DSLValidationError(f"unknown id reference: {ref!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``spec`` against the v1 schema and return a deep-copied,
    normalised dict.

    Raises :class:`DSLValidationError` on the first violation found —
    error messages include a JSON-pointer-like path to the offending
    field so the AI Writer can highlight it.
    """
    if not isinstance(spec, dict):
        raise DSLValidationError(f"DSL root must be an object, got {type(spec).__name__}")
    out = copy.deepcopy(spec)
    _validate(out, DSL_SCHEMA)
    _validate_ids(out)
    _validate_id_refs(out)
    # Normalise: missing optional fields get defaults so downstream
    # consumers don't pepper their code with .get() calls.
    out["strategy"].setdefault("dsl_version", DSL_VERSION)
    out["risk"].setdefault("max_open", 1)
    return out


def serialise(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Re-emit ``spec`` as a plain dict — used by ``python_to_dsl`` and
    by the round-trip identity test.

    The contract is that ``parse(serialise(parse(x))) == parse(x)``,
    which we achieve trivially by deep-copying — there's no lossy
    transformation today. Future versions may strip default values
    or reorder keys; the round-trip identity test will catch any
    drift before it ships.
    """
    return copy.deepcopy(spec)


def parse_file(path: Union[str, Path]) -> Dict[str, Any]:
    """Decode a YAML or JSON file from ``path`` and route through :func:`parse`.

    YAML support is *optional* — we don't depend on PyYAML in the
    engine package. If ``path`` ends in ``.yml`` or ``.yaml`` we
    attempt the import lazily; if PyYAML isn't installed we surface a
    clear error rather than a stack trace.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    if suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:
            raise DSLValidationError(
                f"PyYAML is not installed but {p} has a YAML extension. "
                "Install with `pip install pyyaml` or save as .json."
            ) from e
        spec = yaml.safe_load(text)
    elif suffix == ".json":
        spec = json.loads(text)
    else:
        raise DSLValidationError(
            f"{p}: unsupported extension {suffix!r}; expected .json/.yml/.yaml"
        )
    if not isinstance(spec, dict):
        raise DSLValidationError(f"{p}: top-level must be a mapping, got {type(spec).__name__}")
    return parse(spec)


# Re-export the public surface; ``__init__.py`` mirrors this list.
__all__ = [
    "DSL_SCHEMA",
    "DSL_VERSION",
    "DSLValidationError",
    "ENTRY_ACTIONS",
    "EXIT_TYPES",
    "FILTER_TYPES",
    "INDICATOR_TYPES",
    "parse",
    "parse_file",
    "serialise",
]
