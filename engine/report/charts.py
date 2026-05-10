"""Pure-Python SVG chart generators for the tear-sheet (Phase 3.1).

Why inline SVG (and not matplotlib / pyqtgraph)?
    * The HTML report must be a single self-contained file the user can
      email / commit / view offline. matplotlib would force a PNG side-car
      file or base64-blob; SVG keeps the document fully scalable & native.
    * No GUI dependency — these helpers run inside the engine layer and
      can be unit-tested in CI without a Qt/X11 server.
    * SVGs scale crisply when the report is exported to PDF via WeasyPrint
      (it understands SVG natively).

All functions return a single ``<svg ...>...</svg>`` string with no
external references. The caller embeds the string directly into the HTML.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Styling constants — kept in sync with engine/report/themes.py palettes.
# ---------------------------------------------------------------------------
GRID_COLOR = "#1E1E28"
AXIS_COLOR = "#3A3A42"
TEXT_COLOR = "#9A9AA8"
POSITIVE = "#26A69A"
NEGATIVE = "#EF5350"
NEUTRAL = "#5C8AFF"
P5_COLOR = "#FF9800"
P95_COLOR = "#42A5F5"

# Keep the chart container size constant — the report layout assumes it.
DEFAULT_W = 720
DEFAULT_H = 220


def _empty_chart(text: str = "No data", w: int = DEFAULT_W, h: int = DEFAULT_H) -> str:
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<rect width="{w}" height="{h}" fill="transparent"/>'
        f'<text x="{w / 2}" y="{h / 2}" fill="{TEXT_COLOR}" '
        f'font-size="13" text-anchor="middle" dominant-baseline="central">{text}</text>'
        "</svg>"
    )


def _scale(
    values: Sequence[float], lo: float, hi: float, top: float, bot: float
) -> List[float]:
    rng = hi - lo if hi > lo else 1.0
    return [bot - (v - lo) / rng * (bot - top) for v in values]


# ---------------------------------------------------------------------------
# Phase 6.1 perf helpers — downsampling + vectorised polygon-string build
# ---------------------------------------------------------------------------

# Maximum number of points we render per chart. Above this the chart is
# downsampled — the canvas is only ``DEFAULT_W = 720`` pixels wide, so
# rendering more than ~2k points produces sub-pixel detail no human eye
# can resolve while ballooning the SVG payload by 10-100×.
_DOWNSAMPLE_MAX_POINTS = 2_000


def _downsample_minmax(
    arr: np.ndarray, max_points: int = _DOWNSAMPLE_MAX_POINTS
) -> np.ndarray:
    """Bucketise ``arr`` into ``max_points`` buckets and emit min+max per
    bucket interleaved. Preserves the visual silhouette (peaks and
    troughs) far better than naive stride-sampling.

    Returns the array unchanged when ``arr.size <= max_points``.
    """
    n = arr.size
    if n <= max_points:
        return arr
    n_buckets = max_points // 2
    if n_buckets < 2:
        return arr
    edges = np.linspace(0, n, n_buckets + 1, dtype=int)
    out = np.empty(n_buckets * 2, dtype=arr.dtype)
    for i in range(n_buckets):
        seg = arr[edges[i] : edges[i + 1]]
        if seg.size == 0:
            out[2 * i] = arr[min(edges[i], n - 1)]
            out[2 * i + 1] = out[2 * i]
        else:
            out[2 * i] = seg.min()
            out[2 * i + 1] = seg.max()
    return out


def _polygon_points(xs: np.ndarray, ys: np.ndarray) -> str:
    """Vectorised "x1,y1 x2,y2 …" string builder.

    The previous implementation built the string via a Python ``zip`` +
    f-string loop which is ``O(n)`` Python-level — for a 50k-trade
    equity curve that was ~150 ms per chart and the report draws
    five+ such curves. Using NumPy formatting is ~20× faster and
    pushes most of the work into C.
    """
    if xs.size == 0:
        return ""
    coords = np.empty(xs.size * 2, dtype=xs.dtype)
    coords[0::2] = xs
    coords[1::2] = ys
    # ``np.char.mod`` and ``ndarray.tolist()`` of formatted floats both
    # have similar perf — pick the one with the cleaner output. ``%.1f``
    # matches the existing string format exactly so we don't churn
    # snapshot tests.
    formatted = np.char.mod("%.1f", coords)
    pairs = formatted.reshape(-1, 2)
    return " ".join(",".join(p) for p in pairs.tolist())


# ---------------------------------------------------------------------------
# Equity curve (with drawdown overlay below)
# ---------------------------------------------------------------------------


def equity_curve_svg(
    equity: Sequence[float],
    *,
    initial_equity: Optional[float] = None,
    w: int = DEFAULT_W,
    h: int = DEFAULT_H,
) -> str:
    """Top-half: equity line filled by sign vs initial equity.
    Bottom-half: drawdown underwater overlay (red fill below 0%).
    """
    if not equity or len(equity) < 2:
        return _empty_chart("No equity data", w, h)
    eq = np.asarray(list(equity), dtype=float)
    init = float(initial_equity) if initial_equity is not None else float(eq[0])
    pad_l, pad_r, pad_t, pad_b = 8, 8, 6, 6
    chart_top = pad_t
    chart_bot = h - pad_b
    eq_top = chart_top
    eq_bot = chart_top + int((chart_bot - chart_top) * 0.65)
    dd_top = eq_bot + 6
    dd_bot = chart_bot

    eq_min = float(eq.min())
    eq_max = float(eq.max())
    if eq_max == eq_min:
        eq_max = eq_min + 1

    # Drawdown overlay (computed on FULL series so the worst DD is exact).
    peaks = np.maximum.accumulate(eq)
    safe = np.where(peaks > 0, peaks, 1.0)
    dd_pct = (eq - peaks) / safe * 100.0
    dd_min = float(dd_pct.min())
    if dd_min == 0.0:
        dd_min = -0.01

    # Phase 6.1 perf: downsample BEFORE the SVG-string build so we never
    # emit more than ~2k polygon vertices per chart even on 50k-trade
    # equity curves. The viewport is only 720 px wide so this is loss-
    # free at viewing resolution.
    eq_ds = _downsample_minmax(eq)
    dd_ds = _downsample_minmax(dd_pct)
    n_ds = eq_ds.size
    xs_arr = pad_l + np.linspace(0.0, 1.0, n_ds) * (w - pad_l - pad_r)
    ys_arr = eq_bot - (eq_ds - eq_min) / (eq_max - eq_min) * (eq_bot - eq_top)
    n_dd = dd_ds.size
    xs_dd = pad_l + np.linspace(0.0, 1.0, n_dd) * (w - pad_l - pad_r)
    dd_ys_arr = dd_bot - (dd_ds - dd_min) / (0.0 - dd_min) * (dd_bot - dd_top)

    color = POSITIVE if eq[-1] >= init else NEGATIVE
    poly = _polygon_points(xs_arr, ys_arr)
    fill = f"{xs_arr[0]:.1f},{eq_bot:.1f} " + poly + f" {xs_arr[-1]:.1f},{eq_bot:.1f}"
    dd_poly = _polygon_points(xs_dd, dd_ys_arr)
    dd_fill = (
        f"{xs_dd[0]:.1f},{dd_top:.1f} " + dd_poly + f" {xs_dd[-1]:.1f},{dd_top:.1f}"
    )
    initial_y = eq_bot - (init - eq_min) / (eq_max - eq_min) * (eq_bot - eq_top)
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"<defs>"
        f'<linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.30"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
        f"</linearGradient>"
        f'<linearGradient id="ddg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{NEGATIVE}" stop-opacity="0.05"/>'
        f'<stop offset="100%" stop-color="{NEGATIVE}" stop-opacity="0.30"/>'
        f"</linearGradient>"
        f"</defs>"
        # Initial-equity baseline
        f'<line x1="{pad_l}" y1="{initial_y:.1f}" x2="{w - pad_r}" y2="{initial_y:.1f}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1" stroke-dasharray="3,3"/>'
        # Equity polygon
        f'<polygon points="{fill}" fill="url(#eqg)"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>'
        # Drawdown polygon
        f'<polygon points="{dd_fill}" fill="url(#ddg)"/>'
        f'<polyline points="{dd_poly}" fill="none" stroke="{NEGATIVE}" stroke-width="1"/>'
        f'<text x="{pad_l + 4}" y="{eq_top + 12}" fill="{TEXT_COLOR}" font-size="10">Equity</text>'
        f'<text x="{pad_l + 4}" y="{dd_top + 11}" fill="{TEXT_COLOR}" font-size="10">Underwater</text>'
        "</svg>"
    )


# ---------------------------------------------------------------------------
# Underwater chart (% drawdown over time, area)
# ---------------------------------------------------------------------------


def underwater_svg(
    dd_pct: Sequence[float],
    *,
    w: int = DEFAULT_W,
    h: int = 160,
) -> str:
    arr = np.asarray(list(dd_pct), dtype=float)
    if arr.size < 2:
        return _empty_chart("No drawdown data", w, h)
    pad_l, pad_r, pad_t, pad_b = 36, 8, 10, 18
    lo = float(min(arr.min(), -0.01))

    # Phase 6.1: downsample + vectorised polygon build (see charts.py
    # module-level helpers for rationale).
    arr_ds = _downsample_minmax(arr)
    n_ds = arr_ds.size
    xs_arr = pad_l + np.linspace(0.0, 1.0, n_ds) * (w - pad_l - pad_r)
    bot = h - pad_b
    ys_arr = bot - (arr_ds - lo) / (0.0 - lo) * (bot - pad_t)
    poly = _polygon_points(xs_arr, ys_arr)
    fill = f"{xs_arr[0]:.1f},{pad_t:.1f} " + poly + f" {xs_arr[-1]:.1f},{pad_t:.1f}"

    # 5 y-ticks
    ticks_html = ""
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = lo * frac
        y = pad_t + (h - pad_b - pad_t) * frac
        ticks_html += (
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" '
            f'stroke="{GRID_COLOR}" stroke-width="0.5"/>'
            f'<text x="{pad_l - 4}" y="{y + 3:.1f}" fill="{TEXT_COLOR}" '
            f'font-size="9" text-anchor="end">{v:.1f}%</text>'
        )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<defs><linearGradient id="uwg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{NEGATIVE}" stop-opacity="0.05"/>'
        f'<stop offset="100%" stop-color="{NEGATIVE}" stop-opacity="0.40"/>'
        f"</linearGradient></defs>"
        f"{ticks_html}"
        f'<polygon points="{fill}" fill="url(#uwg)"/>'
        f'<polyline points="{poly}" fill="none" stroke="{NEGATIVE}" stroke-width="1.4"/>'
        f'<text x="{pad_l}" y="{h - 4}" fill="{TEXT_COLOR}" font-size="10">trades →</text>'
        "</svg>"
    )


# ---------------------------------------------------------------------------
# Monthly returns heatmap (year × month grid)
# ---------------------------------------------------------------------------

_MONTH_LABELS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def monthly_heatmap_svg(
    years: Sequence[int],
    pct_grid: np.ndarray,
    *,
    cell_w: int = 50,
    cell_h: int = 26,
) -> str:
    """Render a year × month grid; positive cells in green, negative red.

    Color intensity is proportional to ``|value| / max(|grid|)`` so the
    eye sees the strongest months at a glance — same convention as
    QuantStats / pyfolio.
    """
    if pct_grid is None or pct_grid.size == 0 or len(years) == 0:
        return _empty_chart("No monthly data", w=12 * cell_w + 80, h=cell_h + 40)
    pad_l = 60
    pad_t = 30
    n_years = len(years)
    w = pad_l + 12 * cell_w + 80
    h = pad_t + n_years * cell_h + 12
    max_abs = float(np.max(np.abs(pct_grid))) if pct_grid.size else 1.0
    max_abs = max_abs or 1.0

    # Header row
    header = ""
    for j, label in enumerate(_MONTH_LABELS):
        x = pad_l + j * cell_w + cell_w / 2
        header += (
            f'<text x="{x:.1f}" y="{pad_t - 8}" fill="{TEXT_COLOR}" '
            f'font-size="10" text-anchor="middle">{label}</text>'
        )
    header += (
        f'<text x="{pad_l + 12 * cell_w + 40:.1f}" y="{pad_t - 8}" fill="{TEXT_COLOR}" '
        f'font-size="10" text-anchor="middle">YTD</text>'
    )

    # Cells
    body = ""
    year_totals_pct = np.zeros(n_years, dtype=float)
    for i in range(n_years):
        prod = 1.0
        for m in range(12):
            prod *= 1.0 + pct_grid[i, m] / 100.0
        year_totals_pct[i] = (prod - 1.0) * 100.0

    for i, y in enumerate(years):
        body += (
            f'<text x="{pad_l - 8}" y="{pad_t + i * cell_h + cell_h / 2 + 3:.1f}" '
            f'fill="{TEXT_COLOR}" font-size="10" text-anchor="end">{int(y)}</text>'
        )
        for j in range(12):
            v = float(pct_grid[i, j])
            if abs(v) < 1e-9:
                color = "#1A1A22"
                txt_color = "#5A5A6E"
            else:
                base = POSITIVE if v >= 0 else NEGATIVE
                alpha = min(0.95, 0.15 + 0.80 * (abs(v) / max_abs))
                color = _hex_with_alpha(base, alpha)
                txt_color = "#E8E8EE"
            x = pad_l + j * cell_w
            yy = pad_t + i * cell_h
            body += (
                f'<rect x="{x:.1f}" y="{yy:.1f}" width="{cell_w - 2}" '
                f'height="{cell_h - 2}" rx="3" fill="{color}"/>'
                f'<text x="{x + cell_w / 2:.1f}" y="{yy + cell_h / 2 + 3:.1f}" '
                f'fill="{txt_color}" font-size="10" text-anchor="middle">'
                f"{v:+.1f}</text>"
            )
        # YTD column
        v = float(year_totals_pct[i])
        base = POSITIVE if v >= 0 else NEGATIVE
        alpha = min(0.95, 0.20 + 0.80 * (abs(v) / max_abs)) if max_abs else 0.5
        color = _hex_with_alpha(base, alpha)
        x = pad_l + 12 * cell_w + 8
        yy = pad_t + i * cell_h
        body += (
            f'<rect x="{x:.1f}" y="{yy:.1f}" width="68" height="{cell_h - 2}" '
            f'rx="3" fill="{color}"/>'
            f'<text x="{x + 34:.1f}" y="{yy + cell_h / 2 + 3:.1f}" fill="#E8E8EE" '
            f'font-size="10" text-anchor="middle" font-weight="600">{v:+.2f}%</text>'
        )

    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{header}{body}</svg>'


def _hex_with_alpha(hex_color: str, alpha: float) -> str:
    """Convert ``#RRGGBB`` to ``rgba(r,g,b,a)`` so SVG ``fill`` accepts it."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    a = max(0.0, min(1.0, alpha))
    return f"rgba({r},{g},{b},{a:.3f})"


