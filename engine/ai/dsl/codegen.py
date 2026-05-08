"""DSL codegen — indicator (Phase 2) + filter/entry/exit (Phase 3).

Translates parsed DSL nodes into Python source snippets the
vectorised backtest harness can ``exec`` in a namespace already
holding ``close``, ``high``, ``low``, ``open_``, ``volume``,
``time`` and the indicator outputs from earlier nodes.

Phase 2 — five base indicators (EMA, ATR, Donchian, OTT, FVG).
Phase 3 — three node groups:

* **filters / conditions**:
    - ``cross_above`` / ``cross_below`` — series A crosses series B.
    - ``break_above`` / ``break_below`` — close breaks a level.
    - ``in_zone`` — close inside [low, high] of a zone.
    - ``time_of_day``  — bar timestamp is in window.
    - ``weekday``      — bar weekday is in allow-list.
* **entries / actions**:
    - ``buy_at_next_open`` / ``sell_at_next_open``.
    - ``move_to_be``     — move stop-loss to break-even.
    - ``trail_to_pivot`` — trail stop to last pivot.
* **exits**:
    - ``fixed_pts_sl``  — fixed-points stop-loss.
    - ``fixed_rr_tp``   — fixed risk-reward take-profit.
    - ``atr_mult_sl``   — ATR-multiple stop-loss.

The Phase 3 emitters return *boolean numpy arrays* (filters) or
*signal arrays* (entries / exits) the runtime composer can OR / AND
together to derive position-state arrays.
"""

from __future__ import annotations

from typing import Any, Dict, List


class CodegenError(ValueError):
    """Raised when codegen cannot produce valid code for a node."""


# ---------------------------------------------------------------------------
# Individual emitters
# ---------------------------------------------------------------------------


def _emit_ema(node: Dict[str, Any]) -> List[str]:
    """EMA (Exponential Moving Average)."""
    vid = node["id"]
    period = node.get("period", 20)
    source = node.get("source", "close")
    return [
        "import pandas as _pd",
        f"{vid} = _pd.Series({source}).ewm(span={period}, adjust=False).mean().values",
    ]


def _emit_atr(node: Dict[str, Any]) -> List[str]:
    """ATR (Average True Range)."""
    vid = node["id"]
    period = node.get("period", 14)
    return [
        "import pandas as _pd",
        f"_tr_{vid} = np.maximum(",
        "    high - low,",
        "    np.maximum(",
        "        np.abs(high - np.roll(close, 1)),",
        "        np.abs(low - np.roll(close, 1)),",
        "    ),",
        ")",
        f"_tr_{vid}[0] = high[0] - low[0]",
        f"{vid} = _pd.Series(_tr_{vid}).ewm(span={period}, adjust=False).mean().values",
    ]


def _emit_donchian(node: Dict[str, Any]) -> List[str]:
    """Donchian channel — upper/lower/mid arrays."""
    vid = node["id"]
    period = node.get("period", 20)
    return [
        "import pandas as _pd",
        f"{vid}_upper = _pd.Series(high).rolling({period}).max().values",
        f"{vid}_lower = _pd.Series(low).rolling({period}).min().values",
        f"{vid} = ({vid}_upper + {vid}_lower) / 2.0",
    ]


