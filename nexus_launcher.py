"""
NEXUS IDE — Standalone Launcher
================================
This is the entry point compiled by PyInstaller into NEXUS_IDE.exe (Windows)
or NEXUS_IDE (Mac/Linux).

What it does:
  1. Sets up all paths so the bundled app can find its files
  2. Generates a secure session secret
  3. Starts the Flask server in a background thread
  4. Waits until the server is accepting connections
  5. Opens http://localhost:5000 in the user's default browser
  6. Keeps running until the user closes the terminal window

No Python installation is required on the target machine.
"""

import os
import sys
import secrets
import threading
import time
import webbrowser
import socket

# ── PyInstaller path fix ───────────────────────────────────────────────────────
# When frozen by PyInstaller, __file__ is inside a temp folder (_MEIPASS).
# We must redirect all relative-path lookups to the folder next to the .exe.
if getattr(sys, "frozen", False):
    # Running as compiled .exe — base dir is the folder containing the executable
    BASE_DIR = os.path.dirname(sys.executable)
    # Also add the PyInstaller bundle path so internal imports work
    BUNDLE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
    sys.path.insert(0, BUNDLE_DIR)
else:
    # Running as plain Python script (development mode)
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

os.chdir(BASE_DIR)

# ── Environment setup ─────────────────────────────────────────────────────────
if not os.environ.get("SESSION_SECRET"):
    os.environ["SESSION_SECRET"] = secrets.token_hex(32)

# Ensure agent_workspace exists next to the .exe
os.makedirs(os.path.join(BASE_DIR, "agent_workspace"), exist_ok=True)

PORT = int(os.environ.get("NEXUS_PORT", "5000"))


# ── Wait for port to open ─────────────────────────────────────────────────────
def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ── Flask server thread ───────────────────────────────────────────────────────
def _run_server() -> None:
    # Import here so the path fix above takes effect first
    from app import app  # noqa: F401
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def main() -> None:
    print()
    print("  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗")
    print("  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝")
    print("  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗")
    print("  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║")
    print("  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║")
    print("  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝")
    print()
    print(f"  NEXUS IDE — Starting on http://localhost:{PORT}")
    print("  Close this window to stop the server.")
    print()

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    print("  Waiting for server to start...", end="", flush=True)
    if _wait_for_server(PORT):
        print(" ready!")
        print(f"  Opening browser → http://localhost:{PORT}")
        print()
        webbrowser.open(f"http://localhost:{PORT}")
    else:
        print(" timed out.")
        print("  Server may still be starting — open http://localhost:5000 manually.")

    # Keep the process alive so the daemon server thread keeps running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  NEXUS IDE stopped.")


if __name__ == "__main__":
    main()