# ---------------------------------------------------------------------------
# Rolling Sharpe line chart
# ---------------------------------------------------------------------------


def rolling_sharpe_svg(
    values: Sequence[float],
    *,
    mean_value: float = 0.0,
    w: int = DEFAULT_W,
    h: int = 160,
) -> str:
    arr = np.asarray(list(values), dtype=float)
    finite_mask = np.isfinite(arr)
    if finite_mask.sum() < 2:
        return _empty_chart("Need ≥ window trades for rolling Sharpe", w, h)
    pad_l, pad_r, pad_t, pad_b = 40, 8, 10, 18
    finite = arr[finite_mask]
    lo, hi = float(finite.min()), float(finite.max())
    if hi == lo:
        hi = lo + 0.5

    # Phase 6.1: downsample finite series, then map back to x-coords. We
    # only render finite points so a NaN warmup region naturally
    # produces a gap.
    finite_indices = np.flatnonzero(finite_mask)
    arr_ds = _downsample_minmax(finite)
    if arr_ds.size == 0:
        return _empty_chart("Need ≥ window trades", w, h)
    # Map downsampled samples to the *original* x-axis range so the
    # dashed mean / zero lines still align visually.
    n = arr.size
    x_anchors = np.linspace(finite_indices[0], finite_indices[-1], arr_ds.size)
    xs_arr = pad_l + (x_anchors / max(n - 1, 1)) * (w - pad_l - pad_r)
    bot = h - pad_b
    ys_arr = bot - (arr_ds - lo) / (hi - lo) * (bot - pad_t)
    poly = _polygon_points(xs_arr, ys_arr)
    if not poly:
        return _empty_chart("Need ≥ window trades", w, h)

    # Zero line + mean line
    zero_y = _scale([0.0], lo, hi, pad_t, h - pad_b)[0]
    mean_y = _scale([float(mean_value)], lo, hi, pad_t, h - pad_b)[0]
    ticks = ""
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = hi - (hi - lo) * frac
        y = pad_t + (h - pad_b - pad_t) * frac
        ticks += (
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" '
            f'stroke="{GRID_COLOR}" stroke-width="0.5"/>'
            f'<text x="{pad_l - 4}" y="{y + 3:.1f}" fill="{TEXT_COLOR}" '
            f'font-size="9" text-anchor="end">{v:.2f}</text>'
        )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"{ticks}"
        f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{w - pad_r}" y2="{zero_y:.1f}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
        f'<line x1="{pad_l}" y1="{mean_y:.1f}" x2="{w - pad_r}" y2="{mean_y:.1f}" '
        f'stroke="{NEUTRAL}" stroke-width="1" stroke-dasharray="4,3" opacity="0.6"/>'
        f'<polyline points="{poly}" fill="none" stroke="{NEUTRAL}" stroke-width="1.6"/>'
        f'<text x="{pad_l + 4}" y="{pad_t + 11}" fill="{TEXT_COLOR}" font-size="10">'
        f"Rolling Sharpe (mean={mean_value:.2f})</text>"
        "</svg>"
    )


