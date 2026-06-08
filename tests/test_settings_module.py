import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Import pandas before PySide6; otherwise shiboken's import hook can break
# dateutil/six imports on Windows.
import pandas  # noqa: F401

from gui.controllers.settings_controller import SettingsController
from gui.persistence import settings_persist

pytest.importorskip("PySide6")

from gui.controllers.settings_binding import SettingsBinding  # noqa: E402


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(settings_persist, "config_path", lambda: str(target))
    return target


def test_settings_persist_partial_save_preserves_existing_state(isolated_settings):
    settings_persist.save(
        {
            "last_csv": "data.csv",
            "current_idx": 42,
            "view_range": [[1, 2], [3, 4]],
            "strategy_params": {"Linear": {"risk_pct": 2.5}},
        }
    )

    settings_persist.save({"theme": "light"})

    loaded = settings_persist.load()
    assert loaded["theme"] == "light"
    assert loaded["last_csv"] == "data.csv"
    assert loaded["current_idx"] == 42
    assert loaded["view_range"] == [[1, 2], [3, 4]]
    assert loaded["strategy_params"] == {"Linear": {"risk_pct": 2.5}}

    raw = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert raw["preferences"]["theme"] == "light"
    assert raw["session"]["last_csv"] == "data.csv"


def test_settings_persist_migrates_legacy_flat_chart_colors(isolated_settings):
    isolated_settings.write_text(
        json.dumps({
            "theme": "light",
            "chart_colors": "Neon",
            "last_csv": "data.csv",
            "current_idx": 9,
        }),
        encoding="utf-8",
    )

    assert settings_persist.load_preferences()["chart_scheme"] == "Neon"
    assert settings_persist.load_session_state()["chart_scheme"] == "Neon"
    loaded = settings_persist.load()
    assert loaded["chart_scheme"] == "Neon"
    assert loaded["chart_colors"] == "Neon"
    assert loaded["last_csv"] == "data.csv"
    assert loaded["current_idx"] == 9


def test_settings_persist_preferences_and_session_save_are_independent(isolated_settings):
    settings_persist.save_session_state({
        "last_csv": "data.csv",
        "current_idx": 42,
    })
    settings_persist.save_preferences({
        "theme": "light",
        "risk_pct": 2.0,
        "dashboard_mode": "quant",
    })

    raw = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert raw["preferences"]["theme"] == "light"
    assert raw["preferences"]["risk_pct"] == 2.0
    assert raw["preferences"]["dashboard_mode"] == "quant"
    assert raw["session"]["last_csv"] == "data.csv"
    assert raw["session"]["current_idx"] == 42


def test_settings_binding_accepts_none_for_numeric_widgets(qapp):
    from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

    spin = QSpinBox()
    spin.setRange(5, 10)
    dspin = QDoubleSpinBox()
    dspin.setRange(0.0, 100.0)

    SettingsBinding.set_widget_value(spin, None)
    SettingsBinding.set_widget_value(dspin, None)

    assert spin.value() == 5
    assert dspin.value() == 0.0


def test_settings_binding_can_apply_runtime_after_bulk_apply(qapp):
    from PySide6.QtWidgets import QSpinBox

    seen = []
    spin = QSpinBox()
    spin.setRange(0, 10)
    binding = SettingsBinding()
    binding.bind("answer", spin, apply=seen.append)

    binding.apply_all({"answer": 7}, {"answer": 1}, apply_runtime=True)
    binding.reset_all({"answer": 3}, apply_runtime=True)

    assert seen == [7, 3]


def test_settings_controller_save_uses_full_settings_tab_snapshot(isolated_settings):
    class Combo:
        def __init__(self, text):
            self._text = text

        def currentText(self):
            return self._text

    class Check:
        def __init__(self, checked):
            self._checked = checked

        def isChecked(self):
            return self._checked

    class ViewBox:
        def viewRange(self):
            return [[10.0, 20.0], [100.0, 200.0]]

    class Plot:
        def getViewBox(self):
            return ViewBox()

    settings_tab = SimpleNamespace(
        equity_spin=SimpleNamespace(value=lambda: 123000.0),
        ai_provider_cb=Combo("OpenAI"),
        ai_model_cb=Combo("gpt-4o"),
        ai_api_key_le=SimpleNamespace(text=lambda: "sk-test"),
        ai_custom_base_url_le=SimpleNamespace(text=lambda: ""),
        ai_custom_extra_body_le=SimpleNamespace(text=lambda: ""),
    )
    chart_tab = SimpleNamespace(
        visual_tf_cb=Combo("5m"),
        chart_mode_cb=Combo("Candles"),
        show_markers_cb=Check(False),
        plot_widget=Plot(),
        _color_scheme="Classic",
    )
    host = SimpleNamespace(
        settings_tab=settings_tab,
        chart_tab=chart_tab,
        asset_cb=Combo("DE40"),
        engine=SimpleNamespace(current_idx=77),
    )
    ctrl = SettingsController(host, str(isolated_settings))
    ctrl.applied_state = {
        "risk_pct": 2.5,
        "fast_mode": True,
        "ai_max_tokens": 32768,
        "strategy_params": {"Linear": {"rr": 3.0}},
    }
    ctrl.settings.update({"last_csv": "data.csv", "last_strat": "strategy.py"})

    ctrl.save()

    saved = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert saved["preferences"]["risk_pct"] == 2.5
    assert saved["preferences"]["fast_mode"] is True
    assert saved["preferences"]["ai_max_tokens"] == 32768
    assert saved["preferences"]["strategy_params"] == {"Linear": {"rr": 3.0}}
    assert "last_csv" not in saved["preferences"]