def _emit_ott(node: Dict[str, Any]) -> List[str]:
    """OTT (Optimised Trend Tracker) — percent-based envelope on EMA."""
    vid = node["id"]
    period = node.get("period", 2)
    pct = node.get("pct", 1.4)
    source = node.get("source", "close")
    return [
        "import pandas as _pd",
        f"_mavg_{vid} = _pd.Series({source}).ewm(span={period}, adjust=False).mean().values",
        f"_n_{vid} = len({source})",
        f"{vid}_long_stop = np.full(_n_{vid}, np.nan)",
        f"{vid}_short_stop = np.full(_n_{vid}, np.nan)",
        f"{vid} = np.full(_n_{vid}, np.nan)",
        f"for _i_{vid} in range(1, _n_{vid}):",
        f"    _up = _mavg_{vid}[_i_{vid}] * (1 + {pct}/100)",
        f"    _dn = _mavg_{vid}[_i_{vid}] * (1 - {pct}/100)",
        f"    _prev_ls = {vid}_long_stop[_i_{vid}-1] if not np.isnan({vid}_long_stop[_i_{vid}-1]) else 0",
        f"    _prev_ss = {vid}_short_stop[_i_{vid}-1] if not np.isnan({vid}_short_stop[_i_{vid}-1]) else 999999",
        f"    {vid}_long_stop[_i_{vid}] = max(_dn, _prev_ls) if _mavg_{vid}[_i_{vid}] > _prev_ls else _dn",
        f"    {vid}_short_stop[_i_{vid}] = min(_up, _prev_ss) if _mavg_{vid}[_i_{vid}] < _prev_ss else _up",
        f"    if _mavg_{vid}[_i_{vid}] > _prev_ss:",
        f"        {vid}[_i_{vid}] = {vid}_long_stop[_i_{vid}]",
        f"    elif _mavg_{vid}[_i_{vid}] < _prev_ls:",
        f"        {vid}[_i_{vid}] = {vid}_short_stop[_i_{vid}]",
        "    else:",
        f"        {vid}[_i_{vid}] = {vid}[_i_{vid}-1] if not np.isnan({vid}[_i_{vid}-1]) else {vid}_long_stop[_i_{vid}]",
    ]


def _emit_fvg(node: Dict[str, Any]) -> List[str]:
    """FVG (Fair Value Gap) — body-only detection from linear.py."""
    vid = node["id"]
    return [
        "from engine.core.linear import fvg_close_only",
        f"{vid} = fvg_close_only(open_, close)",
    ]


# ---------------------------------------------------------------------------
# Phase 3 — filter / condition emitters
# ---------------------------------------------------------------------------


def _emit_cross_above(node: Dict[str, Any]) -> List[str]:
    """Boolean array — series A crosses *above* series B at bar t."""
    vid = node["id"]
    a = node["a"]
    b = node["b"]
    return [
        f"_a_{vid} = np.asarray({a}, dtype='float64')",
        f"_b_{vid} = np.asarray({b}, dtype='float64')",
        f"{vid} = np.zeros_like(_a_{vid}, dtype=bool)",
        f"{vid}[1:] = (_a_{vid}[1:] > _b_{vid}[1:]) & (_a_{vid}[:-1] <= _b_{vid}[:-1])",
    ]


def _emit_cross_below(node: Dict[str, Any]) -> List[str]:
    """Boolean array — series A crosses *below* series B at bar t."""
    vid = node["id"]
    a = node["a"]
    b = node["b"]
    return [
        f"_a_{vid} = np.asarray({a}, dtype='float64')",
        f"_b_{vid} = np.asarray({b}, dtype='float64')",
        f"{vid} = np.zeros_like(_a_{vid}, dtype=bool)",
        f"{vid}[1:] = (_a_{vid}[1:] < _b_{vid}[1:]) & (_a_{vid}[:-1] >= _b_{vid}[:-1])",
    ]


def _emit_break_above(node: Dict[str, Any]) -> List[str]:
    """Boolean array — close strictly greater than a fixed level."""
    vid = node["id"]
    series = node.get("series", "close")
    level = node["level"]
    return [
        f"{vid} = np.asarray({series}, dtype='float64') > {float(level)!r}",
    ]


def _emit_break_below(node: Dict[str, Any]) -> List[str]:
    """Boolean array — close strictly less than a fixed level."""
    vid = node["id"]
    series = node.get("series", "close")
    level = node["level"]
    return [
        f"{vid} = np.asarray({series}, dtype='float64') < {float(level)!r}",
    ]


def _emit_in_zone(node: Dict[str, Any]) -> List[str]:
    """Boolean array — close inside the inclusive zone ``[low, high]``."""
    vid = node["id"]
    low = node["low"]
    high = node["high"]
    return [
        f"_lo_{vid} = float({low})",
        f"_hi_{vid} = float({high})",
        f"{vid} = (close >= _lo_{vid}) & (close <= _hi_{vid})",
    ]


