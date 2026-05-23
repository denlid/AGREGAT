"""Phase 2.5 (C3) — drag-and-drop file filtering.

Only the static ``MainWindow._drop_first_supported_path`` helper is
unit-testable headlessly; the actual ``dropEvent`` handler needs a Qt
event loop and is covered by manual GUI testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest


@dataclass
class _FakeUrl:
    """Minimal stand-in for ``QUrl`` — only the two methods we exercise."""

    path: str
    local: bool = True

    def isLocalFile(self) -> bool:  # noqa: N802 — Qt camelCase API
        return self.local

    def toLocalFile(self) -> str:  # noqa: N802 — Qt camelCase API
        return self.path


@pytest.fixture
def filter_fn():
    pytest.importorskip("PySide6")
    from gui.main_window import MainWindow  # noqa: WPS433 — needs Qt import

    return MainWindow._drop_first_supported_path


def _urls(*paths: str, remote: List[str] | None = None) -> list:
    out: list = [_FakeUrl(p) for p in paths]
    out.extend(_FakeUrl(p, local=False) for p in (remote or []))
    return out


def test_picks_first_csv(filter_fn):
    assert filter_fn(_urls("/data/a.csv", "/data/b.csv")) == "/data/a.csv"


def test_accepts_parquet_and_feather(filter_fn):
    assert filter_fn(_urls("/data/a.parquet")) == "/data/a.parquet"
    assert filter_fn(_urls("/data/a.feather")) == "/data/a.feather"


def test_extension_match_is_case_insensitive(filter_fn):
    assert filter_fn(_urls("/DATA/A.CSV")) == "/DATA/A.CSV"


def test_skips_unsupported_extensions(filter_fn):
    assert filter_fn(_urls("/data/a.txt", "/data/b.json")) is None


def test_skips_remote_urls(filter_fn):
    """``http://`` / ``ftp://`` drops never trigger a download."""
    assert filter_fn(_urls(remote=["http://example.com/a.csv"])) is None


def test_first_supported_wins_over_unsupported(filter_fn):
    assert filter_fn(_urls("/data/notes.txt", "/data/data.csv")) == "/data/data.csv"


def test_returns_none_on_empty_drop(filter_fn):
    assert filter_fn([]) is None
