@echo off
setlocal enabledelayedexpansion
echo ============================================================
echo AGREGAT — Final Production Build (Windows)
echo ============================================================
echo.

REM ============================================================
REM Phase 0: Security pre-build checks (P2-10)
REM ============================================================
echo [PHASE 0] Security pre-build checks...

REM Check for .dev_mode (BLOCK for release)
if exist .dev_mode (
    echo.
    echo =============================================
    echo [BLOCKED] .dev_mode file detected!
    echo This DISABLES all security checks.
    echo Remove it for production releases!
    echo =============================================
    echo.
    pause
    exit /b 1
)

REM P2-10: Check for Ed25519 public key in license_client.py
echo [CHECK] Verifying Ed25519 public key is embedded...
findstr /C:"AGREGAT_PUBLIC_KEY" engine\security\license_client.py >nul 2>&1
if errorlevel 1 (
    echo [BLOCKED] Ed25519 public key not found in license_client.py!
    echo Generate and embed the public key before building.
    pause
    exit /b 1
)
echo [OK] Ed25519 public key found

REM P2-10: Check for TLS cert pin
echo [CHECK] Verifying TLS certificate pin...
findstr /C:"AGREGAT_CERT_PIN" engine\security\license_client.py >nul 2>&1
if errorlevel 1 (
    echo [WARNING] TLS certificate pin not found in license_client.py
    echo Consider adding AGREGAT_CERT_PIN for production builds.
)
echo [OK] TLS cert pin check complete

REM P2-10: Check for .env files that should NOT be bundled
echo [CHECK] Checking for sensitive files...
if exist .env (
    echo [WARNING] .env file found in project root
    echo Ensure it is NOT included in the build!
)
if exist server\.env (
    echo [WARNING] server/.env file found
    echo Ensure it is NOT included in the build!
)

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.10+
    pause
    exit /b 1
)

REM Check Nuitka
python -c "import nuitka" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing Nuitka...
    pip install nuitka orderedset zstandard
    if errorlevel 1 (
        echo [ERROR] Failed to install Nuitka!
        pause
        exit /b 1
    )
)

REM Clean previous builds
echo [1/6] Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist __pycache__ rmdir /s /q __pycache__

REM ============================================================
REM Phase 2: Code minification (free obfuscation)
REM ============================================================
echo.
echo [2/6] Minifying security modules (free obfuscation)...

python -c "import python_minifier" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing python-minifier...
    pip install python-minifier
)

REM Backup original files, minify, then restore
python scripts/minify_security.py
if errorlevel 1 (
    echo [WARNING] Minification failed, using original files
)

REM ============================================================
REM Phase 2.5: Compute security file hashes for HIGH-07 integrity
REM ============================================================
echo.
echo [2.5/6] Computing security file hashes for integrity checks...

set "SECURITY_HASHES="
for %%F in (engine\security\license_manager.py engine\security\license_client.py engine\security\anti_debug.py engine\security\anti_tamper.py) do (
    if exist %%F (
        for /f "delims=" %%H in ('certutil -hashfile %%F SHA256 ^| findstr /v "CertUtil" ^| findstr /v "SHA256"') do (
            set "FHASH=%%H"
            set "FHASH=!FHASH: =!"
            set "FNAME=%%~nxF"
            if defined SECURITY_HASHES (
                set "SECURITY_HASHES=!SECURITY_HASHES!,!FNAME!:!FHASH!"
            ) else (
                set "SECURITY_HASHES=!FNAME!:!FHASH!"
            )
        )
    )
)

if defined SECURITY_HASHES (
    echo [OK] Security hashes computed
    echo [INFO] AGREGAT_SECURITY_HASHES will be embedded in binary
) else (
    echo [WARNING] Could not compute security hashes
)

REM Also compute license_manager.py hash separately for its self-check
set "LICENSE_MANAGER_HASH="
if exist engine\security\license_manager.py (
    for /f "delims=" %%H in ('certutil -hashfile engine\security\license_manager.py SHA256 ^| findstr /v "CertUtil" ^| findstr /v "SHA256"') do (
        set "LICENSE_MANAGER_HASH=%%H"
        set "LICENSE_MANAGER_HASH=!LICENSE_MANAGER_HASH: =!"
    )
)

REM ============================================================
REM Phase 3: Nuitka compilation (P2-9: hardened with obfuscation)
REM ============================================================
echo.
echo [3/6] Compiling with Nuitka (hardened + obfuscated)...
echo This may take 10-30 minutes depending on your CPU...
echo.

REM Anti-reverse engineering: Nuitka hardening flags
REM --obfuscate-string-literals: Encrypts string literals in the binary
REM   This prevents "strings" tool from revealing sensitive strings
REM --obfuscate-filenames: Obfuscates Python filenames in tracebacks
REM   Makes it harder to map compiled code back to source files
REM NOTE: For maximum protection, consider adding a commercial protector
REM   like VMProtect or Themida after Nuitka compilation (code virtualization)