def test_settings_controller_save_preserves_session_state_before_data_loaded(isolated_settings):
    class Combo:
        def __init__(self, text):
            self._text = text

        def currentText(self):
            return self._text

    class Check:
        def __init__(self, checked):
            self._checked = checked

        def isChecked(self):
            return self._checked

    class ViewBox:
        def viewRange(self):
            return [[0, 1], [0, 1]]

    class Plot:
        def getViewBox(self):
            return ViewBox()

    settings_persist.save(
        {
            "last_csv": "data.csv",
            "last_strat": "strategy.py",
            "current_idx": 42,
            "view_range": [[10.0, 20.0], [100.0, 200.0]],
        }
    )

    host = SimpleNamespace(
        settings_tab=SimpleNamespace(),
        chart_tab=SimpleNamespace(
            visual_tf_cb=Combo("1m"),
            chart_mode_cb=Combo("Line"),
            show_markers_cb=Check(True),
            plot_widget=Plot(),
            _color_scheme="Classic",
        ),
        asset_cb=Combo("DE40"),
        engine=SimpleNamespace(current_idx=-1, df=None),
    )
    ctrl = SettingsController(host, str(isolated_settings))
    ctrl.applied_state = {"theme": "dark"}

    ctrl.save("theme", "light")

    saved = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert saved["session"]["last_csv"] == "data.csv"
    assert saved["session"]["last_strat"] == "strategy.py"
    assert saved["session"]["current_idx"] == 42
    assert saved["session"]["view_range"] == [[10.0, 20.0], [100.0, 200.0]]
    assert saved["preferences"]["theme"] == "dark"


def _controller_host(tab, engine):
    class Combo:
        def __init__(self, text):
            self._text = text

        def currentText(self):
            return self._text

    class Check:
        def __init__(self, checked):
            self._checked = checked

        def isChecked(self):
            return self._checked

    class ViewBox:
        def viewRange(self):
            return [[10.0, 20.0], [100.0, 200.0]]

    class Plot:
        def getViewBox(self):
            return ViewBox()

        def showGrid(self, **_kwargs):
            pass

    class Chart:
        def __init__(self):
            self.visual_tf_cb = Combo("1m")
            self.chart_mode_cb = Combo("Line")
            self.show_markers_cb = Check(True)
            self.plot_widget = Plot()
            self._color_scheme = "Classic"
            self.time_axis = SimpleNamespace(tz_offset=0)

        def set_color_scheme(self, name):
            self._color_scheme = name

        def redraw_chart(self):
            pass

    class AutoSave:
        def setChecked(self, _checked):
            pass

    class LogConsole:
        def __init__(self):
            self.auto_save_cb = AutoSave()

        def set_level_threshold(self, _level):
            pass

        def set_max_lines(self, _lines):
            pass

    return SimpleNamespace(
        settings_tab=tab,
        chart_tab=Chart(),
        chart_tab_2=None,
        log_console=LogConsole(),
        asset_cb=Combo("DE40"),
        engine=engine,
        bar_replay=SimpleNamespace(_run_to_end_batch=0),
        _switch_theme=lambda _name: None,
    )


def test_settings_controller_apply_persisted_updates_runtime_without_saving(
    qapp, monkeypatch, isolated_settings
):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    monkeypatch.setattr(
        settings_persist,
        "save_preferences",
        lambda _settings: pytest.fail("unexpected save"),
    )

    engine = BacktestEngine()
    tab = SettingsTab(engine, parent_window=None)
    host = _controller_host(tab, engine)
    tab.pw = host
    ctrl = SettingsController(host, str(isolated_settings))
    host._settings_ctrl = ctrl
    ctrl.attach_settings_tab(tab)

    ctrl.apply_persisted(
        {
            "risk_pct": 2.75,
            "max_lots_per_100k": 33,
            "fast_mode": True,
            "exec_profile": "Custom",
            "exec_spread_points": 1.25,
            "exec_commission_per_lot": 4.5,
            "exec_slippage_points": 0.75,
        }
    )

    assert engine.risk_pct == pytest.approx(2.75)
    assert engine.asset_config.max_lots_per_100k == 33
    assert engine.fast_mode is True
    assert engine.execution_model.profile == "Custom"
    assert engine.execution_model.spread_points == pytest.approx(1.25)
    assert engine.execution_model.commission_per_lot == pytest.approx(4.5)
    assert engine.execution_model.slippage_points == pytest.approx(0.75)