# ---------------------------------------------------------------------------
# Monte Carlo confidence band (p5 / p50 / p95)
# ---------------------------------------------------------------------------


def monte_carlo_band_svg(
    p5: Sequence[float],
    p50: Sequence[float],
    p95: Sequence[float],
    *,
    initial_equity: Optional[float] = None,
    w: int = DEFAULT_W,
    h: int = 200,
) -> str:
    p5_a = np.asarray(list(p5), dtype=float)
    p50_a = np.asarray(list(p50), dtype=float)
    p95_a = np.asarray(list(p95), dtype=float)
    if p5_a.size < 2 or p5_a.size != p50_a.size or p50_a.size != p95_a.size:
        return _empty_chart("Monte Carlo needs ≥ 10 trades to bootstrap", w, h)
    pad_l, pad_r, pad_t, pad_b = 44, 8, 10, 18
    lo = float(min(p5_a.min(), p50_a.min(), p95_a.min()))
    hi = float(max(p5_a.max(), p50_a.max(), p95_a.max()))
    if hi == lo:
        hi = lo + 1.0

    # Phase 6.1: downsample all three series consistently — using the
    # same bucket count keeps the band polygon's two halves aligned.
    p5_ds = _downsample_minmax(p5_a)
    p50_ds = _downsample_minmax(p50_a)
    p95_ds = _downsample_minmax(p95_a)
    n_ds = min(p5_ds.size, p50_ds.size, p95_ds.size)
    if p5_ds.size != n_ds or p50_ds.size != n_ds or p95_ds.size != n_ds:
        # Different downsampled sizes (very unlikely for same-length
        # inputs) — re-bucket all three to the smaller count.
        p5_ds = _downsample_minmax(p5_a, max_points=n_ds)
        p50_ds = _downsample_minmax(p50_a, max_points=n_ds)
        p95_ds = _downsample_minmax(p95_a, max_points=n_ds)
        n_ds = min(p5_ds.size, p50_ds.size, p95_ds.size)

    xs_arr = pad_l + np.linspace(0.0, 1.0, n_ds) * (w - pad_l - pad_r)
    bot = h - pad_b
    p5_y = bot - (p5_ds - lo) / (hi - lo) * (bot - pad_t)
    p50_y = bot - (p50_ds - lo) / (hi - lo) * (bot - pad_t)
    p95_y = bot - (p95_ds - lo) / (hi - lo) * (bot - pad_t)
    band = (
        _polygon_points(xs_arr, p95_y) + " " + _polygon_points(xs_arr[::-1], p5_y[::-1])
    )
    poly_p50 = _polygon_points(xs_arr, p50_y)
    poly_p5 = _polygon_points(xs_arr, p5_y)
    poly_p95 = _polygon_points(xs_arr, p95_y)
    init_html = ""
    if initial_equity is not None:
        iy = _scale([float(initial_equity)], lo, hi, pad_t, h - pad_b)[0]
        init_html = (
            f'<line x1="{pad_l}" y1="{iy:.1f}" x2="{w - pad_r}" y2="{iy:.1f}" '
            f'stroke="{AXIS_COLOR}" stroke-width="1" stroke-dasharray="3,3"/>'
        )
    ticks = ""
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = hi - (hi - lo) * frac
        y = pad_t + (h - pad_b - pad_t) * frac
        ticks += (
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" '
            f'stroke="{GRID_COLOR}" stroke-width="0.5"/>'
            f'<text x="{pad_l - 4}" y="{y + 3:.1f}" fill="{TEXT_COLOR}" '
            f'font-size="9" text-anchor="end">{v:,.0f}</text>'
        )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"{ticks}"
        f"{init_html}"
        f'<polygon points="{band}" fill="{NEUTRAL}" opacity="0.12"/>'
        f'<polyline points="{poly_p5}" fill="none" stroke="{P5_COLOR}" stroke-width="1.2" '
        f'stroke-dasharray="3,2"/>'
        f'<polyline points="{poly_p95}" fill="none" stroke="{P95_COLOR}" stroke-width="1.2" '
        f'stroke-dasharray="3,2"/>'
        f'<polyline points="{poly_p50}" fill="none" stroke="{NEUTRAL}" stroke-width="1.8"/>'
        f'<text x="{pad_l + 4}" y="{pad_t + 11}" fill="{P5_COLOR}" font-size="10">p5</text>'
        f'<text x="{pad_l + 32}" y="{pad_t + 11}" fill="{NEUTRAL}" font-size="10">p50</text>'
        f'<text x="{pad_l + 64}" y="{pad_t + 11}" fill="{P95_COLOR}" font-size="10">p95</text>'
        "</svg>"
    )


