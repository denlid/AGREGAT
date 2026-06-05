"""Єдиний реєстр налаштувань додатку.

Кожне налаштування має ключ + дефолтне значення.
Це єдине місце, де визначаються всі ключі settings.json.
"""

from typing import Any

SETTINGS_REGISTRY: dict[str, dict[str, Any]] = {
    # --- Appearance ---
    "theme":                     {"default": "dark"},
    "chart_scheme":              {"default": "Classic"},
    "font_size":                 {"default": 11},
    # --- Chart ---
    "timezone":                  {"default": 0},
    "render_window":             {"default": 300},
    "cache_size":                {"default": 1000},
    "batch_size":                {"default": 5000},
    "show_grid":                 {"default": True},
    "crosshair":                 {"default": True},
    "show_volume":               {"default": False},
    "autofit_y":                 {"default": True},
    # --- Trading ---
    "initial_equity":            {"default": 100_000},
    "risk_pct":                  {"default": 1.0},
    "max_lots_per_100k":         {"default": 12},
    # --- Execution ---
    "exec_profile":              {"default": "Optimistic"},
    "exec_spread_points":        {"default": None},
    "exec_commission_per_lot":   {"default": None},
    "exec_slippage_points":      {"default": None},
    # --- Performance ---
    "fast_mode":                 {"default": False},
    # --- Logging ---
    "log_level":                 {"default": "INFO"},
    "auto_save_logs":            {"default": True},
    "max_log_lines":             {"default": 5000},
    # --- AI ---
    "ai_provider":               {"default": "OpenAI"},
    "ai_model":                  {"default": "gpt-4o"},
    "ai_api_key":                {"default": ""},
    "ai_custom_base_url":        {"default": ""},
    "ai_custom_extra_body":      {"default": ""},
    "ai_max_tokens":             {"default": 16384},
    # --- Engine state (SettingsController) ---
    "asset":                     {"default": "DE40"},
    "timeframe":                 {"default": "1m"},
    "chart_mode":                {"default": "📈 Line"},
    "show_markers":              {"default": True},
    "chart_colors":              {"default": "Classic"},
    "current_idx":               {"default": -1},
    "view_range":                {"default": None},
    "last_csv":                  {"default": ""},
    "last_strat":                {"default": ""},
}

# Похідні константи — щоб не хардкодити ключі
DEFAULTS: dict[str, Any] = {
    k: v["default"] for k, v in SETTINGS_REGISTRY.items()
}

ALL_KEYS: set[str] = set(SETTINGS_REGISTRY.keys())