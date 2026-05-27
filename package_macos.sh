#!/bin/bash
# macOS packaging — аналог agregat_installer.iss
# Створює .pkg (встановлення в /Applications) та .dmg (drag-and-drop)
# Usage: ./package_macos.sh

set -euo pipefail

APP_NAME="AGREGAT"
APP_VERSION="7.7.8"
APP_DIST="dist/app.dist"
APP_BUNDLE="dist/$APP_NAME.app"
OUTPUT_DIR="dist/installers"

mkdir -p "$OUTPUT_DIR"

if [ ! -d "$APP_DIST" ]; then
    echo "Error: Build not found. Run ./build_macos.sh first."
    exit 1
fi

# === .pkg installer (як Inno Setup — встановлює в /Applications) ===
echo "--> Building .pkg installer..."
pkgbuild \
    --root "$APP_DIST" \
    --identifier "com.agregat.platform.pkg" \
    --version "$APP_VERSION" \
    --install-location "/Applications/$APP_NAME" \
    "$OUTPUT_DIR/${APP_NAME}-${APP_VERSION}-macos.pkg"
echo "    -> $OUTPUT_DIR/${APP_NAME}-${APP_VERSION}-macos.pkg"

# === .dmg (drag-and-drop installer) ===
echo "--> Building .dmg..."
if command -v create-dmg &> /dev/null; then
    # Використовуємо .app бандл, якщо Nuitka його створив
    DMG_SRC="${APP_BUNDLE}"
    if [ ! -d "$APP_BUNDLE" ]; then
        DMG_SRC="$APP_DIST"
    fi

    create-dmg \
        --volname "$APP_NAME" \
        --volicon "assets/icon.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "$APP_NAME.app" 150 190 \
        --hide-extension "$APP_NAME.app" \
        --app-drop-link 450 190 \
        "$OUTPUT_DIR/${APP_NAME}-${APP_VERSION}-macos.dmg" \
        "$DMG_SRC" 2>/dev/null || {
        echo "    -> create-dmg failed, using hdiutil..."
        hdiutil create -volname "$APP_NAME" -srcfolder "$APP_DIST" \
            -ov -format UDZO "$OUTPUT_DIR/${APP_NAME}-${APP_VERSION}-macos.dmg"
    }
else
    echo "    -> create-dmg not found, using hdiutil..."
    hdiutil create -volname "$APP_NAME" -srcfolder "$APP_DIST" \
        -ov -format UDZO "$OUTPUT_DIR/${APP_NAME}-${APP_VERSION}-macos.dmg"
fi
echo "    -> $OUTPUT_DIR/${APP_NAME}-${APP_VERSION}-macos.dmg"

echo ""
echo "==> Packaging complete!"
ls -lh "$OUTPUT_DIR"