# ---------------------------------------------------------------------------
# Exit-reason breakdown — donut + legend
# ---------------------------------------------------------------------------

_EXIT_PALETTE = [
    "#26A69A",
    "#EF5350",
    "#FF9800",
    "#5C8AFF",
    "#AB47BC",
    "#42A5F5",
    "#FFCA28",
    "#7E57C2",
    "#26C6DA",
    "#9CCC65",
]


def exit_reason_donut_svg(
    rows: Iterable[Tuple[str, int]],
    *,
    w: int = 320,
    h: int = 220,
) -> str:
    """``rows`` is iterable of ``(reason, count)`` tuples."""
    items = [(r, int(c)) for r, c in rows if int(c) > 0]
    if not items:
        return _empty_chart("No exits", w, h)
    total = sum(c for _, c in items)
    cx, cy, r_outer, r_inner = 90, 110, 80, 50
    start_angle = -np.pi / 2  # start from 12 o'clock
    paths = ""
    legend = ""
    for i, (reason, count) in enumerate(items):
        frac = count / total
        end_angle = start_angle + frac * 2 * np.pi
        large = 1 if (end_angle - start_angle) > np.pi else 0
        x1 = cx + r_outer * np.cos(start_angle)
        y1 = cy + r_outer * np.sin(start_angle)
        x2 = cx + r_outer * np.cos(end_angle)
        y2 = cy + r_outer * np.sin(end_angle)
        x3 = cx + r_inner * np.cos(end_angle)
        y3 = cy + r_inner * np.sin(end_angle)
        x4 = cx + r_inner * np.cos(start_angle)
        y4 = cy + r_inner * np.sin(start_angle)
        color = _EXIT_PALETTE[i % len(_EXIT_PALETTE)]
        d = (
            f"M {x1:.2f},{y1:.2f} "
            f"A {r_outer},{r_outer} 0 {large},1 {x2:.2f},{y2:.2f} "
            f"L {x3:.2f},{y3:.2f} "
            f"A {r_inner},{r_inner} 0 {large},0 {x4:.2f},{y4:.2f} Z"
        )
        paths += f'<path d="{d}" fill="{color}"/>'
        ly = 18 + i * 18
        legend += (
            f'<rect x="200" y="{ly}" width="10" height="10" rx="2" fill="{color}"/>'
            f'<text x="216" y="{ly + 9}" fill="{TEXT_COLOR}" font-size="11">'
            f'{_xml_escape(reason)} <tspan fill="#E8E8EE">{count} ({frac * 100:.1f}%)</tspan></text>'
        )
        start_angle = end_angle
    inner_text = (
        f'<text x="{cx}" y="{cy - 4}" fill="{TEXT_COLOR}" font-size="11" text-anchor="middle">'
        f"Total exits</text>"
        f'<text x="{cx}" y="{cy + 14}" fill="#E8E8EE" font-size="16" text-anchor="middle" '
        f'font-weight="600">{total}</text>'
    )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"{paths}{inner_text}{legend}</svg>"
    )


