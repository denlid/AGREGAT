#!/bin/bash
# macOS build script (Intel x86_64) — аналог build_nuitka.cmd
# Скомпилированное приложение запустится на Intel и на Apple Silicon (через Rosetta 2)

set -euo pipefail

APP_NAME="AGREGAT"
APP_VERSION="7.7.8"
OUTPUT_DIR="dist"
ARCH="x86_64"

echo "==> Building $APP_NAME for macOS ($ARCH)..."

# Конвертация .ico → .icns с поддержкой полного спектра Retina-разрешений
if [ ! -f "assets/icon.icns" ]; then
    echo "--> Converting icon.ico to icon.icns..."
    python3 -c "
from PIL import Image
import os, tempfile, shutil

ico = Image.open('assets/icon.ico')
iconset = 'assets/icon.iconset'
os.makedirs(iconset, exist_ok=True)

# Размеры стандартных иконок и их Retina-аналогов (@2x)
sizes = [16, 32, 128, 256, 512]
for s in sizes:
    # Обычная иконка
    img = ico.resize((s, s), Image.LANCZOS)
    img.save(os.path.join(iconset, f'icon_{s}x{s}.png'))
    # Retina иконка
    img_2x = ico.resize((s*2, s*2), Image.LANCZOS)
    img_2x.save(os.path.join(iconset, f'icon_{s}x{s}@2x.png'))

os.system('iconutil -c icns assets/icon.iconset -o assets/icon.icns')
shutil.rmtree(iconset)
print('--> icon.icns created')
"
fi

# Сборка через Nuitka строго под Intel (x86_64)
python3 -m nuitka \
    --standalone \
    --lto=yes \
    --remove-output \
    --enable-plugin=pyside6 \
    --macos-target-arch="$ARCH" \
    --include-data-dir=gui/themes=gui/themes \
    --include-data-dir=gui/widgets=gui/widgets \
    --include-data-dir=config=config \
    --include-data-dir=strategies=strategies \
    --include-package=engine \
    --include-package=engine.security \
    --include-module=PySide6.QtOpenGL \
    --include-module=PySide6.QtOpenGLWidgets \
    --macos-create-app-bundle \
    --macos-app-name="$APP_NAME" \
    --macos-app-version="$APP_VERSION" \
    --macos-app-icon=assets/icon.icns \
    --output-dir="$OUTPUT_DIR" \
    app.py

echo ""
echo "==> Build complete! App bundle at: $OUTPUT_DIR/$APP_NAME.app"
echo "==> To package: ./package_macos.sh"ß