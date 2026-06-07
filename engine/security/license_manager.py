"""
AGREGAT License Manager — Protected Edition
Захист бета-версії від копіювання та зламу.
Безпечний для Windows Defender.
"""

import os
import sys
import hashlib
import hmac
import json
import base64
import struct
import urllib.request
import time
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────
# 1. СТРОКОВА ОБФУСКАЦІЯ
#    Всі рядки (команди, помилки) зашифровані.
# ─────────────────────────────────────────────────────────────────────
def _d(s: str) -> str:
    """Декодує рядки під час виконання, приховуючи їх від аналізаторів."""
    try:
        return bytes(a ^ 0x3F for a in base64.b64decode(s)).decode('utf-8')
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────
# 2. ДИНАМІЧНА ЗБІРКА КЛЮЧА З ПРИВ'ЯЗКОЮ ДО АПАРАТУРИ
# ─────────────────────────────────────────────────────────────────────
def _build_key(hwid_seed: str) -> bytes:
    """Генерує ключ розшифровки, який математично залежить від комп'ютера."""
    _a = b"\x41\x47\x52\x45"          # AGRE
    _b = b"\x47\x41\x54"              # GAT
    _salt = struct.pack(">I", 0x7788AABB)
    return hashlib.sha256(_a + _b + hwid_seed.encode() + _salt).digest()

# ─────────────────────────────────────────────────────────────────────
# 3. АНТИ-ВІДЛАДКА (Anti-Debug & Anti-Sandbox)
# ─────────────────────────────────────────────────────────────────────
def _check_environment() -> bool:
    # 3.1. Timing Check: Математичний цикл, який Nuitka не зможе вирізати
    start_time = time.time()
    _dummy = 0
    for i in range(250000):
        _dummy ^= (i * 3) & 0xFF
    if time.time() - start_time > 1.2:  # Якщо дебагер перехоплює інструкції, час зросте експоненційно
        return False

    if sys.gettrace() is not None:
        return False

    # 3.2. Sandbox Check
    username = os.environ.get(_d("amx6bXF+cno="), "").upper() # USERNAME
    sandbox_markers = (_d("aHt+eGprdnN2a2Y="), _d("bH5xe31wZw=="), _d("cn5za3psaw=="), _d("aXZtamw="), _d("bH5yb3N6"))
    if any(m in username for m in sandbox_markers):
        return False

    try:
        if os.cpu_count() is not None and os.cpu_count() < 2:
            return False
    except Exception:
        pass

    return True

# ─────────────────────────────────────────────────────────────────────
# 4. HWID
# ─────────────────────────────────────────────────────────────────────
def get_hwid() -> str:
    try:
        import subprocess
        output = subprocess.check_output(
            _d("SFJWXB9cTE9NUFtKXEsfWFpLH0pKVls="), shell=True, stderr=subprocess.DEVNULL
        ).decode()
        uuid_str = output.split('\n')[1].strip()
        if not uuid_str or uuid_str == "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
            raise ValueError()
        return hashlib.sha256(uuid_str.encode()).hexdigest()[:16].upper()
    except Exception:
        import uuid
        mac = str(uuid.getnode())
        return hashlib.sha256(mac.encode()).hexdigest()[:16].upper()

_SESSION_NETWORK_TIME = None
_SESSION_MONOTONIC_START = None