def _xml_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ---------------------------------------------------------------------------
# Exposure heatmap (24h × 7day) — Phase 4.4
# ---------------------------------------------------------------------------

_DAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def exposure_heatmap_svg(
    fraction: "np.ndarray",
    *,
    cell_w: int = 24,
    cell_h: int = 22,
) -> str:
    """Render a 7×24 in-position fraction heatmap.

    ``fraction`` is a ``(7, 24)`` array in [0, 1] where each cell is the
    fraction of available bars in that (weekday, hour) bucket the
    strategy spent in a position. Empty buckets render as flat
    background — same convention as PLAN.md §4.4.
    """
    if fraction is None or fraction.size == 0:
        return _empty_chart("No exposure data", w=24 * cell_w + 80, h=7 * cell_h + 40)
    arr = np.asarray(fraction, dtype=float)
    if arr.shape != (7, 24):
        # Defensive: bail rather than render a malformed grid.
        return _empty_chart("Bad exposure shape", w=24 * cell_w + 80, h=7 * cell_h + 40)
    pad_l, pad_t = 50, 28
    w = pad_l + 24 * cell_w + 16
    h = pad_t + 7 * cell_h + 18
    body = ""
    for j in range(24):
        x = pad_l + j * cell_w + cell_w / 2
        body += (
            f'<text x="{x:.1f}" y="{pad_t - 8}" fill="{TEXT_COLOR}" '
            f'font-size="9" text-anchor="middle">{j:02d}</text>'
        )
    for i in range(7):
        body += (
            f'<text x="{pad_l - 6}" y="{pad_t + i * cell_h + cell_h / 2 + 3:.1f}" '
            f'fill="{TEXT_COLOR}" font-size="10" text-anchor="end">{_DAY_LABELS[i]}</text>'
        )
        for j in range(24):
            v = float(arr[i, j])
            if v <= 0:
                fill = "#16161C"
            else:
                alpha = max(0.10, min(0.95, v))
                fill = _hex_with_alpha(NEUTRAL, alpha)
            x = pad_l + j * cell_w
            y = pad_t + i * cell_h
            tooltip = f"{_DAY_LABELS[i]} {j:02d}:00 — exposure {v * 100:.1f}%"
            body += (
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w - 1}" '
                f'height="{cell_h - 1}" rx="2" fill="{fill}">'
                f"<title>{_xml_escape(tooltip)}</title></rect>"
            )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"{body}"
        f'<text x="{pad_l}" y="{h - 4}" fill="{TEXT_COLOR}" font-size="10">'
        f"hour-of-day →</text></svg>"
    )


