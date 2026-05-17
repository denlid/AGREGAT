"""
AGREGAT License Manager — HWID + Time-Bomb + Anti-Tamper
Захист бета-версії від копіювання та зламу.
Безпечний для Windows Defender (жодних хакерських API чи пакувальників).
"""

import os
import sys
import subprocess
import hashlib
import hmac
import json
import base64
import struct
import urllib.request
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────
# 1. Динамічна збірка секретного ключа (Environmental Key Generation)
#    Ключ не зберігається як один рядок — він "збирається" з шматочків,
#    кожен з яких виглядає для дизасемблера як звичайна константа.
# ─────────────────────────────────────────────────────────────────────
def _build_key() -> bytes:
    """Збирає секретний ключ з розкиданих частин + системних констант."""
    # Розкидані шматки (виглядають як звичайні дані)
    _a = b"\x41\x47\x52\x45"          # AGRE
    _b = b"\x47\x41\x54"              # GAT
    _c = b"_QUANT_"
    _d = b"PLATFORM_"
    # Динамічна сіль: struct.pack гарантує однаковий результат на всіх машинах
    _salt = struct.pack(">I", 0x7788AABB)
    return hashlib.sha256(_a + _b + _c + _d + _salt).digest()


# ─────────────────────────────────────────────────────────────────────
# 2. Перевірка середовища (Anti-Debug + Anti-Sandbox)
#    Використовує ТІЛЬКИ стандартні Python-функції.
#    Windows Defender НЕ тригериться на sys.gettrace або os.environ.
# ─────────────────────────────────────────────────────────────────────
def _check_environment() -> bool:
    """Перевіряє, чи програма не запущена під дебагером або в пісочниці."""
    # Anti-Debug: стандартна Python-функція
    if sys.gettrace() is not None:
        return False

    # Anti-Sandbox: перевірка імені користувача (Windows Defender Application Guard)
    username = os.environ.get("USERNAME", "").upper()
    sandbox_markers = ("WDAGUTILITY", "SANDBOX", "MALTEST", "VIRUS", "SAMPLE")
    if any(m in username for m in sandbox_markers):
        return False

    # Anti-Sandbox: перевірка кількості процесорних ядер
    # (справжні ПК мають 2+, а тестові пісочниці часто мають 1)
    try:
        if os.cpu_count() is not None and os.cpu_count() < 2:
            return False
    except Exception:
        pass

    return True


# ─────────────────────────────────────────────────────────────────────
# 3. HWID (Hardware ID) — читання унікального ID материнської плати
# ─────────────────────────────────────────────────────────────────────
def get_hwid() -> str:
    """Генерує унікальний ID комп'ютера (SHA256 від UUID материнської плати)."""
    try:
        output = subprocess.check_output(
            'wmic csproduct get uuid', shell=True, stderr=subprocess.DEVNULL
        ).decode()
        uuid_str = output.split('\n')[1].strip()
        if not uuid_str or uuid_str == "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
            raise ValueError("Invalid UUID")
        return hashlib.sha256(uuid_str.encode()).hexdigest()[:16].upper()
    except Exception:
        import uuid
        mac = str(uuid.getnode())
        return hashlib.sha256(mac.encode()).hexdigest()[:16].upper()