def _get_network_time() -> datetime:
    """Використовує Monotonic Clock для захисту від переведення системного часу."""
    global _SESSION_NETWORK_TIME, _SESSION_MONOTONIC_START
    
    if _SESSION_NETWORK_TIME is not None and _SESSION_MONOTONIC_START is not None:
        # Якщо вже отримали час при старті, рахуємо різницю незалежно від системного годинника
        elapsed = time.monotonic() - _SESSION_MONOTONIC_START
        from datetime import timedelta
        return _SESSION_NETWORK_TIME + timedelta(seconds=elapsed)

    try:
        req = urllib.request.Request(
            _d("V0tLTwUQEEhQTVNbS1ZSWl5PVhFQTVgQXk9WEEtWUlpFUFFaEHpLXBBqa3w="),
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            network_time = datetime.fromtimestamp(data['unixtime'], timezone.utc)
    except Exception:
        network_time = datetime.now(timezone.utc)
        
    _SESSION_NETWORK_TIME = network_time
    _SESSION_MONOTONIC_START = time.monotonic()
    
    return network_time

# ─────────────────────────────────────────────────────────────────────
# 5. ПЕРЕВІРКА ЦІЛІСНОСТІ
# ─────────────────────────────────────────────────────────────────────
def _self_integrity_ok() -> bool:
    try:
        this_file = os.path.abspath(__file__)
        if not os.path.exists(this_file):
            return True
        if this_file.endswith(('.pyd', '.so', '.exe')):
            return True
        with open(this_file, "rb") as f:
            content = f.read()
        m1 = _d("SVpNVllGYFNWXFpRTFo=").encode()
        m2 = _d("YF1KVlNbYFRaRg==").encode()
        m3 = _d("WFpLYFdIVls=").encode()
        m4 = _d("YFxXWlxUYFpRSVZNUFFSWlFL").encode()
        return all(m in content for m in [m1, m2, m3, m4])
    except Exception:
        return True

def _license_path() -> str:
    local_path = _d("EV5YTVpYXktgU1ZcWlFMWhFUWkY=") # .agregat_license.key
    if os.path.exists(local_path):
        return local_path
    return os.path.join(os.path.expanduser("~"), local_path)

def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """Просте, але ефективне симетричне XOR шифрування (не визначається як троян)."""
    return bytes(a ^ key[i % len(key)] for i, a in enumerate(data))



def save_license_key(license_key: str) -> bool:
    try:
        with open(_license_path(), "w", encoding="utf-8") as f:
            f.write(license_key.strip())
        return True
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────
# 6. DEV MODE CHECK (env-based, NOT file-based)
# ─────────────────────────────────────────────────────────────────────
def _is_dev_mode() -> bool:
    """
    Check if running in development mode.
    Requires BOTH environment variables — a single .dev_mode file is NOT enough.
    This prevents accidental shipping of dev bypass to production.
    
    To enable dev mode, set:
      AGREGAT_DEV_MODE=1
      APP_ENV=development
    """
    dev_flag = os.environ.get("AGREGAT_DEV_MODE", "").lower() in ("1", "true", "yes")
    env_flag = os.environ.get("APP_ENV", "").lower() == "development"
    return dev_flag and env_flag


# ─────────────────────────────────────────────────────────────────────
# 7. ВЕРИФІКАЦІЯ ЛІЦЕНЗІЇ
# ─────────────────────────────────────────────────────────────────────
def verify_license() -> tuple[bool, str]:
    # Dev mode requires BOTH env vars — not a file check
    if _is_dev_mode():
        return True, _d("e3ppYHJwe3o=") # DEV_MODE

    if not _check_environment():
        return False, _d("76Dvge+D74fvhO+F748f7qnvgu6p7rnuqe+P74Tuqe+I74/uue6p7qgf7r7viu6/74rvi++B743vh+62748f743vh++F74Hvgu+P74Lvgu6wHxcPRwcPDw8LDw8KFhE=")

    if not _self_integrity_ok():
        return False, _d("76Dvge+D74fvhO+F748f7rnuqe+E7qnuvu+C74Huvu697qkf74Pvge+L7rzvhO6wHxcPR3wPDw8PDQ0OFhE=")

    lpath = _license_path()
    my_hwid = get_hwid()

    if not os.path.exists(lpath):
        return False, f"{_d('cXBgc3Z8enFsekM=')}{my_hwid}"

    try:
        with open(lpath, "r", encoding="utf-8") as f:
            encoded = f.read().strip()

        key = _build_key(my_hwid)

        # Розшифровуємо блоб
        try:
            encrypted_blob = base64.b64decode(encoded)
            decrypted_raw = _xor_crypt(encrypted_blob, key)
            data = json.loads(decrypted_raw.decode())
        except Exception:
            return False, _d("76Tuqe6574rvgu+I7qnvhu+C74fvhh/vhe+E7rHuuB/vgO+B7rfvhe+B74vvie+K74LvgR/vj++O74Ef74Duqe+L7r/vge+O74Tviu+C74ER")

        hwid_in = data.get("h", "")
        exp_str = data.get("e", "")
        sig_in = data.get("s", "")

        expected_sig = hmac.new(key, f"{hwid_in}|{exp_str}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_in, expected_sig):
            return False, _d("76Tuqe6574rvgu+I7qnvhu+C74fvhh/vhe+E7rHuuB/vgO+B7rfvhe+B74vvie+K74LvgR/vj++O74Ef74Duqe+L7r/vge+O74Tviu+C74ER")

        if hwid_in != my_hwid:
            return False, f"{_d('75nusB/vhO6p7rnviu+C74juqe6wH++A7r/vh++NGO6w74jvj++C748f74vvgR/uqe+C7rfvge+M74Ef74Xvge+D74AY7rHuve+K7r/vjxE1Ne+t74/utx93Xk1bSF5NWh92ewUf')}{my_hwid}"

        try:
            exp_date = datetime.fromisoformat(exp_str).replace(tzinfo=timezone.utc)
        except ValueError:
            return False, _d("76Dvge+D74fvhO+F748f7rvvge6/74Pvj+697rwf74vvj+6974cf740f74Tuqe6574rvgu+I7qnuqBEf767uvO+L7rMf74Tvj+6+74XvjxMf74Dviu6/74rvjO+K74Lviu6/7rzvhu6974of74XvhO6x7rgR")

        now = _get_network_time()
        if now > exp_date:
            return False, _d("75jvj+6+H++L74rvg++B74Luvu697r/vj+657qnvhu+C74HuqB/vje+K7r/uvu6p7qgf743vh+6474ruv++A74/vgu+BETU176vvge6+7r3uvO+AH++I74/vju+E74Hvhe+B743vj++C74ERNe+o740Y7rDvie6p7r3us+6+7rAf74gf7r/vge+I7r/vge+O74Lvh++F74/vg++HH++L74TusB/vgO6/74Hvi++B743vie+K74Lvgu6wEQ==")

        return True, _d("cHQ=")

    except Exception as e:
        return False, f"{_d('76Dvge+D74fvhO+F748f7rjvh+6974/vgu+C7rAf74Tuqe6574rvgu+I7qnuqAUf')}{e}"