# ---------------------------------------------------------------------------
# Regime breakdown bar chart — Phase 4.2
# ---------------------------------------------------------------------------

_REGIME_COLORS = {
    "bull_lo": "#26A69A",
    "bull_hi": "#0F8B66",
    "bear_lo": "#EF5350",
    "bear_hi": "#B53B3B",
    "range_lo": "#5C8AFF",
    "range_hi": "#3A6BD8",
}


def regime_bar_chart_svg(
    rows: "list",
    *,
    w: int = 720,
    h: int = 200,
) -> str:
    """``rows`` is a list of ``RegimeBreakdownRow``-like objects (with
    ``regime``, ``sum_pnl``, ``n_trades`` attrs). Renders each bar
    proportional to ``sum_pnl`` (signed: positive up, negative down).
    """
    items = list(rows)
    if not items:
        return _empty_chart("No regime data", w, h)
    pad_l, pad_r, pad_t, pad_b = 60, 12, 16, 28
    chart_h = h - pad_t - pad_b
    bar_w = (w - pad_l - pad_r) / max(1, len(items))
    pnl_vals = [float(getattr(r, "sum_pnl", 0.0) or 0.0) for r in items]
    lo = min(0.0, min(pnl_vals))
    hi = max(0.0, max(pnl_vals))
    if hi == lo:
        hi = lo + 1.0
    zero_y = pad_t + chart_h * (hi / (hi - lo))
    body = (
        f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{w - pad_r}" y2="{zero_y:.1f}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )
    for j, (row, pnl) in enumerate(zip(items, pnl_vals)):
        x = pad_l + j * bar_w + bar_w * 0.15
        bw = bar_w * 0.70
        if pnl >= 0:
            y = pad_t + chart_h * ((hi - pnl) / (hi - lo))
            bh = zero_y - y
        else:
            y = zero_y
            bh = pad_t + chart_h * ((hi - pnl) / (hi - lo)) - zero_y
        color = _REGIME_COLORS.get(getattr(row, "regime", ""), NEUTRAL)
        body += (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(0, bh):.1f}" '
            f'fill="{color}" rx="3">'
            f"<title>{_xml_escape(getattr(row, 'regime', ''))}: "
            f"{int(getattr(row, 'n_trades', 0))} trades, ${pnl:+,.2f}</title>"
            f"</rect>"
            f'<text x="{x + bw / 2:.1f}" y="{h - 14}" fill="{TEXT_COLOR}" '
            f'font-size="9" text-anchor="middle">{_xml_escape(getattr(row, "regime", ""))}</text>'
            f'<text x="{x + bw / 2:.1f}" y="{h - 4}" fill="{TEXT_COLOR}" '
            f'font-size="9" text-anchor="middle">{int(getattr(row, "n_trades", 0))}</text>'
        )
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>'