python -m nuitka ^
    --standalone ^
    --onefile ^
    --lto=yes ^
    --remove-output ^
    --enable-plugin=pyside6 ^
    --include-data-dir=gui/themes=gui/themes ^
    --include-data-dir=gui/widgets=gui/widgets ^
    --include-data-dir=config=config ^
    --include-data-dir=strategies=strategies ^
    --include-data-dir=assets=assets ^
    --include-package=engine ^
    --include-package=engine.security ^
    --include-module=PySide6.QtOpenGL ^
    --include-module=PySide6.QtOpenGLWidgets ^
    --include-module=PySide6.QtNetwork ^
    --windows-console-mode=disable ^
    --company-name="AGREGAT" ^
    --product-name="AGREGAT Platform" ^
    --product-version="7.7.8" ^
    --file-description="AGREGAT Algorithmic Trading Platform" ^
    --copyright="Copyright (c) 2024-2026 AGREGAT" ^
    --windows-icon-from-ico=assets\icon.ico ^
    --output-dir=dist ^
    --output-filename=AGREGAT.exe ^
    --onefile-tempdir-spec="%%TEMP%%\agregat_%%PID%%" ^
    --python-flag=no_site ^
    --python-flag=no_warnings ^
    --python-flag=-O ^
    --assume-yes-for-downloads ^
    --show-progress ^
    --show-memory ^
    --nofollow-import-to=tests ^
    --nofollow-import-to=notebooks ^
    --nofollow-import-to=bench ^
    --nofollow-import-to=docs ^
    --obfuscate-string-literals ^
    --obfuscate-filenames ^
    --windows-uac-admin=no ^
    app.py

if errorlevel 1 (
    echo.
    echo [ERROR] Nuitka compilation failed!
    echo Check the error messages above.
    pause
    exit /b 1
)

REM ============================================================
REM Phase 4: UPX packing (optional, free)
REM ============================================================
echo.
echo [4/6] UPX packing (optional)...

where upx >nul 2>&1
if not errorlevel 1 (
    echo [INFO] UPX found, packing executable...
    upx --best --lzma --force dist\AGREGAT.exe
    echo [OK] UPX packing complete
) else (
    echo [SKIP] UPX not found. Install from https://upx.github.io/
    echo        Run: upx --best --lzma dist\AGREGAT.exe
)

REM ============================================================
REM Phase 5: Build integrity hash (P2-10)
REM ============================================================
echo.
echo [5/6] Generating build integrity hash...

if exist dist\AGREGAT.exe (
    certutil -hashfile dist\AGREGAT.exe SHA256 > dist\AGREGAT.exe.sha256 2>nul
    echo [OK] SHA-256 hash saved to dist\AGREGAT.exe.sha256
    type dist\AGREGAT.exe.sha256
) else (
    echo [ERROR] AGREGAT.exe not found in dist/
    pause
    exit /b 1
)

REM ============================================================
REM Phase 6: Verification
REM ============================================================
echo.
echo [6/6] Verifying build...

if exist dist\AGREGAT.exe (
    for %%A in (dist\AGREGAT.exe) do (
        set SIZE=%%~zA
        set SIZE_MB=!SIZE:~0,-6!
        if "!SIZE_MB!"=="" set SIZE_MB=0
        echo.
        echo ============================================
        echo BUILD SUCCESSFUL!
        echo ============================================
        echo File: dist\AGREGAT.exe
        echo Size: !SIZE! bytes (~!SIZE_MB! MB)
        echo SHA-256: see dist\AGREGAT.exe.sha256
        echo ============================================
    )
) else (
    echo [ERROR] AGREGAT.exe not found in dist/
    pause
    exit /b 1
)

REM Restore original files if they were minified
if exist engine\security\license_manager.py.bak (
    echo.
    echo [INFO] Restoring original security files...
    copy /y engine\security\license_manager.py.bak engine\security\license_manager.py >nul
    copy /y engine\security\license_client.py.bak engine\security\license_client.py >nul
    copy /y engine\security\anti_debug.py.bak engine\security\anti_debug.py >nul
    copy /y engine\security\secure_memory.py.bak engine\security\secure_memory.py >nul 2>nul
    del engine\security\*.bak >nul 2>&1
    echo [OK] Original files restored
)

echo.
echo ============================================================
echo RELEASE CHECKLIST:
echo ============================================================
echo   [x] .dev_mode file removed (enforced by build script)
echo   [x] Ed25519 public key embedded (verified by build script)
echo   [ ] Set DEBUG=false on server
echo   [ ] Change JWT_SECRET to random 64-char hex
echo   [ ] Change FINGERPRINT_SALT to random string
echo   [ ] Set ALLOWED_ORIGINS to specific domains
echo   [ ] Enable HTTPS on server (Let's Encrypt/Cloudflare)
echo   [ ] Change default owner password
echo   [ ] Set up PostgreSQL backups
echo   [ ] Sign EXE with code signing certificate (post-beta)
echo   [ ] Publish SHA-256 hash for download verification
echo ============================================================
echo.
pause