def _emit_time_of_day(node: Dict[str, Any]) -> List[str]:
    """Boolean array — ``time`` is between ``start`` and ``end`` (inclusive).

    ``start`` / ``end`` are HH:MM strings. The runtime stores ``time``
    as ``np.datetime64`` so we extract minutes-of-day with NumPy.
    """
    vid = node["id"]
    start = str(node["start"])
    end = str(node["end"])
    return [
        f"_t_{vid} = np.asarray(time, dtype='datetime64[m]')",
        f"_mod_{vid} = (_t_{vid} - _t_{vid}.astype('datetime64[D]')).astype(int)",
        f"_start_{vid} = {_minutes_of(start)!r}",
        f"_end_{vid} = {_minutes_of(end)!r}",
        f"{vid} = (_mod_{vid} >= _start_{vid}) & (_mod_{vid} <= _end_{vid})",
    ]


def _emit_weekday(node: Dict[str, Any]) -> List[str]:
    """Boolean array — bar weekday is in the allow-list (0=Mon … 6=Sun)."""
    vid = node["id"]
    days = list(node.get("days", [0, 1, 2, 3, 4]))
    return [
        f"_t_{vid} = np.asarray(time, dtype='datetime64[D]')",
        # NumPy: (datetime64[D] - epoch_monday) % 7 → weekday (Mon=0)
        f"_wd_{vid} = ((_t_{vid}.astype('int64') - 4) % 7).astype(int)",
        f"{vid} = np.isin(_wd_{vid}, {days!r})",
    ]


# ---------------------------------------------------------------------------
# Phase 3 — entry / action emitters
# ---------------------------------------------------------------------------


def _emit_buy_next_open(node: Dict[str, Any]) -> List[str]:
    """Pending-order signal — fires a +1 action when ``when`` is true."""
    vid = node["id"]
    when = node["when"]
    return [
        f"{vid} = np.where(np.asarray({when}, dtype=bool), 1, 0).astype('int8')",
    ]


def _emit_sell_next_open(node: Dict[str, Any]) -> List[str]:
    """Pending-order signal — fires a −1 action when ``when`` is true."""
    vid = node["id"]
    when = node["when"]
    return [
        f"{vid} = np.where(np.asarray({when}, dtype=bool), -1, 0).astype('int8')",
    ]


def _emit_move_to_be(node: Dict[str, Any]) -> List[str]:
    """Boolean signal — when true, runtime moves SL to break-even."""
    vid = node["id"]
    when = node["when"]
    return [
        f"{vid} = np.asarray({when}, dtype=bool)",
    ]


def _emit_trail_to_pivot(node: Dict[str, Any]) -> List[str]:
    """Per-bar trailing stop level — emits an array of pivot prices."""
    vid = node["id"]
    pivots = node["pivots"]
    return [
        f"{vid} = np.asarray({pivots}, dtype='float64')",
    ]


# ---------------------------------------------------------------------------
# Phase 3 — exit emitters
# ---------------------------------------------------------------------------


def _emit_fixed_pts_sl(node: Dict[str, Any]) -> List[str]:
    """Fixed-points stop-loss distance from entry."""
    vid = node["id"]
    pts = float(node["pts"])
    return [
        f"{vid} = float({pts!r})",
    ]


def _emit_fixed_rr_tp(node: Dict[str, Any]) -> List[str]:
    """Fixed risk-reward take-profit multiple."""
    vid = node["id"]
    rr = float(node["rr"])
    return [
        f"{vid} = float({rr!r})",
    ]


