"""Institutional tear-sheet report rendering — Phase 3.

The legacy ``engine.report_export`` module remains the public entry point for
file emission (``export_report``). All HTML rendering, chart generation and
cost/regime analytics now live in this subpackage so individual sections can
be unit-tested in isolation and reused outside the GUI report flow.

Module map:
    metrics_extra  — derived metrics (monthly returns, rolling Sharpe,
                     cost attribution, top-N trades, exit reason rollup,
                     drawdown episodes formatted for the report).
    charts         — pure-Python SVG generators for every chart on the
                     tear-sheet (no matplotlib / pyqtgraph dependency).
    tooltips       — short metric explanations rendered as inline tooltips
                     and as full drill-down modals.
    themes         — CSS palettes (dark / light / print) with shared base.
    tearsheet      — top-level ``render_html_tearsheet`` that assembles a
                     single self-contained HTML document.
    exporters/pdf  — optional WeasyPrint-backed PDF exporter (no-op when
                     WeasyPrint is not installed).
"""

from engine.report.tearsheet import render_html_tearsheet

__all__ = ["render_html_tearsheet"]
