@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  NEXUS IDE — Local Startup Script (Windows)
REM ─────────────────────────────────────────────────────────────────────────────

echo.
echo   NEXUS IDE — Local Setup
echo.

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ is required.
    echo         Download from https://python.org
    pause
    exit /b 1
)

REM ── Virtual environment ───────────────────────────────────────────────────────
if not exist ".venv" (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
echo [INFO] Virtual environment active

REM ── Install dependencies ──────────────────────────────────────────────────────
echo [INFO] Installing dependencies...
pip install -q --upgrade pip
pip install -q -r requirements.txt

REM ── API Keys (optional — can also be set in the Settings panel) ───────────────
REM  Uncomment and fill in any keys you want to use:
REM  set NVIDIA_API_KEY=nvapi-...
REM  set OPENAI_API_KEY=sk-...
REM  set OPENROUTER_API_KEY=sk-or-...
REM  set GROQ_API_KEY=gsk_...

REM ── Ollama ────────────────────────────────────────────────────────────────────
REM  If Ollama is running on http://localhost:11434 it will be detected automatically.
REM  Install from: https://ollama.com
REM  Start with: ollama serve    Pull a model: ollama pull llama3.2

REM ── Session secret ────────────────────────────────────────────────────────────
if "%SESSION_SECRET%"=="" (
    for /f "delims=" %%i in ('python -c "import secrets; print(secrets.token_hex(32))"') do set SESSION_SECRET=%%i
)

REM ── Create workspace ──────────────────────────────────────────────────────────
if not exist "agent_workspace" mkdir agent_workspace

REM ── Launch ────────────────────────────────────────────────────────────────────
echo.
echo   NEXUS IDE — Starting on http://localhost:5000
echo   Press Ctrl+C to stop
echo.

python app.py
pause