# ---------------------------------------------------------------------------
# Cluster scatter — Phase 4.3
# ---------------------------------------------------------------------------

_CLUSTER_PALETTE = (
    "#5C8AFF",
    "#26A69A",
    "#FF9800",
    "#AB47BC",
    "#42A5F5",
    "#EF5350",
    "#FFCA28",
)


def cluster_scatter_svg(
    projection: "np.ndarray",
    labels: "np.ndarray",
    *,
    w: int = 480,
    h: int = 320,
) -> str:
    """Render a 2-D PCA scatter coloured by cluster id."""
    if projection is None or labels is None:
        return _empty_chart("No clusters", w, h)
    proj = np.asarray(projection, dtype=float)
    lab = np.asarray(labels, dtype=int)
    if proj.size == 0 or proj.shape[0] != lab.size:
        return _empty_chart("No clusters", w, h)
    pad_l, pad_r, pad_t, pad_b = 32, 12, 16, 20
    xs = proj[:, 0]
    ys = proj[:, 1]
    x_lo, x_hi = float(xs.min()), float(xs.max())
    y_lo, y_hi = float(ys.min()), float(ys.max())
    if x_hi == x_lo:
        x_hi = x_lo + 1.0
    if y_hi == y_lo:
        y_hi = y_lo + 1.0
    body = (
        f'<rect x="{pad_l}" y="{pad_t}" width="{w - pad_l - pad_r}" '
        f'height="{h - pad_t - pad_b}" fill="transparent" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )
    for i in range(proj.shape[0]):
        cx = pad_l + (xs[i] - x_lo) / (x_hi - x_lo) * (w - pad_l - pad_r)
        cy = pad_t + (1 - (ys[i] - y_lo) / (y_hi - y_lo)) * (h - pad_t - pad_b)
        color = _CLUSTER_PALETTE[int(lab[i]) % len(_CLUSTER_PALETTE)]
        body += (
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.5" fill="{color}" '
            f'opacity="0.75"/>'
        )
    # Legend
    unique = sorted(set(int(x) for x in lab.tolist()))
    for i, k in enumerate(unique):
        ly = pad_t + 8 + i * 16
        body += (
            f'<rect x="{w - pad_r - 88}" y="{ly}" width="10" height="10" '
            f'rx="2" fill="{_CLUSTER_PALETTE[k % len(_CLUSTER_PALETTE)]}"/>'
            f'<text x="{w - pad_r - 72}" y="{ly + 9}" fill="{TEXT_COLOR}" '
            f'font-size="11">Cluster {k}</text>'
        )
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>'
