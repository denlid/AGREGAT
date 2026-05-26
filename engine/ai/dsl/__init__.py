"""DSL parser + JSON-Schema validator (Sprint 2.9).

See :mod:`engine.ai.dsl.parser` for the public API. The grammar
itself is documented in ``docs/dsl/grammar_v1.md``; this package
ships the executable form.
"""

from engine.ai.dsl.parser import (
    DSL_SCHEMA,
    DSLValidationError,
    parse,
    parse_file,
    serialise,
)

__all__ = [
    "DSL_SCHEMA",
    "DSLValidationError",
    "parse",
    "parse_file",
    "serialise",
]
