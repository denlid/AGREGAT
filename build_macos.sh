#!/bin/bash
# macOS build script — аналог build_nuitka.cmd
# Usage: ./build_macos.sh [--arch arm64|x86_64]

set -euo pipefail

ARCH="${1:-arm64}"
APP_NAME="AGREGAT"
APP_VERSION="7.7.8"
OUTPUT_DIR="dist"

echo "==> Building $APP_NAME for macOS ($ARCH)..."

# Convert .ico → .icns if .icns doesn't exist
if [ ! -f "assets/icon.icns" ]; then
    echo "--> Converting icon.ico to icon.icns..."
    python3 -c "
from PIL import Image
import os, tempfile, shutil

ico = Image.open('assets/icon.ico')
iconset = 'assets/icon.iconset'
os.makedirs(iconset, exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512]
for s in sizes:
    img = ico.resize((s, s), Image.LANCZOS)
    img.save(os.path.join(iconset, f'icon_{s}x{s}.png'))
    if s <= 256:
        img2 = ico.resize((s*2, s*2), Image.LANCZOS)
        img2.save(os.path.join(iconset, f'icon_{s}x{s}@2x.png'))

os.system('iconutil -c icns assets/icon.iconset -o assets/icon.icns')
shutil.rmtree(iconset)
print('--> icon.icns created')
"
fi

# Build with Nuitka
python3 -m nuitka \
    --standalone \
    --lto=yes \
    --remove-output \
    --enable-plugin=pyside6 \
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
echo "==> Build complete! App bundle at: $OUTPUT_DIR/app.dist/"
echo "==> To package: ./package_macos.sh"
