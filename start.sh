#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NEXUS IDE — Local Startup Script (Linux / macOS)
# ─────────────────────────────────────────────────────────────────────────────
set -e

# ── Check Python ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3.11+ is required. Install from https://python.org"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[INFO] Python $PY_VER detected"

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate
echo "[INFO] Virtual environment active"

# ── Install / upgrade dependencies ───────────────────────────────────────────
echo "[INFO] Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── API Keys (optional — can also be set in the Settings panel) ──────────────
# Uncomment and fill in any keys you want to use:
# export NVIDIA_API_KEY="nvapi-..."
# export OPENAI_API_KEY="sk-..."
# export OPENROUTER_API_KEY="sk-or-..."
# export GROQ_API_KEY="gsk_..."

# ── Ollama (optional — install from https://ollama.com) ──────────────────────
# If Ollama is running locally on port 11434, it will be detected automatically.
# Start it with: ollama serve
# Pull a model: ollama pull llama3.2

# ── Session secret (auto-generated if not set) ───────────────────────────────
if [ -z "$SESSION_SECRET" ]; then
    export SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

# ── Create workspace ─────────────────────────────────────────────────────────
mkdir -p agent_workspace

# ── Launch ───────────────────────────────────────────────────────────────────
echo ""
echo "  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗"
echo "  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝"
echo "  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗"
echo "  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║"
echo "  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║"
echo "  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝"
echo ""
echo "  NEXUS IDE — Starting on http://localhost:5000"
echo "  Press Ctrl+C to stop"
echo ""

python3 app.py