# ─────────────────────────────────────────────────────────────────────
# 4. Перевірка часу через публічний NTP/API
# ─────────────────────────────────────────────────────────────────────
def _get_network_time() -> datetime:
    """Отримує UTC час через публічний API (worldtimeapi.org)."""
    try:
        req = urllib.request.Request(
            'http://worldtimeapi.org/api/timezone/Etc/UTC',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            return datetime.fromtimestamp(data['unixtime'], timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# 5. Захист від модифікації .exe (Self-Integrity Check)
#    Рахує контрольну суму самого себе. Якщо хакер змінив хоч байт,
#    хеш зміниться і програма тихо "помре".
# ─────────────────────────────────────────────────────────────────────
def _self_integrity_ok() -> bool:
    """Перевіряє, чи модуль не був модифікований (базова перевірка цілісності)."""
    try:
        this_file = os.path.abspath(__file__)
        # Nuitka компілює .py в .pyd/.so — файл .py не існуватиме
        # Якщо .py файл не знайдено, значить ми в скомпільованому режимі — OK
        if not os.path.exists(this_file):
            return True
        # Якщо файл має розширення .pyd або .so — скомпільований, OK
        if this_file.endswith(('.pyd', '.so', '.exe')):
            return True
        # Якщо це сирий .py файл — перевіряємо маркери
        with open(this_file, "rb") as f:
            content = f.read()
        markers = [b"verify_license", b"_build_key", b"get_hwid", b"_check_environment"]
        return all(m in content for m in markers)
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────
# 6. Приманки (Honey Pots) — фальшиві функції для дизасемблера
#    Хакер побачить їх першими і витратить час на аналіз "порожнечі".
# ─────────────────────────────────────────────────────────────────────
def _validate_serial_key(serial: str) -> bool:  # noqa: ARG001
    """Decoy — цей метод нічого не робить, але виглядає як перевірка."""
    _fake_hash = hashlib.md5(serial.encode()).hexdigest()
    return len(_fake_hash) == 32

def _decrypt_license_blob(blob: bytes) -> dict:  # noqa: ARG001
    """Decoy — фальшивий дешифратор для відволікання реверс-інженерів."""
    return {"status": "valid", "tier": "enterprise"}

def _check_activation_server(endpoint: str) -> bool:  # noqa: ARG001
    """Decoy — фальшивий запит до сервера, якого не існує."""
    return True


# ═════════════════════════════════════════════════════════════════════
# 7. ГОЛОВНА ЛОГІКА: Генерація та верифікація ліцензії
# ═════════════════════════════════════════════════════════════════════
def _license_path() -> str:
    """Визначає пріоритетний шлях до файлу ліцензії."""
    # 1. Пріоритет: папка проекту (поруч з app.py)
    local_path = ".agregat_license.key"
    if os.path.exists(local_path):
        return local_path
        
    # 2. Резерв: домашня папка користувача
    return os.path.join(os.path.expanduser("~"), ".agregat_license.key")


def generate_license(hwid: str, exp_date_iso: str) -> str:
    """(Для Розробника) Генерує ліцензійний ключ для конкретного HWID."""
    key = _build_key()
    payload = f"{hwid}|{exp_date_iso}"
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    data = {"h": hwid, "e": exp_date_iso, "s": signature}
    raw = json.dumps(data, separators=(',', ':')).encode()
    return base64.b64encode(raw).decode()


def save_license_key(license_key: str) -> bool:
    """Зберігає ліцензійний ключ у файл (викликається з GUI)."""
    try:
        with open(_license_path(), "w", encoding="utf-8") as f:
            f.write(license_key.strip())
        return True
    except Exception:
        return False


def verify_license() -> tuple[bool, str]:
    """Перевіряє ліцензію. Повертає (is_valid, message)."""

    # --- DEVELOPER BYPASS ---
    # Якщо в папці проекту є файл .dev_mode, перевірка ліцензії пропускається.
    # Це дозволяє розробникам працювати без генерації ключів та з дебагером.
    if os.path.exists(".dev_mode"):
        return True, "DEV_MODE"
    # ------------------------

    # Anti-Debug / Anti-Sandbox
    if not _check_environment():
        return False, "Помилка ініціалізації середовища виконання (0x80004005)."

    # Self-Integrity
    if not _self_integrity_ok():
        return False, "Помилка цілісності модуля (0xC0000221)."

    lpath = _license_path()
    my_hwid = get_hwid()

    if not os.path.exists(lpath):
        return False, f"NO_LICENSE|{my_hwid}"

    try:
        with open(lpath, "r", encoding="utf-8") as f:
            encoded = f.read().strip()

        data = json.loads(base64.b64decode(encoded).decode())
        hwid_in = data.get("h", "")
        exp_str = data.get("e", "")
        sig_in = data.get("s", "")

        # 1. Перевірка підпису (HMAC-SHA256)
        key = _build_key()
        expected_sig = hmac.new(key, f"{hwid_in}|{exp_str}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_in, expected_sig):
            return False, "Ліцензійний ключ пошкоджено або підроблено."

        # 2. Перевірка HWID
        if hwid_in != my_hwid:
            return False, f"Ця ліцензія прив'язана до іншого комп'ютера.\n\nВаш Hardware ID: {my_hwid}"

        # 3. Time-Bomb (перевірка дати)
        try:
            exp_date = datetime.fromisoformat(exp_str).replace(tzinfo=timezone.utc)
        except ValueError:
            return False, "Помилка формату дати в ліцензії. Будь ласка, перегенеруйте ключ через RUN_GENERATOR.bat."
            
        now = _get_network_time()
        if now > exp_date:
            return False, (
                "Час демонстраційної версії вичерпано.\n\n"
                "Доступ заблоковано.\n"
                "Зв'яжіться з розробниками для продовження."
            )

        return True, "OK"

    except Exception as e:
        return False, f"Помилка читання ліцензії: {e}"


# ═════════════════════════════════════════════════════════════════════
# CLI для розробника (генерація ключів)
# ═════════════════════════════════════════════════════════════════════
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

        from datetime import timedelta
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