def _emit_atr_mult_sl(node: Dict[str, Any]) -> List[str]:
    """ATR-multiple stop-loss — emits a per-bar SL distance array."""
    vid = node["id"]
    atr = node["atr"]
    mult = float(node["mult"])
    return [
        f"{vid} = np.asarray({atr}, dtype='float64') * {mult!r}",
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minutes_of(hhmm: str) -> int:
    """Parse ``HH:MM`` to minutes-of-day. Raises CodegenError on bad input."""
    try:
        h, m = hhmm.split(":")
        h_int = int(h)
        m_int = int(m)
    except ValueError as exc:
        raise CodegenError(f"time_of_day expects HH:MM, got {hhmm!r}") from exc
    if not (0 <= h_int < 24 and 0 <= m_int < 60):
        raise CodegenError(f"time_of_day out of range: {hhmm!r}")
    return h_int * 60 + m_int


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------

_EMITTERS = {
    # Phase 2 — indicators
    "ema": _emit_ema,
    "atr": _emit_atr,
    "donchian": _emit_donchian,
    "ott": _emit_ott,
    "fvg": _emit_fvg,
}

_FILTER_EMITTERS = {
    "cross_above": _emit_cross_above,
    "cross_below": _emit_cross_below,
    "break_above": _emit_break_above,
    "break_below": _emit_break_below,
    "in_zone": _emit_in_zone,
    "time_of_day": _emit_time_of_day,
    "weekday": _emit_weekday,
}

_ENTRY_EMITTERS = {
    "buy_at_next_open": _emit_buy_next_open,
    "sell_at_next_open": _emit_sell_next_open,
    "move_to_be": _emit_move_to_be,
    "trail_to_pivot": _emit_trail_to_pivot,
}

_EXIT_EMITTERS = {
    "fixed_pts_sl": _emit_fixed_pts_sl,
    "fixed_rr_tp": _emit_fixed_rr_tp,
    "atr_mult_sl": _emit_atr_mult_sl,
}


def codegen_indicator(node: Dict[str, Any]) -> List[str]:
    """Generate Python source lines for a single DSL indicator node.

    Parameters
    ----------
    node
        A parsed indicator dict with at least ``{"id": ..., "type": ...}``.

    Returns
    -------
    List[str]
        Python source lines (un-indented).

    Raises
    ------
    CodegenError
        If the indicator type has no emitter registered.
    """
    itype = node.get("type", "")
    emitter = _EMITTERS.get(itype)
    if emitter is None:
        raise CodegenError(
            f"No codegen emitter for indicator type {itype!r}. "
            f"Supported: {sorted(_EMITTERS.keys())}"
        )
    return emitter(node)


def codegen_indicators(indicators: List[Dict[str, Any]]) -> str:
    """Generate combined Python source for a list of DSL indicator nodes."""
    lines: List[str] = []
    for node in indicators:
        lines.append(f"# --- {node.get('type', '?')}: {node.get('id', '?')} ---")
        lines.extend(codegen_indicator(node))
        lines.append("")
    return "\n".join(lines)


def codegen_filter(node: Dict[str, Any]) -> List[str]:
    """Generate Python source lines for a Phase 3 filter / condition node."""
    ftype = node.get("type", "")
    emitter = _FILTER_EMITTERS.get(ftype)
    if emitter is None:
        raise CodegenError(
            f"No codegen emitter for filter type {ftype!r}. "
            f"Supported: {sorted(_FILTER_EMITTERS.keys())}"
        )
    return emitter(node)


def codegen_entry(node: Dict[str, Any]) -> List[str]:
    """Generate Python source lines for a Phase 3 entry / action node."""
    etype = node.get("type", "")
    emitter = _ENTRY_EMITTERS.get(etype)
    if emitter is None:
        raise CodegenError(
            f"No codegen emitter for entry type {etype!r}. "
            f"Supported: {sorted(_ENTRY_EMITTERS.keys())}"
        )
    return emitter(node)


def codegen_exit(node: Dict[str, Any]) -> List[str]:
    """Generate Python source lines for a Phase 3 exit node."""
    xtype = node.get("type", "")
    emitter = _EXIT_EMITTERS.get(xtype)
    if emitter is None:
        raise CodegenError(
            f"No codegen emitter for exit type {xtype!r}. "
            f"Supported: {sorted(_EXIT_EMITTERS.keys())}"
        )
    return emitter(node)


def codegen_program(
    indicators: List[Dict[str, Any]],
    filters: List[Dict[str, Any]] | None = None,
    entries: List[Dict[str, Any]] | None = None,
    exits: List[Dict[str, Any]] | None = None,
) -> str:
    """Combine indicator / filter / entry / exit codegen into one program."""
    lines: List[str] = []
    lines.append(codegen_indicators(indicators))
    for label, group, emit in (
        ("filter", filters or [], codegen_filter),
        ("entry", entries or [], codegen_entry),
        ("exit", exits or [], codegen_exit),
    ):
        for node in group:
            lines.append(f"# --- {label} {node.get('type', '?')}: {node.get('id', '?')} ---")
            lines.extend(emit(node))
            lines.append("")
    return "\n".join(lines)


__all__ = [
    "CodegenError",
    "codegen_indicator",
    "codegen_indicators",
    "codegen_filter",
    "codegen_entry",
    "codegen_exit",
    "codegen_program",
]
