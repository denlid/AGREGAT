"""Phase 2.6 (C9) — gui.persistence.recent_files MRU list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gui.persistence import recent_files


@pytest.fixture
def isolated_recent(tmp_path, monkeypatch):
    """Redirect ``recent_files.config_path()`` into a tmp dir so tests
    never touch the user's real ``~/.agregat/recent_files.json``.
    """
    target = tmp_path / "recent_files.json"
    monkeypatch.setattr(recent_files, "config_path", lambda: str(target))
    return target


def _touch(path: Path) -> str:
    """Create an empty file at ``path`` and return its absolute path string.

    ``recent_files.load()`` filters out non-existent paths, so tests that
    care about ordering need real files on disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ohlc")
    return str(path)


def test_load_returns_empty_when_no_history(isolated_recent):
    assert recent_files.load() == []


def test_push_records_new_file(tmp_path, isolated_recent):
    p = _touch(tmp_path / "data.csv")
    out = recent_files.push(p)
    assert out == [str(Path(p).resolve())]
    assert recent_files.load() == out


def test_push_dedupes_existing_entry(tmp_path, isolated_recent):
    a = _touch(tmp_path / "a.csv")
    b = _touch(tmp_path / "b.csv")
    recent_files.push(a)
    recent_files.push(b)
    out = recent_files.push(a)  # re-pushing should bring it back to the front
    assert out[0] == str(Path(a).resolve())
    # No duplicates anywhere in the list.
    assert len(out) == len(set(out))


def test_push_respects_max_entries(tmp_path, isolated_recent, monkeypatch):
    monkeypatch.setattr(recent_files, "MAX_ENTRIES", 3)
    paths = [_touch(tmp_path / f"f{i}.csv") for i in range(5)]
    for p in paths:
        recent_files.push(p)
    out = recent_files.load()
    assert len(out) == 3
    # Most-recently-pushed wins; oldest entries are evicted.
    assert out[0] == str(Path(paths[-1]).resolve())


def test_load_drops_missing_files(tmp_path, isolated_recent):
    keep = _touch(tmp_path / "keep.csv")
    gone = str(tmp_path / "gone.csv")  # never created
    target = isolated_recent
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps([gone, keep]))
    out = recent_files.load()
    assert out == [keep]


def test_clear_empties_the_list(tmp_path, isolated_recent):
    p = _touch(tmp_path / "data.csv")
    recent_files.push(p)
    assert recent_files.load() != []
    recent_files.clear()
    assert recent_files.load() == []


def test_corrupt_json_is_treated_as_empty(tmp_path, isolated_recent):
    target = isolated_recent
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ not json")
    assert recent_files.load() == []


def test_non_list_payload_is_treated_as_empty(tmp_path, isolated_recent):
    target = isolated_recent
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"not": "a list"}))
    assert recent_files.load() == []
