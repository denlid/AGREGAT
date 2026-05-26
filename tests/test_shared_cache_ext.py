"""Tests for SharedTradesCache extensions (stats_dict, equity curve)."""

from unittest.mock import MagicMock, PropertyMock
import numpy as np
import pytest


@pytest.fixture
def mock_trade():
    def _make(id_, pnl, entry_time="2024-01-01", exit_time="2024-01-02",
              direction="long", entry_price=100.0, rr=1.5, duration_minutes=240):
        t = MagicMock()
        t.id = id_
        t.pnl = pnl
        t.entry_time = entry_time
        t.exit_time = exit_time
        t.direction = direction
        t.entry_price = entry_price
        t.rr = rr
        t.duration_minutes = duration_minutes
        t.pnl_points = pnl
        t.mae = 0.0
        t.mfe = 0.0
        type(t).entry_time_np = PropertyMock(return_value=np.datetime64(entry_time))
        type(t).exit_time_np = PropertyMock(return_value=np.datetime64(exit_time))
        return t
    return _make


@pytest.fixture
def engine_with_trades(mock_trade):
    e = MagicMock()
    e.initial_equity = 10000.0
    e.trades = [
        mock_trade(1, 250.0, entry_time="2024-01-01"),
        mock_trade(2, -100.0, entry_time="2024-01-03"),
        mock_trade(3, 150.0, entry_time="2024-01-02"),
        mock_trade(4, -50.0, entry_time="2024-01-04"),
        mock_trade(5, 0.0, entry_time="2024-01-05"),
    ]
    return e


class TestSharedTradesCacheStatsDict:
    def test_get_stats_dict_basic(self, engine_with_trades):
        from gui.shared_data_cache import SharedTradesCache
        cache = SharedTradesCache(engine_with_trades)
        stats = cache.get_stats_dict()
        assert stats["total"] == 5
        assert stats["n_w"] == 2
        assert stats["n_l"] == 2
        assert stats["n_be"] == 1
        assert stats["wr"] == 40.0
        assert stats["total_pnl"] == 250.0
        assert stats["gross_profit"] == 400.0
        assert stats["gross_loss"] == 150.0
        assert stats["best"] == 250.0
        assert stats["worst"] == -100.0

    def test_get_stats_dict_empty(self):
        from gui.shared_data_cache import SharedTradesCache
        e = MagicMock()
        e.initial_equity = 10000.0
        e.trades = []
        cache = SharedTradesCache(e)
        stats = cache.get_stats_dict()
        assert stats["total"] == 0
        assert stats["return_pct"] == 0.0

    def test_get_stats_dict_zero_equity(self, mock_trade):
        from gui.shared_data_cache import SharedTradesCache
        e = MagicMock()
        e.initial_equity = 0.0
        e.trades = [mock_trade(1, 100.0)]
        cache = SharedTradesCache(e)
        stats = cache.get_stats_dict()
        assert stats["return_pct"] == 0.0
        assert stats["total_pnl"] == 100.0

    def test_get_stats_dict_caches(self, engine_with_trades):
        from gui.shared_data_cache import SharedTradesCache
        cache = SharedTradesCache(engine_with_trades)
        r1 = cache.get_stats_dict()
        r2 = cache.get_stats_dict()
        assert r1 is r2

    def test_get_equity_curve_arrays(self, engine_with_trades):
        from gui.shared_data_cache import SharedTradesCache
        cache = SharedTradesCache(engine_with_trades)
        eq_arr, peak, dd_abs, dd_pct = cache.get_equity_curve_arrays()
        assert len(eq_arr) == 6
        np.testing.assert_almost_equal(eq_arr[0], 10000.0)
        np.testing.assert_almost_equal(eq_arr[-1], 10250.0)

    def test_invalidate_clears_stats(self, engine_with_trades):
        from gui.shared_data_cache import SharedTradesCache
        cache = SharedTradesCache(engine_with_trades)
        _ = cache.get_stats_dict()
        cache.invalidate()
        assert cache._stats is None


class TestStatsCalculatorDelegation:
    def test_delegates_to_shared_cache(self, engine_with_trades):
        from gui.analytics.stats_calculator import StatsCalculator
        sc = StatsCalculator()
        cache = type("FakeCache", (), {
            "get_stats_dict": lambda self: {"total": 5, "delegated": True},
            "engine": engine_with_trades,
        })()
        result = sc.compute(engine_with_trades.trades, 10000.0, shared_cache=cache)
        assert result["delegated"] is True
        assert result["total"] == 5

    def test_fallback_when_trades_differ(self, engine_with_trades, mock_trade):
        from gui.analytics.stats_calculator import StatsCalculator
        sc = StatsCalculator()
        different_trades = [mock_trade(99, 999.0)]
        cache = type("FakeCache", (), {
            "get_stats_dict": lambda self: {"delegated": True},
            "engine": engine_with_trades,
        })()
        result = sc.compute(different_trades, 10000.0, shared_cache=cache)
        assert "delegated" not in result
        assert result["total"] == 1

    def test_zero_equity_no_mask(self, engine_with_trades):
        from gui.analytics.stats_calculator import StatsCalculator
        sc = StatsCalculator()
        result = sc.compute(engine_with_trades.trades, 0.0)
        assert result["return_pct"] == 0.0
