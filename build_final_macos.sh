#!/bin/bash
# ============================================================
# AGREGAT — Final Production Build (macOS)
# ============================================================
# Usage: chmod +x build_final_macos.sh && ./build_final_macos.sh
# ============================================================

set -e  # Exit on error

echo "============================================================"
echo "AGREGAT — Final Production Build (macOS)"
echo "============================================================"
echo ""

# ============================================================
# Phase 1: Pre-build checks
# ============================================================
echo "[PHASE 1] Pre-build checks..."

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found! Install Python 3.10+"
    exit 1
fi

PYTHON=python3
echo "  Python: $($PYTHON --version 2>&1)"

# Check Nuitka
if ! $PYTHON -c "import nuitka" 2>/dev/null; then
    echo "  [INFO] Installing Nuitka..."
    $PYTHON -m pip install nuitka orderedset zstandard
fi

# Check for .dev_mode
if [ -f ".dev_mode" ]; then
    echo ""
    echo "============================================="
    echo "[WARNING] .dev_mode file detected!"
    echo "This DISABLES all security checks."
    echo "Remove it for production releases!"
    echo "============================================="
    echo ""
    read -p "Continue with .dev_mode? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Clean previous builds
echo "[1/5] Cleaning previous builds..."
rm -rf dist build __pycache__
rm -rf engine/security/__pycache__

# ============================================================
# Phase 2: Code minification (free obfuscation)
# ============================================================
echo ""
echo "[2/5] Minifying security modules (free obfuscation)..."

if ! $PYTHON -c "import python_minifier" 2>/dev/null; then
    echo "  [INFO] Installing python-minifier..."
    $PYTHON -m pip install python-minifier
fi

$PYTHON scripts/minify_security.py || echo "  [WARNING] Minification failed, using original files"

# ============================================================
# Phase 3: Nuitka compilation (hardened)
# ============================================================
echo ""
echo "[3/5] Compiling with Nuitka (hardened mode)..."
echo "This may take 10-30 minutes depending on your CPU..."
echo ""

# Detect architecture
ARCH=$(uname -m)
echo "  Architecture: $ARCH"

$PYTHON -m nuitka \
    --standalone \
    --onefile \
    --lto=yes \
    --remove-output \
    --enable-plugin=pyside6 \
    --include-data-dir=gui/themes=gui/themes \
    --include-data-dir=gui/widgets=gui/widgets \
    --include-data-dir=config=config \
    --include-data-dir=strategies=strategies \
    --include-data-dir=assets=assets \
    --include-package=engine \
    --include-package=engine.security \
    --include-module=PySide6.QtOpenGL \
    --include-module=PySide6.QtOpenGLWidgets \
    --include-module=PySide6.QtNetwork \
    --macos-create-app-bundle \
    --macos-app-name=AGREGAT \
    --macos-app-version=7.7.8 \
    --macos-app-icon=assets/icon.ico \
    --company-name="AGREGAT" \
    --product-name="AGREGAT Platform" \
    --product-version="7.7.8" \
    --file-description="AGREGAT Algorithmic Trading Platform" \
    --output-dir=dist \
    --output-filename=AGREGAT \
    --onefile-tempdir-spec="\$TMPDIR/agregat_\$\$" \
    --python-flag=no_site \
    --python-flag=no_warnings \
    --assume-yes-for-downloads \
    --show-progress \
    --show-memory \
    app.py

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Nuitka compilation failed!"
    echo "Check the error messages above."
    exit 1
fi

# ============================================================
# Phase 4: UPX packing (optional, free)
# ============================================================
echo ""
echo "[4/5] UPX packing (optional)..."

if command -v upx &> /dev/null; then
    echo "  [INFO] UPX found, packing executable..."
    upx --best --lzma --force dist/AGREGAT
    echo "  [OK] UPX packing complete"
else
    echo "  [SKIP] UPX not found. Install: brew install upx"
    echo "         Run: upx --best --lzma dist/AGREGAT"
fi

# ============================================================
# Phase 5: Verification and DMG creation
# ============================================================
echo ""
echo "[5/5] Verifying build..."

if [ -f "dist/AGREGAT" ] || [ -d "dist/AGREGAT.app" ]; then
    echo ""
    echo "============================================"
    echo "BUILD SUCCESSFUL!"
    echo "============================================"
    
    if [ -d "dist/AGREGAT.app" ]; then
        echo "App Bundle: dist/AGREGAT.app"
        SIZE=$(du -sh dist/AGREGAT.app | cut -f1)
        echo "Size: $SIZE"
        
        # Create DMG
        echo ""
        echo "Creating DMG..."
        if command -v create-dmg &> /dev/null; then
            create-dmg \
                --volname "AGREGAT" \
                --volicon "assets/icon.ico" \
                --window-pos 200 120 \
                --window-size 600 400 \
                --icon-size 100 \
                --icon "AGREGAT.app" 175 190 \
                --hide-extension "AGREGAT.app" \
                --app-drop-link 425 190 \
                "dist/AGREGAT.dmg" \
                "dist/AGREGAT.app"
            echo "DMG created: dist/AGREGAT.dmg"
        else
            echo "  [SKIP] create-dmg not found. Install: brew install create-dmg"
            echo "         Or use Disk Utility to create DMG manually"
        fi
    else
        echo "Binary: dist/AGREGAT"
        SIZE=$(du -sh dist/AGREGAT | cut -f1)
        echo "Size: $SIZE"
    fi
    
    echo "============================================"
else
    echo "[ERROR] AGREGAT not found in dist/"
    exit 1
fi

# Restore original files if they were minified
if [ -f "engine/security/license_manager.py.bak" ]; then
    echo ""
    echo "  [INFO] Restoring original security files..."
    cp -f engine/security/license_manager.py.bak engine/security/license_manager.py
    cp -f engine/security/license_client.py.bak engine/security/license_client.py
    cp -f engine/security/anti_debug.py.bak engine/security/anti_debug.py
    rm -f engine/security/*.bak
    echo "  [OK] Original files restored"
fi

echo ""
echo "============================================================"
echo "RELEASE CHECKLIST:"
echo "============================================================"
echo "  [ ] Remove .dev_mode file"
echo "  [ ] Set DEBUG=false on server"
echo "  [ ] Change JWT_SECRET to random 64-char hex"
echo "  [ ] Change FINGERPRINT_SALT to random string"
echo "  [ ] Set ALLOWED_ORIGINS to specific domains"
echo "  [ ] Enable HTTPS on server (Let's Encrypt/Cloudflare)"
echo "  [ ] Change default owner password"
echo "  [ ] Set up PostgreSQL backups"
echo "  [ ] Code sign with Apple Developer ID (optional)"
echo "  [ ] Notarize with Apple (required for distribution)"
echo "============================================================"
echo ""
echo "To code sign (requires Apple Developer account):"
echo "  codesign --deep --force --verify --verbose \\"
echo "    --sign 'Developer ID Application: Your Name' \\"
echo "    dist/AGREGAT.app"
echo ""
echo "To notarize:"
echo "  xcrun notarytool submit dist/AGREGAT.dmg \\"
echo "    --apple-id YOUR_EMAIL --password APP_PASSWORD \\"
echo "    --team-id YOUR_TEAM_ID --wait"
echo ""