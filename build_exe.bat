@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  NEXUS IDE — Build Windows Executable
REM  Run this once to produce dist\NEXUS_IDE.exe
REM  No Python install is needed on the machine that RUNS the exe.
REM ─────────────────────────────────────────────────────────────────────────────

echo.
echo   NEXUS IDE - EXE Builder
echo   ========================
echo.

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo.
    echo   1. Go to https://python.org/downloads
    echo   2. Download Python 3.11 or 3.12
    echo   3. During install, TICK "Add Python to PATH"
    echo   4. Restart this script
    echo.
    pause
    exit /b 1
)

REM ── Check Python version (warn if 3.14+ — very new, may have issues) ─────────
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [INFO] Python %PY_VER% detected

REM ── Virtual environment ───────────────────────────────────────────────────────
if not exist ".venv_build" (
    echo [INFO] Creating build virtual environment...
    python -m venv .venv_build
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call .venv_build\Scripts\activate.bat
echo [INFO] Virtual environment active

REM ── Upgrade pip using the correct Windows syntax ──────────────────────────────
REM  NOTE: On Windows you MUST use "python -m pip" not just "pip" to upgrade pip.
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip -q

REM ── Install all project dependencies ─────────────────────────────────────────
echo [INFO] Installing project dependencies...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Check your internet connection.
    pause
    exit /b 1
)

REM ── Install PyInstaller ───────────────────────────────────────────────────────
echo [INFO] Installing PyInstaller...
python -m pip install -q pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    pause
    exit /b 1
)

REM ── Clean previous build ─────────────────────────────────────────────────────
if exist "dist\NEXUS_IDE.exe" (
    echo [INFO] Removing previous build...
    del /f /q "dist\NEXUS_IDE.exe" 2>nul
)
if exist "build" (
    rmdir /s /q build 2>nul
)

REM ── Build ─────────────────────────────────────────────────────────────────────
echo.
echo [INFO] Building NEXUS_IDE.exe...
echo [INFO] This takes 2-4 minutes. The window may go quiet - that is normal.
echo.

python -m PyInstaller nexus.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Read the messages above for the exact error.
    echo.
    echo Common fixes:
    echo   - Make sure you are in the correct folder (must contain nexus.spec)
    echo   - Delete the .venv_build folder and try again
    echo   - Try Python 3.11 or 3.12 if you have 3.14 (very new, fewer tested packages)
    echo.
    pause
    exit /b 1
)

REM ── Verify output ─────────────────────────────────────────────────────────────
if not exist "dist\NEXUS_IDE.exe" (
    echo [ERROR] Build finished but NEXUS_IDE.exe was not created.
    echo         Check the output above for warnings.
    pause
    exit /b 1
)

REM ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo   BUILD COMPLETE
echo.
echo   File: dist\NEXUS_IDE.exe
echo.
echo   HOW TO RUN:
echo     Double-click dist\NEXUS_IDE.exe
echo     Your browser will open automatically.
echo.
echo   HOW TO SHARE:
echo     Copy the dist\ folder to any Windows PC.
echo     No Python install needed on that machine.
echo ============================================================
echo.
pause
