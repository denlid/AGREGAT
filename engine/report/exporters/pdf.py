"""Optional PDF exporter (Phase 3.2).

WeasyPrint converts the tear-sheet HTML directly to a print-quality PDF
because the document is already a single self-contained file with inline
SVG (no external dependencies, no JS required for rendering).

WeasyPrint pulls in heavy native libraries (Pango / Cairo / GTK) that
aren't part of the default install profile, so this module degrades
gracefully:

  * ``available()`` returns False when the import fails — callers can
    surface a tooltip / disable the menu item.
  * ``html_to_pdf(...)`` raises ``RuntimeError`` with an actionable
    install hint instead of an obscure ImportError.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("StrategyTester.engine.report.exporters.pdf")


_INSTALL_HINT = (
    "WeasyPrint is not installed. Install it with `pip install weasyprint` "
    "and ensure the system libraries (Pango, Cairo, GLib) are present. "
    "On Ubuntu: `apt install libpango-1.0-0 libpangoft2-1.0-0`."
)


def available() -> bool:
    """Cheap import probe — does NOT actually instantiate WeasyPrint."""
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


def html_to_pdf(html: str, out_path: str, *, base_url: Optional[str] = None) -> str:
    """Render ``html`` (full document) to a PDF file at ``out_path``.

    Returns the absolute path to the written file. Raises ``RuntimeError``
    if WeasyPrint isn't installed — callers should check ``available()``
    first to give the user a graceful degradation path.
    """
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:  # pragma: no cover - environmental
        raise RuntimeError(_INSTALL_HINT) from exc
    out_path = os.path.abspath(out_path)
    HTML(string=html, base_url=base_url).write_pdf(out_path)
    logger.info("PDF written: %s", out_path)
    return out_path
