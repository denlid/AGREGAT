"""
AGREGAT — Security Module Minifier
Безкоштовна обфускація критичних модулів перед компіляцією.

Використовує python-minifier для:
- Перейменування локальних/глобальних змінних
- Видалення анотацій, docstrings, pass
- Hoisting літералів
- Стиснення коду

Оригінальні файли зберігаються як .bak і відновлюються після білду.
"""

import sys
from pathlib import Path

try:
    import python_minifier
except ImportError:
    print("[INFO] Installing python-minifier...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-minifier"])
    import python_minifier


# Критичні файли для мініфікації
SECURITY_FILES = [
    "engine/security/license_manager.py",
    "engine/security/license_client.py",
    "engine/security/anti_debug.py",
]


def minify_file(filepath: str) -> bool:
    """Мініфікує один файл, зберігаючи оригінал як .bak"""
    path = Path(filepath)
    
    if not path.exists():
        print(f"  [SKIP] {filepath} — not found")
        return False
    
    # Зберігаємо оригінал
    backup = path.with_suffix(path.suffix + ".bak")
    original = path.read_text(encoding="utf-8")
    backup.write_text(original, encoding="utf-8")
    
    try:
        minified = python_minifier.minify(
            original,
            rename_locals=True,
            rename_globals=True,
            hoist_literals=True,
            remove_annotations=True,
            remove_pass=True,
            remove_literal_statements=True,
            combine_statements=True,
        )
        
        path.write_text(minified, encoding="utf-8")
        
        original_size = len(original)
        minified_size = len(minified)
        reduction = (1 - minified_size / original_size) * 100
        
        print(f"  [OK] {filepath}: {original_size} → {minified_size} bytes ({reduction:.0f}% smaller)")
        return True
        
    except Exception as e:
        print(f"  [ERROR] {filepath}: {e}")
        # Відновлюємо оригінал при помилці
        backup.write_text(original, encoding="utf-8")
        return False


def main():
    print("=" * 50)
    print("AGREGAT Security Module Minifier")
    print("=" * 50)
    print()
    
    success_count = 0
    
    for filepath in SECURITY_FILES:
        if minify_file(filepath):
            success_count += 1
    
    print()
    print(f"Minified {success_count}/{len(SECURITY_FILES)} files")
    print("Original files saved as .bak (will be restored after build)")
    
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())