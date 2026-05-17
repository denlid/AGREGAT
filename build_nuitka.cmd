@echo off
echo Building AGREGAT with Nuitka...

REM Define Nuitka command
python -m nuitka ^
    --standalone ^
    --lto=yes ^
    --remove-output ^
    --enable-plugin=pyside6 ^
    --include-data-dir=gui/themes=gui/themes ^
    --include-data-dir=gui/widgets=gui/widgets ^
    --include-data-dir=config=config ^
    --include-data-dir=strategies=strategies ^
    --include-package=engine ^
    --include-package=engine.security ^
    --include-module=PySide6.QtOpenGL ^
    --include-module=PySide6.QtOpenGLWidgets ^
    --windows-console-mode=disable ^
    --company-name="AGREGAT" ^
    --product-name="AGREGAT Platform" ^
    --product-version="7.7.8" ^
    --windows-icon-from-ico=assets\icon.ico ^
    --output-dir=dist ^
    app.py

echo Build completed! You can find the compiled application in the 'dist' folder.
pause
