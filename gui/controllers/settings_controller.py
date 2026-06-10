"""SettingsTab preferences controller.

Owns the SettingsTab draft/applied state machine and preference
persistence. Last-opened CSV/strategy/chart position are handled by
SessionStateController.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from gui.controllers.settings_registry import (
    DEFAULTS,
    PREFERENCE_KEYS,
    SESSION_KEYS,
)
from gui.persistence import settings_persist

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.settings")


def strategy_settings_key(strategy) -> str:
    """Stable bucket key for persisted strategy-specific settings."""
    if strategy is None:
        return ""
    class_name = type(strategy).__name__
    if class_name != "_CompiledStrategy":
        return class_name
    declarative = getattr(strategy, "_declarative", None)
    declarative_name = getattr(declarative, "_name", None)
    if isinstance(declarative_name, str) and declarative_name.strip():
        return declarative_name.strip()
    name = getattr(strategy, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return class_name


class SettingsController:
    """Owns SettingsTab state, Apply/Reject flow, and preference JSON."""

    def __init__(self, host: MainWindow, settings_file: str) -> None:
        self._host = host
        self.settings_file = settings_file
        self.settings: dict[str, Any] = {}
        self.settings_tab = None
        self.applied_state: dict[str, Any] = {}
        self.draft_state: dict[str, Any] = {}
        self.strategy_params: dict[str, Any] = {}
        self.dirty = False

    def attach_settings_tab(self, tab) -> None:
        self.settings_tab = tab
        setattr(tab, "_settings_controller", self)
        tab.apply_btn.clicked.connect(self.apply_changes)
        tab.reject_btn.clicked.connect(self.reject_changes)
        tab.reset_btn.clicked.connect(self.reset_defaults)
        tab.ai_provider_cb.currentTextChanged.connect(tab._on_ai_provider_changed)
        tab._bind.wire_all(self.on_settings_changed, apply_runtime=False)
        self._set_dirty(False)

    def _tab(self):
        return self.settings_tab or getattr(self._host, "settings_tab", None)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = bool(dirty)
        tab = self._tab()
        if tab is not None and hasattr(tab, "_set_dirty"):
            tab._set_dirty(self.dirty)

    def on_settings_changed(self) -> None:
        tab = self._tab()
        if tab is None or getattr(tab, "_loading_settings", False):
            return
        self.draft_state = self.capture_ui_settings()
        self._set_dirty(True)

    def apply_persisted(self, settings: dict[str, Any]) -> None:
        tab = self._tab()
        if tab is None:
            return
        state = dict(settings or {})
        if "chart_colors" in state and "chart_scheme" not in state:
            state["chart_scheme"] = state["chart_colors"]
        self.strategy_params = dict(state.get("strategy_params", {}) or {})
        self.load_to_ui(state)
        if self.strategy_params:
            tab.load_strategy_params_to_ui(
                self.strategy_params_for(getattr(tab.engine, "strategy", None))
            )
        self.load_to_vars()
        self.applied_state = self.capture_ui_settings()
        self.draft_state = dict(self.applied_state)
        self.settings.clear()
        self.settings.update(self.applied_state)
        self._set_dirty(False)

    def apply_changes(self) -> None:
        tab = self._tab()
        if tab is None:
            return
        next_state = self.capture_ui_settings()
        changed = self._changed_keys(self.applied_state, next_state)
        self.apply_changed_to_vars(changed)
        self.draft_state = dict(next_state)
        self.applied_state = dict(self.draft_state)
        self._set_dirty(False)
        self.save()
        logger.info("Settings applied")

    def reject_changes(self) -> None:
        tab = self._tab()
        if tab is None:
            return
        state = self.applied_state or {
            key: value for key, value in DEFAULTS.items()
            if key in PREFERENCE_KEYS
        }
        self.load_to_ui(state)
        tab.load_strategy_params_to_ui(self._strategy_params_from_state(state))
        self.draft_state = dict(self.applied_state)
        self._set_dirty(False)
        logger.info("Settings changes rejected")

    def reset_defaults(self) -> None:
        tab = self._tab()
        if tab is None:
            return
        defaults = {
            key: value for key, value in DEFAULTS.items()
            if key in PREFERENCE_KEYS
        }
        self.load_to_ui(defaults)
        tab.reset_strategy_params_to_defaults()
        self.draft_state = self.capture_ui_settings()
        self._set_dirty(True)
        logger.info("Settings defaults loaded into draft")

    def capture_settings_snapshot(self) -> dict[str, Any]:
        if self.dirty and self.applied_state:
            return dict(self.applied_state)
        return self.capture_ui_settings() or dict(self.applied_state)

    def sync_applied_from_ui(self) -> None:
        self.applied_state = self.capture_ui_settings()
        self.draft_state = dict(self.applied_state)
        self._set_dirty(False)

    def strategy_params_for(self, strategy) -> dict[str, Any]:
        if strategy is None:
            return {}
        state = self.draft_state or self.applied_state or self.settings
        strategy_params = self.strategy_params
        if isinstance(state, dict) and state.get("strategy_params"):
            strategy_params = state.get("strategy_params", {})
        return dict((strategy_params.get(strategy_settings_key(strategy), {}) or {}))

    def _strategy_params_from_state(self, state: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab()
        if tab is None:
            return {}
        strategy = getattr(tab.engine, "strategy", None)
        if strategy is None:
            return {}
        params = (state or {}).get("strategy_params", {})
        if not isinstance(params, dict):
            return {}
        return dict((params.get(strategy_settings_key(strategy), {}) or {}))

    def _normalize_loaded_ai_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab()
        out = dict(settings)
        provider = (out.get("ai_provider") or "OpenAI").strip()
        legacy = getattr(tab, "_AI_LEGACY_PROVIDER", {}) if tab is not None else {}
        providers = getattr(tab, "AI_PROVIDERS", ()) if tab is not None else ()
        if provider in legacy:
            new_provider, new_model = legacy[provider]
            out["ai_provider"] = new_provider
            out.setdefault("ai_model", new_model)
        elif providers and provider not in providers:
            out["ai_provider"] = "OpenAI"
            out.setdefault("ai_model", "gpt-4o")
        return out

    def load_to_ui(self, settings: dict[str, Any]) -> None:
        tab = self._tab()
        if tab is None:
            return
        tab._loading_settings = True
        for widget in tab._bind.widgets_to_block():
            widget.blockSignals(True)
        try:
            state = self._normalize_loaded_ai_settings(settings)
            tab._bind.apply_all(state, DEFAULTS)
            tab._repopulate_ai_model_combo(
                select_model=state.get("ai_model") or "gpt-4o"
            )
            tab._update_ai_custom_visibility()
        finally:
            for widget in tab._bind.widgets_to_block():
                widget.blockSignals(False)
            tab._loading_settings = False

    def load_to_vars(self) -> None:
        tab = self._tab()
        if tab is None:
            return
        tab._bind.apply_runtime_all(exclude={"ai_provider"})
        tab._apply_strategy_runtime()

    def apply_changed_to_vars(self, changed_keys: set[str]) -> None:
        """Apply only runtime callbacks for changed settings."""
        tab = self._tab()
        if tab is None:
            return
        for key in changed_keys:
            if key == "ai_provider":
                continue
            if key == "strategy_params":
                continue
            if key in getattr(tab._bind, "_bindings", {}):
                tab._bind.apply_runtime(key)
        if "strategy_params" in changed_keys:
            tab._apply_strategy_runtime()

    @staticmethod
    def _changed_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
        keys = set(before) | set(after)
        return {key for key in keys if before.get(key) != after.get(key)}

    def capture_ui_settings(self) -> dict[str, Any]:
        tab = self._tab()
        if tab is None:
            return {}
        if not hasattr(tab, "_bind"):
            return dict(self.applied_state)
        snapshot: dict[str, Any] = {}
        snapshot.update(tab._bind.capture_all())
        strategy = getattr(tab.engine, "strategy", None)
        if strategy is not None and getattr(tab, "_strategy_widgets", None):
            self.strategy_params[strategy_settings_key(strategy)] = tab._strategy_form_values()
            snapshot["strategy_params"] = dict(self.strategy_params)
        elif self.strategy_params:
            snapshot["strategy_params"] = dict(self.strategy_params)
        return {
            key: value for key, value in snapshot.items()
            if key in PREFERENCE_KEYS
        }

    def load(self) -> None:
        try:
            self.apply_persisted(settings_persist.load_preferences())
        except Exception as exc:
            logger.warning("Settings preferences load failed: %s", exc, exc_info=True)

    def save(self, key: str | None = None, value: Any = None) -> None:
        """Persist preferences; delegate session keys as a back-compat shim."""
        if key:
            normalized_key = "chart_scheme" if key == "chart_colors" else key
            if normalized_key in SESSION_KEYS and normalized_key not in PREFERENCE_KEYS:
                session_ctrl = getattr(self._host, "_session_state_ctrl", None)
                if session_ctrl is not None:
                    session_ctrl.save(normalized_key, value)
                else:
                    settings_persist.save_session_state({normalized_key: value})
                return
            if normalized_key in PREFERENCE_KEYS:
                self.settings[normalized_key] = value

        capture = self.capture_settings_snapshot()
        preferences = {
            pref_key: pref_value
            for pref_key, pref_value in {**self.settings, **capture}.items()
            if pref_key in PREFERENCE_KEYS
        }
        if not preferences:
            preferences = {
                pref_key: pref_value for pref_key, pref_value in DEFAULTS.items()
                if pref_key in PREFERENCE_KEYS
            }

        self.settings.clear()
        self.settings.update(preferences)
        settings_persist.save_preferences(preferences)
