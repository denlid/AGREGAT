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
echo "[1/6] Cleaning previous builds..."
rm -rf dist build __pycache__
rm -rf engine/security/__pycache__

# ============================================================
# Phase 2: Code minification (free obfuscation)
# ============================================================
echo ""
echo "[2/6] Minifying security modules (free obfuscation)..."

if ! $PYTHON -c "import python_minifier" 2>/dev/null; then
    echo "  [INFO] Installing python-minifier..."
    $PYTHON -m pip install python-minifier
fi

$PYTHON scripts/minify_security.py || echo "  [WARNING] Minification failed, using original files"

# ============================================================
# Phase 2.5: Compute security file hashes for HIGH-07 integrity
# ============================================================
echo ""
echo "[2.5/6] Computing security file hashes for integrity checks..."

SECURITY_HASHES=""
for f in engine/security/license_manager.py engine/security/license_client.py engine/security/anti_debug.py engine/security/anti_tamper.py; do
    if [ -f "$f" ]; then
        FHASH=$(shasum -a 256 "$f" | awk '{print $1}')
        FNAME=$(basename "$f")
        if [ -n "$SECURITY_HASHES" ]; then
            SECURITY_HASHES="${SECURITY_HASHES},${FNAME}:${FHASH}"
        else
            SECURITY_HASHES="${FNAME}:${FHASH}"
        fi
    fi
done

if [ -n "$SECURITY_HASHES" ]; then
    echo "  [OK] Security hashes computed"
    echo "  [INFO] AGREGAT_SECURITY_HASHES will be embedded in binary"
    export AGREGAT_SECURITY_HASHES="$SECURITY_HASHES"
else
    echo "  [WARNING] Could not compute security hashes"
fi

# Also compute license_manager.py hash separately for its self-check
LICENSE_MANAGER_HASH=""
if [ -f "engine/security/license_manager.py" ]; then
    LICENSE_MANAGER_HASH=$(shasum -a 256 "engine/security/license_manager.py" | awk '{print $1}')
    export AGREGAT_LICENSE_MANAGER_HASH="$LICENSE_MANAGER_HASH"
fi

# ============================================================
# Phase 3: Nuitka compilation (hardened + obfuscated)
# ============================================================
echo ""
echo "[3/6] Compiling with Nuitka (hardened + obfuscated)..."
echo "This may take 10-30 minutes depending on your CPU..."
echo ""

# Anti-reverse engineering: Nuitka hardening flags
# --obfuscate-string-literals: Encrypts string literals in the binary
#   This prevents "strings" tool from revealing sensitive strings
# --obfuscate-filenames: Obfuscates Python filenames in tracebacks
#   Makes it harder to map compiled code back to source files
# NOTE: For maximum protection on macOS, consider:
#   1. Code signing with Apple Developer ID
#   2. Hardened Runtime entitlements (com.apple.security.cs.debugger prevents ptrace)
#   3. Section encryption with custom loader

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
    --obfuscate-string-literals \
    --obfuscate-filenames \
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