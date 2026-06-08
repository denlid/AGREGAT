"""Settings keys and defaults.

Preferences are values controlled by SettingsTab. Session state is the
last-opened workspace state restored on startup. Back-compat exports keep
older flat settings call sites working while controllers move to separated
stores.
"""

from typing import Any

PREFERENCES_REGISTRY: dict[str, dict[str, Any]] = {
    # Appearance
    "theme": {"default": "dark"},
    "chart_scheme": {"default": "Classic"},
    "font_size": {"default": 11},
    # Chart
    "timezone": {"default": 0},
    "render_window": {"default": 300},
    "cache_size": {"default": 1000},
    "batch_size": {"default": 5000},
    "show_grid": {"default": True},
    "crosshair": {"default": True},
    "show_volume": {"default": False},
    "autofit_y": {"default": True},
    # Trading
    "initial_equity": {"default": 100_000},
    "risk_pct": {"default": 1.0},
    "max_lots_per_100k": {"default": 12},
    # Execution
    "exec_profile": {"default": "Optimistic"},
    "exec_spread_points": {"default": None},
    "exec_commission_per_lot": {"default": None},
    "exec_slippage_points": {"default": None},
    # Performance
    "fast_mode": {"default": False},
    # Logging
    "log_level": {"default": "INFO"},
    "auto_save_logs": {"default": True},
    "max_log_lines": {"default": 5000},
    # Dashboard audience mode is controlled from the toolbar, not SettingsTab.
    "dashboard_mode": {"default": "retail"},
    # AI
    "ai_provider": {"default": "OpenAI"},
    "ai_model": {"default": "gpt-4o"},
    "ai_api_key": {"default": ""},
    "ai_custom_base_url": {"default": ""},
    "ai_custom_extra_body": {"default": ""},
    "ai_max_tokens": {"default": 16384},
    # Strategy form values are user preferences, not chart replay state.
    "strategy_params": {"default": {}},
}

SESSION_STATE_REGISTRY: dict[str, dict[str, Any]] = {
    "asset": {"default": "DE40"},
    "timeframe": {"default": "1m"},
    "chart_mode": {"default": "Line"},
    "show_markers": {"default": True},
    "chart_scheme": {"default": "Classic"},
    "current_idx": {"default": -1},
    "view_range": {"default": None},
    "last_csv": {"default": ""},
    "last_strat": {"default": ""},
}

PREFERENCES_DEFAULTS: dict[str, Any] = {
    k: v["default"] for k, v in PREFERENCES_REGISTRY.items()
}
SESSION_STATE_DEFAULTS: dict[str, Any] = {
    k: v["default"] for k, v in SESSION_STATE_REGISTRY.items()
}

PREFERENCE_KEYS: set[str] = set(PREFERENCES_REGISTRY.keys())
SESSION_KEYS: set[str] = set(SESSION_STATE_REGISTRY.keys())

# Back-compat flat registry/defaults.
SETTINGS_REGISTRY: dict[str, dict[str, Any]] = {
    **PREFERENCES_REGISTRY,
    **SESSION_STATE_REGISTRY,
}
DEFAULTS: dict[str, Any] = {
    **PREFERENCES_DEFAULTS,
    **SESSION_STATE_DEFAULTS,
}

# SettingsBinding.validate() should validate only SettingsTab-bound prefs.
ALL_KEYS: set[str] = set(PREFERENCE_KEYS)
