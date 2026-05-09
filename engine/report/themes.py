"""CSS palettes for the tear-sheet (Phase 3.3).

Three themes are shipped: ``dark`` (default — matches the in-app dark
theme), ``light`` (professional light, suitable for printed reports and
shareable HTML), and ``print`` (high-contrast monochrome friendly,
auto-applied via ``@media print`` on top of whichever active theme).

Themes are applied as raw CSS variables on the ``<body>`` tag so the
HTML / SVG markup never has to branch by theme.
"""

from __future__ import annotations

from typing import Dict


_BASE_CSS = r"""
* { margin: 0; padding: 0; box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
    background: var(--bg-page);
    color: var(--fg-default);
    font-family: -apple-system, 'Inter', 'SF Pro Display', 'Roboto',
                 'Segoe UI', system-ui, sans-serif;
    padding: 32px 40px;
    line-height: 1.5;
    font-size: 14px;
}
.report-wrapper { max-width: 1200px; margin: 0 auto; }
.header {
    border-bottom: 1px solid var(--border-default);
    padding-bottom: 18px;
    margin-bottom: 24px;
}
.header h1 {
    color: var(--fg-accent);
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.4px;
}
.header .sub {
    color: var(--fg-muted);
    font-size: 12px;
    margin-top: 6px;
}
.header .sub b { color: var(--fg-secondary); font-weight: 500; }
.cards {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 12px;
    margin-bottom: 24px;
}
.card {
    background: var(--bg-section);
    border: 1px solid var(--border-default);
    border-radius: 10px;
    padding: 14px 16px;
    transition: border-color 120ms ease, transform 120ms ease;
}
.card:hover { border-color: var(--border-hover); transform: translateY(-1px); }
.card .label {
    color: var(--fg-muted);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
.card .value {
    font-size: 20px;
    font-weight: 700;
    font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    margin-top: 4px;
}
h2 {
    color: var(--fg-accent);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    margin: 28px 0 10px 0;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
}
h2 .anchor {
    color: var(--fg-muted);
    font-size: 11px;
    opacity: 0;
    transition: opacity 80ms ease;
    text-decoration: none;
}
h2:hover .anchor { opacity: 1; }
.section {
    background: var(--bg-section);
    border: 1px solid var(--border-default);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 16px;
}
.section.tight { padding: 12px 14px; }
.grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    align-items: start;
}
.grid.three { grid-template-columns: 1.4fr 1fr; }
table { border-collapse: collapse; width: 100%; }
th, td {
    padding: 7px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
    font-size: 13px;
}
th { color: var(--fg-muted); font-weight: 500; width: 55%; }
td { font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace; }
th[data-tooltip] { cursor: help; border-bottom-style: dotted; }
tr.metric-row[data-detail="1"] th { cursor: pointer; }
tr.metric-row[data-detail="1"] th::after {
    content: ' ⓘ';
    color: var(--fg-accent);
    font-size: 10px;
    opacity: 0.6;
}
tr.metric-row:hover th { color: var(--fg-default); }
.green { color: var(--success); }
.red   { color: var(--danger);  }
.amber { color: var(--warning); }
.muted { color: var(--fg-muted); }
.footer {
    color: var(--fg-muted);
    font-size: 11px;
    margin-top: 32px;
    text-align: center;
    border-top: 1px solid var(--border-default);
    padding-top: 16px;
}
.footer a { color: var(--fg-accent); text-decoration: none; }
.cost-bar {
    display: flex;
    height: 22px;
    border-radius: 4px;
    overflow: hidden;
    margin-top: 6px;
    background: var(--bg-page);
    border: 1px solid var(--border-default);
}
.cost-bar > div {
    color: #fff;
    font-size: 11px;
    text-align: center;
    line-height: 22px;
    overflow: hidden;
    white-space: nowrap;
}
.cost-bar .cb-spread { background: #5C8AFF; }
.cost-bar .cb-slip   { background: #FF9800; }
.cost-bar .cb-comm   { background: #AB47BC; }
.cost-bar .cb-net    { background: #26A69A; }
.cost-bar .cb-loss   { background: #EF5350; }
.cost-bar .cb-empty  { background: transparent; flex-shrink: 1; }
.legend { display:flex; gap:12px; margin-top:8px; flex-wrap: wrap; }
.legend-chip {
    display:inline-flex; align-items:center; gap:6px;
    font-size:11px; color: var(--fg-muted);
}
.legend-swatch { width:10px; height:10px; border-radius:2px; }

/* drill-down modal (no JS dependency on libraries) */
.modal-bg {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.6);
    display: none;
    align-items: center; justify-content: center;
    z-index: 1000;
}
.modal-bg.open { display: flex; }
.modal {
    background: var(--bg-section);
    border: 1px solid var(--border-default);
    border-radius: 12px;
    padding: 24px 28px;
    max-width: 520px;
    width: 90%;
    color: var(--fg-default);
    box-shadow: 0 12px 32px rgba(0,0,0,0.45);
}
.modal h3 {
    color: var(--fg-accent);
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 14px;
}
.modal p { margin-bottom: 8px; line-height: 1.55; }
.modal .close {
    float: right; cursor: pointer; color: var(--fg-muted);
    font-size: 14px; user-select: none;
}
.modal .close:hover { color: var(--fg-default); }

@media print {
    body {
        background: #ffffff !important;
        color: #111 !important;
        padding: 16px 24px;
    }
    .header h1 { color: #111 !important; }
    .card, .section, .modal { background: #fff !important; border-color: #ccc !important; }
    .green { color: #128a6f !important; }
    .red   { color: #b53b3b !important; }
    .amber { color: #b87316 !important; }
    h2 .anchor { display: none; }
    .modal-bg { display: none !important; }
}
"""


_THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        "--bg-page": "#0B0B0D",
        "--bg-section": "#131316",
        "--fg-default": "#E8E8EE",
        "--fg-secondary": "#8A8A96",
        "--fg-muted": "#5A5A6E",
        "--fg-accent": "#5C8AFF",
        "--border-default": "#1E1E28",
        "--border-subtle": "#1A1A22",
        "--border-hover": "#2D2D38",
        "--success": "#26A69A",
        "--danger": "#EF5350",
        "--warning": "#FF9800",
    },
    "light": {
        "--bg-page": "#F5F6F9",
        "--bg-section": "#FFFFFF",
        "--fg-default": "#1A1B22",
        "--fg-secondary": "#3F4150",
        "--fg-muted": "#6E7180",
        "--fg-accent": "#2547D0",
        "--border-default": "#DEE0E7",
        "--border-subtle": "#E7E9EE",
        "--border-hover": "#B8BBC6",
        "--success": "#0F8B66",
        "--danger": "#C63838",
        "--warning": "#B36100",
    },
    "print": {
        # Mostly an alias for light — @media print rules will override the rest.
        "--bg-page": "#FFFFFF",
        "--bg-section": "#FFFFFF",
        "--fg-default": "#111111",
        "--fg-secondary": "#222222",
        "--fg-muted": "#555555",
        "--fg-accent": "#222222",
        "--border-default": "#CCCCCC",
        "--border-subtle": "#DDDDDD",
        "--border-hover": "#888888",
        "--success": "#128A6F",
        "--danger": "#B53B3B",
        "--warning": "#B87316",
    },
}


def render_theme_css(theme: str = "dark") -> str:
    """Return a complete <style>...</style> block for the requested theme.

    Falls back to ``dark`` if the theme is unknown — matches the in-app
    behaviour and means future palette additions don't break old saves.
    """
    palette = _THEMES.get(theme, _THEMES["dark"])
    vars_block = "\n  ".join(f"{k}: {v};" for k, v in palette.items())
    return f"<style>\n:root {{\n  {vars_block}\n}}\n{_BASE_CSS}\n</style>"


def known_themes() -> tuple[str, ...]:
    return tuple(_THEMES.keys())
