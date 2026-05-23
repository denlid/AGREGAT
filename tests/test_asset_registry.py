"""Tests for engine.asset_registry — list / get / save / atomic write."""

from __future__ import annotations

import json

import pytest

from engine.asset_registry import (
    default_config_path,
    detect_asset_from_filename,
    get_asset,
    list_asset_names,
    load_assets,
    save_asset,
)


def _write_assets(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_returns_empty_when_missing(tmp_path):
    missing = tmp_path / "no-such-file.json"
    assert load_assets(str(missing)) == {}


def test_load_returns_empty_for_invalid_json(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ this is not json", encoding="utf-8")
    assert load_assets(str(p)) == {}


def test_load_returns_empty_for_non_object_root(tmp_path):
    p = tmp_path / "list-root.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_assets(str(p)) == {}


def test_list_assets_is_sorted_case_insensitively(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"de40": {}, "ZZZ": {}, "abc": {}, "AAA": {}})
    names = list_asset_names(str(p))
    # Sorted alphabetically (case-insensitive) → AAA, abc, de40, ZZZ
    assert names == sorted(names, key=str.lower)
    assert set(names) == {"de40", "ZZZ", "abc", "AAA"}


def test_get_asset_returns_dict_or_none(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {"point_value": 1.0, "tick_size": 0.1}})
    cfg = get_asset("DE40", str(p))
    assert cfg is not None
    assert cfg["point_value"] == 1.0
    assert get_asset("MISSING", str(p)) is None


def test_save_asset_creates_file_when_absent(tmp_path):
    p = tmp_path / "assets.json"
    save_asset(
        "TEST",
        {
            "point_value": 1.0,
            "tick_size": 0.5,
            "buffer": 1.0,
            "max_lots_per_100k": 10,
            "currency": "USD",
        },
        path=str(p),
    )
    data = load_assets(str(p))
    assert "TEST" in data
    assert data["TEST"]["currency"] == "USD"


def test_save_asset_overwrites_existing(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {"currency": "EUR", "point_value": 1.0}})
    save_asset("DE40", {"currency": "USD", "point_value": 2.0}, path=str(p))
    data = load_assets(str(p))
    assert data["DE40"]["currency"] == "USD"
    assert data["DE40"]["point_value"] == 2.0


def test_save_asset_preserves_other_entries(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {"currency": "EUR"}, "US500": {"currency": "USD"}})
    save_asset("NEW", {"currency": "USD", "point_value": 1.0}, path=str(p))
    data = load_assets(str(p))
    assert {"DE40", "US500", "NEW"}.issubset(data.keys())
    assert data["DE40"]["currency"] == "EUR"


def test_default_config_path_points_to_repo_assets():
    p = default_config_path()
    assert p.endswith("assets.json")


def test_bundled_assets_contains_top10_indexes():
    """Sanity check that the shipped config still defines the expected top-10."""
    data = load_assets()
    expected = {
        "DE40",
        "NAS100",
        "US500",
        "UK100",
        "US30",
        "JP225",
        "HK50",
        "AUS200",
        "FR40",
        "EU50",
    }
    assert expected.issubset(set(data.keys())), (
        f"Bundled assets are missing: {expected - set(data.keys())}"
    )
    # Each index must declare the fields BacktestEngine.load_asset_config relies on.
    required = {"point_value", "tick_size", "buffer", "max_lots_per_100k", "currency"}
    for name in expected:
        assert required.issubset(set(data[name].keys())), (
            f"Asset {name} missing required fields: {required - set(data[name].keys())}"
        )


def test_save_atomic_write_uses_replace(tmp_path, monkeypatch):
    """Verify atomic write — original file must not be left half-truncated on
    a write failure (we write to .tmp then replace)."""
    import os

    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {"currency": "EUR"}})
    original = p.read_text(encoding="utf-8")

    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    save_asset("NEW", {"currency": "USD", "point_value": 1.0}, path=str(p))
    assert calls, "save_asset must atomically os.replace(.tmp -> target)"
    src, dst = calls[0]
    assert src.endswith(".tmp")
    assert dst == str(p)
    # Original entry preserved.
    data = load_assets(str(p))
    assert "DE40" in data
    assert "NEW" in data
    # Initial content was a strict subset of new content (no truncation glitch).
    assert "DE40" in original


# --------------------------------------------------------- Phase 2.4 (C4)
def test_detect_asset_returns_exact_match(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {}, "EURUSD": {}, "XAUUSD": {}})
    assert detect_asset_from_filename("EURUSD.csv", path=str(p)) == "EURUSD"
    assert detect_asset_from_filename("/data/DE40.parquet", path=str(p)) == "DE40"


def test_detect_asset_handles_mt5_naming(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"EURUSD": {}, "GBPUSD": {}})
    assert detect_asset_from_filename("EURUSD_M1_20240101.csv", path=str(p)) == "EURUSD"
    assert detect_asset_from_filename("GBPUSD-2023.csv", path=str(p)) == "GBPUSD"


def test_detect_asset_picks_longest_prefix(tmp_path):
    """``XAUUSD_M1`` must beat ``XAU`` — longest prefix wins."""
    p = tmp_path / "assets.json"
    _write_assets(p, {"XAU": {}, "XAUUSD": {}})
    assert detect_asset_from_filename("XAUUSD_M1.csv", path=str(p)) == "XAUUSD"


def test_detect_asset_returns_none_when_no_match(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {}})
    assert detect_asset_from_filename("totally_unknown.csv", path=str(p)) is None


def test_detect_asset_returns_none_for_empty_inputs(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {}})
    assert detect_asset_from_filename("", path=str(p)) is None
    # Empty registry -> None even on a perfect filename.
    empty = tmp_path / "empty.json"
    _write_assets(empty, {})
    assert detect_asset_from_filename("DE40.csv", path=str(empty)) is None


def test_detect_asset_is_case_insensitive(tmp_path):
    p = tmp_path / "assets.json"
    _write_assets(p, {"DE40": {}})
    assert detect_asset_from_filename("de40_M1.csv", path=str(p)) == "DE40"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
