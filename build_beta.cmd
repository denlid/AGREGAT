@echo off
REM ============================================================
REM AGREGAT — Beta Build Script (Windows)
REM ============================================================
REM Builds AGREGAT.exe for beta testers using PyInstaller.
REM The .exe connects to the cloud server (Render.com) for licensing.
REM ============================================================

echo.
echo ============================================================
echo   AGREGAT Beta Build
echo ============================================================
echo.

REM ─── Check Python ─────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

REM ─── Check PyInstaller ────────────────────────────────────
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM ─── Clean previous builds ────────────────────────────────
echo Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist AGREGAT.spec del /q AGREGAT.spec

REM ─── Build ────────────────────────────────────────────────
echo.
echo Building AGREGAT.exe...
echo This may take 3-5 minutes...
echo.

pyinstaller ^
    --onefile ^
    --windowed ^
    --name AGREGAT ^
    --icon=assets/icon.ico ^
    --add-data "config;config" ^
    --hidden-import=PySide6 ^
    --hidden-import=sqlalchemy ^
    --hidden-import=asyncpg ^
    --hidden-import=cryptography ^
    --hidden-import=bcrypt ^
    --hidden-import=pyotp ^
    --hidden-import=httpx ^
    --hidden-import=nacl ^
    --hidden-import=OpenGL ^
    --collect-all PySide6 ^
    app.py

REM ─── Check result ─────────────────────────────────────────
echo.
if exist dist\AGREGAT.exe (
    echo ============================================================
    echo   BUILD SUCCESS!
    echo ============================================================
    echo.
    echo   File: dist\AGREGAT.exe
    for %%A in (dist\AGREGAT.exe) do echo   Size: %%~zA bytes
    echo.
    echo   Next steps:
    echo   1. Test the .exe locally (double-click)
    echo   2. Upload to GitHub Releases
    echo   3. Share link with beta testers
    echo.
) else (
    echo ============================================================
    echo   BUILD FAILED!
    echo ============================================================
    echo.
    echo   Check the error messages above.
    echo   Common issues:
    echo   - Missing dependencies: pip install -r requirements.txt
    echo   - Missing icon: check assets\icon.ico exists
    echo.
)

pause