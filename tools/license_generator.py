"""
AGREGAT License Manager — Isolated Generator
Цей файл призначений ТІЛЬКИ для розробника і не компілюється в фінальний клієнтський .exe.
"""

import sys
import os
import hmac
import hashlib
import json
import base64
from datetime import datetime, timezone, timedelta

# Додаємо кореневу директорію в шлях імпорту, щоб бачити engine
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.security.license_manager import _build_key, get_hwid

def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """Просте, але ефективне симетричне XOR шифрування."""
    return bytes(a ^ key[i % len(key)] for i, a in enumerate(data))

def generate_license(hwid: str, exp_date_iso: str) -> str:
    key = _build_key(hwid)
    payload = f"{hwid}|{exp_date_iso}"
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    data = {"h": hwid, "e": exp_date_iso, "s": signature}
    raw = json.dumps(data, separators=(',', ':')).encode()
    
    encrypted_blob = _xor_crypt(raw, key)
    return base64.b64encode(encrypted_blob).decode()

if __name__ == "__main__":
    try:
        print("╔══════════════════════════════════════════════╗")
        print("║     AGREGAT — LICENSE KEY GENERATOR          ║")
        print("╚══════════════════════════════════════════════╝")
        print()

        target = input("  HWID інвестора (Enter = свій): ").strip()
        if not target:
            target = get_hwid()
            print(f"  → Ваш HWID: {target}")

        days = input("  Кількість днів доступу (Enter = 3): ").strip()
        if not days:
            days = "3"

        exp = (datetime.now(timezone.utc) + timedelta(days=int(days))).strftime("%Y-%m-%dT23:59:59")

        license_key = generate_license(target, exp)

        print()
        print("  ┌─── ЛІЦЕНЗІЙНИЙ КЛЮЧ ───┐")
        print(f"  {license_key}")
        print("  └────────────────────────┘")
        print()
        print("  Інвестор вставляє цей ключ у вікні активації програми.")
        print("  Нічого більше робити не потрібно!")
    except Exception as e:
        print(f"\n  ❌ ПОМИЛКА: {e}")
    
    print("\n" + "="*46)
    input("  Натисніть ENTER, щоб вийти...")
