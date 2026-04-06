# -*- mode: python ; coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────────────────────
#  NEXUS IDE — PyInstaller Build Specification
#  Build command:  pyinstaller nexus.spec
# ─────────────────────────────────────────────────────────────────────────────

import os
from pathlib import Path

ROOT = Path(SPECPATH)  # directory containing this .spec file


def _add_if_exists(src, dest):
    """Return (src, dest) tuple only if src exists — avoids build errors."""
    if os.path.exists(str(src)):
        return [(str(src), dest)]
    return []


# ── Data files to bundle ──────────────────────────────────────────────────────
# Only include files/folders that actually exist in the project directory.
datas = []

# Required folders
for folder, dest in [
    ("templates",      "templates"),
    ("static",         "static"),
    ("src",            "src"),
    ("agent_workspace","agent_workspace"),
]:
    if (ROOT / folder).exists():
        datas.append((str(ROOT / folder), dest))

# Optional root-level Python files (include whichever exist)
for fname in ["app.py", "main.py", "models.py", "nexus_launcher.py"]:
    datas.extend(_add_if_exists(ROOT / fname, "."))

# ── Hidden imports ────────────────────────────────────────────────────────────
# Packages that PyInstaller's static analysis misses because they use
# dynamic imports, __import__(), importlib, or lazy loading.
hidden_imports = [
    # Flask internals
    "flask",
    "flask.templating",
    "flask.json",
    "werkzeug",
    "werkzeug.middleware.proxy_fix",
    "werkzeug.security",
    "jinja2",
    "jinja2.ext",
    "markupsafe",
    "click",
    # OpenAI client (lazy sub-modules)
    "openai",
    "openai._models",
    "openai.resources",
    "openai.resources.chat",
    "openai.resources.chat.completions",
    "openai._streaming",
    "openai._client",
    "httpx",
    "httpcore",
    "anyio",
    "anyio._backends._asyncio",
    # tiktoken (loads encoding data at runtime)
    "tiktoken",
    "tiktoken.core",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    # requests / urllib3
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
    # psycopg2 (optional DB panel — skip gracefully if not installed)
    "psycopg2",
    "psycopg2.extras",
    # Standard library modules that sometimes get missed
    "encodings",
    "encodings.utf_8",
    "encodings.ascii",
    "email",
    "email.mime",
    "email.mime.text",
    "email.mime.multipart",
    # NEXUS internal packages
    "src.agent",
    "src.ultraworker",
    "src.llm",
    "src.toolbox",
]

# ── Collect entire packages (tiktoken needs its data files too) ───────────────
from PyInstaller.utils.hooks import collect_all

binaries  = []
all_datas = list(datas)

for pkg in ("tiktoken", "openai", "certifi"):
    try:
        pkg_datas, pkg_binaries, pkg_hiddens = collect_all(pkg)
        all_datas      += pkg_datas
        binaries       += pkg_binaries
        hidden_imports += pkg_hiddens
    except Exception as e:
        print(f"[nexus.spec] Warning: could not collect all for {pkg}: {e}")

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["nexus_launcher.py"],          # entry point
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=all_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim bundle size — these are not needed by NEXUS IDE
        "matplotlib", "numpy", "pandas", "scipy", "PIL", "cv2",
        "tkinter", "PyQt5", "wx", "gi",
        "notebook", "IPython", "jupyter",
        "test", "unittest", "pytest",
        "sphinx", "docutils",
        # Exclude the openai voice/numpy helper that triggers the numpy warning
        "openai.helpers",
        "openai._extras",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# ── Single-file executable ────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NEXUS_IDE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress with UPX if available (smaller file)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # keep console window — it shows server logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
