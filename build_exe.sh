#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  NEXUS IDE — Build Executable (macOS / Linux)
#  Run this once to produce a standalone binary in dist/NEXUS_IDE
#  Output: dist/NEXUS_IDE   (~80–120 MB, no Python install needed to run)
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo ""
echo "  NEXUS IDE — Executable Builder"
echo "  ================================"
echo ""

# ── Check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3.11+ is required to build."
    echo "        Install from https://python.org"
    exit 1
fi

PY_VER=$(python3 -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')")
echo "[INFO] Python $PY_VER detected"

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d ".venv_build" ]; then
    echo "[INFO] Creating build virtual environment..."
    python3 -m venv .venv_build
fi
source .venv_build/bin/activate
echo "[INFO] Virtual environment active"

# ── Install all dependencies + PyInstaller ────────────────────────────────────
echo "[INFO] Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q pyinstaller

# ── Clean previous build ──────────────────────────────────────────────────────
rm -f dist/NEXUS_IDE
rm -rf build/ 2>/dev/null || true

# ── Build ─────────────────────────────────────────────────────────────────────
echo ""
echo "[INFO] Building NEXUS_IDE binary — this takes 1–3 minutes..."
echo ""

pyinstaller nexus.spec --noconfirm

# ── macOS: remove quarantine flag (allows running without Gatekeeper popup) ───
if [[ "$OSTYPE" == "darwin"* ]]; then
    if [ -f "dist/NEXUS_IDE" ]; then
        xattr -cr dist/NEXUS_IDE 2>/dev/null || true
        echo "[INFO] Removed macOS quarantine flag"
    fi
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  BUILD COMPLETE"
echo ""
echo "  Binary:  dist/NEXUS_IDE"
echo ""
echo "  To run:  ./dist/NEXUS_IDE"
echo "  The IDE will open in your browser automatically."
echo ""
echo "  To share: copy the dist/ folder to another machine."
echo "  No Python installation is needed on the target machine."
echo "════════════════════════════════════════════════════════"
echo ""