def test_settings_tab_show_grid_updates_chart_runtime_state(qapp):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    class Plot:
        def __init__(self):
            self.calls = []

        def showGrid(self, **kwargs):
            self.calls.append(kwargs)

    chart = SimpleNamespace(plot_widget=Plot())
    tab = SettingsTab(
        BacktestEngine(),
        parent_window=SimpleNamespace(chart_tab=chart, chart_tab_2=None),
    )

    tab._on_show_grid(False)

    assert chart.show_grid is False
    assert chart.plot_widget.calls[-1] == {"x": False, "y": False, "alpha": 0.0}


def test_settings_controller_changes_are_draft_until_apply(qapp, monkeypatch, isolated_settings):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    monkeypatch.setattr(
        settings_persist,
        "save_preferences",
        lambda _settings: pytest.fail("unexpected save"),
    )

    engine = BacktestEngine()
    tab = SettingsTab(engine, parent_window=None)
    host = _controller_host(tab, engine)
    tab.pw = host
    ctrl = SettingsController(host, str(isolated_settings))
    host._settings_ctrl = ctrl
    ctrl.attach_settings_tab(tab)
    ctrl.apply_persisted({"risk_pct": 1.0})

    tab.risk_pct_spin.setValue(2.5)

    assert ctrl.dirty is True
    assert tab.apply_btn.isEnabled() is True
    assert engine.risk_pct == pytest.approx(1.0)


def test_settings_controller_apply_commits_draft_to_runtime_and_disk(
    qapp, monkeypatch, isolated_settings
):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    saved = []
    monkeypatch.setattr(settings_persist, "save_preferences", saved.append)

    engine = BacktestEngine()
    tab = SettingsTab(engine, parent_window=None)
    host = _controller_host(tab, engine)
    tab.pw = host
    ctrl = SettingsController(host, str(isolated_settings))
    host._settings_ctrl = ctrl
    ctrl.attach_settings_tab(tab)
    ctrl.apply_persisted({"risk_pct": 1.0})
    tab.risk_pct_spin.setValue(2.5)

    ctrl.apply_changes()

    assert engine.risk_pct == pytest.approx(2.5)
    assert saved[-1]["risk_pct"] == pytest.approx(2.5)
    assert ctrl.dirty is False
    assert tab.apply_btn.isEnabled() is False


def test_settings_controller_apply_only_runs_changed_runtime_callbacks(
    qapp, monkeypatch, isolated_settings
):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    monkeypatch.setattr(settings_persist, "save_preferences", lambda _settings: None)

    engine = BacktestEngine()
    tab = SettingsTab(engine, parent_window=None)
    host = _controller_host(tab, engine)
    tab.pw = host
    ctrl = SettingsController(host, str(isolated_settings))
    host._settings_ctrl = ctrl
    ctrl.attach_settings_tab(tab)
    ctrl.apply_persisted({"risk_pct": 1.0, "render_window": 300})

    calls = []
    tab._bind._bindings["risk_pct"].apply = lambda value: calls.append(("risk", value))
    tab._bind._bindings["render_window"].apply = lambda value: calls.append(("render", value))

    tab.risk_pct_spin.setValue(2.5)
    ctrl.apply_changes()

    assert calls == [("risk", pytest.approx(2.5))]


def test_settings_controller_reject_restores_applied_snapshot_without_saving(
    qapp, monkeypatch, isolated_settings
):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    monkeypatch.setattr(
        settings_persist,
        "save_preferences",
        lambda _settings: pytest.fail("unexpected save"),
    )

    engine = BacktestEngine()
    tab = SettingsTab(engine, parent_window=None)
    host = _controller_host(tab, engine)
    tab.pw = host
    ctrl = SettingsController(host, str(isolated_settings))
    host._settings_ctrl = ctrl
    ctrl.attach_settings_tab(tab)
    ctrl.apply_persisted({"risk_pct": 1.5})
    tab.risk_pct_spin.setValue(3.0)

    ctrl.reject_changes()

    assert tab.risk_pct_spin.value() == pytest.approx(1.5)
    assert engine.risk_pct == pytest.approx(1.5)
    assert ctrl.dirty is False


def test_settings_controller_dirty_capture_returns_applied_snapshot(qapp, isolated_settings):
    from engine.backtest_engine import BacktestEngine
    from gui.tabs.settings_tab import SettingsTab

    engine = BacktestEngine()
    tab = SettingsTab(engine, parent_window=None)
    host = _controller_host(tab, engine)
    tab.pw = host
    ctrl = SettingsController(host, str(isolated_settings))
    host._settings_ctrl = ctrl
    ctrl.attach_settings_tab(tab)
    ctrl.apply_persisted({"risk_pct": 1.25})
    tab.risk_pct_spin.setValue(4.0)

    captured = ctrl.capture_settings_snapshot()

    assert captured["risk_pct"] == pytest.approx(1.25)
