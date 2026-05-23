import os
from typing import Optional

def truthy_env(value: Optional[str]) -> Optional[bool]:
    """Tri-state env parser: True/False/None."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on", "y"}:
        return True
    if v in {"0", "false", "no", "off", "n", ""}:
        return False
    return None
