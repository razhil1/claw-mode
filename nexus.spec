# -*- mode: python ; coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────────────────────
#  NEXUS IDE — PyInstaller Build Specification
#  Build command:  pyinstaller nexus.spec
# ─────────────────────────────────────────────────────────────────────────────

import os
from pathlib import Path

ROOT = Path(SPECPATH)  # directory containing this .spec file

# ── Data files to bundle ──────────────────────────────────────────────────────
# Format: (source_path, destination_folder_inside_bundle)
datas = [
    # Flask templates and static assets
    (str(ROOT / "templates"),     "templates"),
    (str(ROOT / "static"),        "static"),
    # Source package
    (str(ROOT / "src"),           "src"),
    # Agent workspace skeleton (creates the folder; actual files stay outside)
    (str(ROOT / "agent_workspace"), "agent_workspace"),
    # Top-level Python files the app needs
    (str(ROOT / "app.py"),        "."),
    (str(ROOT / "models.py"),     "."),
    (str(ROOT / "main.py"),       "."),
]

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
    # psycopg2 (optional DB panel)
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
from PyInstaller.utils.hooks import collect_all, collect_data_files

binaries  = []
all_datas = list(datas)

for pkg in ("tiktoken", "openai", "certifi"):
    pkg_datas, pkg_binaries, pkg_hiddens = collect_all(pkg)
    all_datas      += pkg_datas
    binaries       += pkg_binaries
    hidden_imports += pkg_hiddens

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
        # Things we definitely do not need — trim the bundle size
        "matplotlib", "numpy", "pandas", "scipy", "PIL", "cv2",
        "tkinter", "PyQt5", "wx", "gi",
        "notebook", "IPython", "jupyter",
        "test", "unittest", "pytest",
        "sphinx", "docutils",
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
    # Windows-only: set icon if icon file exists
    icon=None,          # replace with "static/favicon.ico" if you have one
)
