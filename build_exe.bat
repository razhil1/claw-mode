@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  NEXUS IDE — Build Windows Executable
REM  Run this once on your Windows machine to produce NEXUS_IDE.exe
REM  Output: dist\NEXUS_IDE.exe   (~80–120 MB, no Python install needed to run)
REM ─────────────────────────────────────────────────────────────────────────────

echo.
echo   NEXUS IDE — EXE Builder
echo   ========================
echo.

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ is required to BUILD the exe.
    echo         Download from https://python.org
    pause
    exit /b 1
)

REM ── Virtual environment ───────────────────────────────────────────────────────
if not exist ".venv_build" (
    echo [INFO] Creating build virtual environment...
    python -m venv .venv_build
)
call .venv_build\Scripts\activate.bat

REM ── Install all dependencies + PyInstaller ────────────────────────────────────
echo [INFO] Installing dependencies...
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q pyinstaller

REM ── Optional: install UPX for smaller exe (skip if not available) ─────────────
REM  UPX compresses the exe. Download from https://upx.github.io if you want it.
REM  PyInstaller will find it automatically if it's on your PATH.

REM ── Clean previous build ─────────────────────────────────────────────────────
if exist "dist\NEXUS_IDE.exe" (
    echo [INFO] Removing old build...
    del /f /q "dist\NEXUS_IDE.exe"
)
if exist "build" rmdir /s /q build 2>nul

REM ── Build ─────────────────────────────────────────────────────────────────────
echo.
echo [INFO] Building NEXUS_IDE.exe — this takes 1–3 minutes...
echo.

pyinstaller nexus.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the output above for details.
    pause
    exit /b 1
)

REM ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo ════════════════════════════════════════════════════════
echo   BUILD COMPLETE
echo.
echo   Executable: dist\NEXUS_IDE.exe
echo.
echo   To run: double-click dist\NEXUS_IDE.exe
echo   The IDE will open in your browser automatically.
echo.
echo   To share: copy the entire dist\ folder to another PC.
echo   No Python installation is needed on the target machine.
echo ════════════════════════════════════════════════════════
echo.
pause
