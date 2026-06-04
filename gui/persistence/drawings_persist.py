import json
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("StrategyTester.drawings_persist")


def config_path(symbol: str = "default") -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if symbol and symbol != "default":
        safe_symbol = "".join(c for c in symbol if c.isalnum() or c in ("-", "_")).strip()
        if safe_symbol:
            return os.path.join(root, "saves", f"drawings_{safe_symbol}.json")
    return os.path.join(root, "saves", "drawings.json")


def load(symbol: str = "default") -> List[Dict[str, Any]]:
    """Return the persisted drawings, falling back to an empty list on missing/broken file."""
    path = config_path(symbol)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read {path}: {e!r}; returning empty drawings list.")
        return []


def save(drawings: List[Dict[str, Any]], symbol: str = "default") -> None:
    """Atomically write `drawings` to ~/.agregat/drawings.json."""
    path = config_path(symbol)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(drawings, f, indent=2, default=str)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"Could not write {path}: {e!r}")
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


__all__ = ["load", "save", "config_path"]
