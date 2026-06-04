"""Overlay diff reconciliation helpers (Roadmap v2 / S4).

Replaces the clear+recreate pattern in _draw_strategy and _draw_user_items
with stable-ID-based diff reconciliation. Only changed overlays are
added/updated/removed, reducing scene churn on heavy annotated charts.

Expected gain: 2-4x on scenes with many strategy overlays or user drawings.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional


def cached_time64(d: Dict[str, Any], key: str, cache_key: str) -> Optional[Any]:
    """Return ``pd.to_datetime(d[key]).to_datetime64()`` with per-drawing caching.

    The converted value is cached on the drawing dict under ``cache_key``,
    together with the source string it was derived from. If the source string
    changes (e.g. the user drags an ROI and ``_on_roi_changed`` rewrites
    ``d["t1"]``), the cache auto-refreshes — so this is safe across edits.

    Avoids the repeated ``pd.to_datetime`` calls that dominated
    ``_draw_user_items`` on every redraw (BUG-2): each ~50-100µs string parse,
    × several per drawing × every redraw. Returns None if ``key`` is missing.
    """
    import pandas as pd

    src = d.get(key)
    if src is None:
        return None
    src_marker = cache_key + "__src"
    if d.get(src_marker) == src and cache_key in d:
        return d[cache_key]
    val = pd.to_datetime(src).to_datetime64()
    d[cache_key] = val
    d[src_marker] = src
    return val


def drawing_id(d: Dict[str, Any]) -> str:
    """Generate stable ID for a drawing dict.

    The ID is a hash of the drawing's immutable properties: type, geometry
    (time/price coordinates), and visual properties (color, width, text).
    Changes to these properties produce a different ID, triggering update.

    Args:
        d: Drawing dict with keys like type, t/t1/t2, p/p1/p2, color, width, text.

    Returns:
        Stable ID string (hex digest of hash).
    """
    # Build a canonical representation of the drawing for hashing.
    parts = [
        d.get("type", ""),
        str(d.get("t", d.get("t1", ""))),
        str(d.get("t2", "")),
        str(d.get("p", d.get("p1", ""))),
        str(d.get("p2", "")),
        str(d.get("color", "")),
        str(d.get("width", "")),
        str(d.get("text", "")),
        str(d.get("tag", "")),  # Strategy drawings have tags.
    ]
    canonical = "|".join(parts)
    return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()


def diff_overlays(
    old_ids: Dict[str, Any],
    new_drawings: list[Dict[str, Any]],
) -> tuple[list[str], list[str], list[Dict[str, Any]]]:
    """Compute diff between old overlay IDs and new drawings.

    Args:
        old_ids: Dict mapping drawing_id -> QGraphicsItem (current scene state).
        new_drawings: List of drawing dicts (desired scene state).

    Returns:
        Tuple of (to_remove, to_keep, to_add):
        - to_remove: List of IDs present in old but not in new (remove these items).
        - to_keep: List of IDs present in both (reuse these items).
        - to_add: List of drawing dicts not in old (create new items).
    """
    new_ids = {drawing_id(d): d for d in new_drawings}

    to_remove = [oid for oid in old_ids if oid not in new_ids]
    to_keep = [oid for oid in old_ids if oid in new_ids]
    to_add = [d for did, d in new_ids.items() if did not in old_ids]

    return to_remove, to_keep, to_add


def is_drawing_in_viewport(d: Dict[str, Any], vmin, vmax) -> bool:
    """Check if drawing intersects the visible time range [vmin, vmax].

    Args:
        d: Drawing dict with time coordinates (t/t1/t2).
        vmin: Minimum visible time (datetime64).
        vmax: Maximum visible time (datetime64).

    Returns:
        True if the drawing should be rendered, False if it's outside viewport.
    """
    import pandas as pd

    # Extract time coordinates (handle both cached _t_np and raw t fields).
    t1 = d.get("_t_np") or d.get("_t1_np")
    if t1 is None:
        tk = "t" if "t" in d else "t1"
        if tk not in d:
            return True  # No time info, assume visible (e.g., hline).
        t1 = pd.to_datetime(d[tk]).to_datetime64()

    # Single-point drawings (text, marker).
    if "t2" not in d and "_t2_np" not in d:
        return bool(vmin <= t1 <= vmax)

    # Two-point drawings (line, rect).
    t2 = d.get("_t2_np")
    if t2 is None:
        t2 = pd.to_datetime(d["t2"]).to_datetime64()

    # Drawing is visible if it intersects [vmin, vmax].
    return bool(not (t2 < vmin or t1 > vmax))
