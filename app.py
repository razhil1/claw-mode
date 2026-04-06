"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         Claw IDE — Flask Backend v5.0                                       ║
║                                                                              ║
║  UPGRADES OVER v4                                                            ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  • Session registry with metadata, TTL eviction, and per-session stats      ║
║  • Rollback API — undo all file changes from any past session               ║
║  • Git API — status, diff, log, commit, checkout, branch management         ║
║  • File search API — regex across the entire workspace                      ║
║  • Atlas & memory read/write endpoints                                      ║
║  • Agent health/status endpoint with live metrics                           ║
║  • Rate limiter — per-IP sliding window, configurable                       ║
║  • Structured error responses with error codes                              ║
║  • Heartbeat SSE with per-event sequence numbers                            ║
║  • Workspace diff endpoint — what changed since last sync                   ║
║  • Multi-file batch read endpoint                                           ║
║  • File rename/move endpoint                                                ║
║  • Workspace reset endpoint with confirmation token                         ║
║  • Request logging middleware with timing                                   ║
║  • CORS support with configurable origins                                   ║
║  • Graceful shutdown hook — drains active sessions before exit              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shlex
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    stream_with_context,
)

from src.agent import ClawAgent
from src.ultraworker import UltraWorker
from src.toolbox import (
    tool_bash_run,
    tool_file_delete,
    tool_file_edit,
    tool_file_read,
)
from src.llm import (
    DEFAULT_MODEL,
    get_all_models,
    get_nvidia_key,
    set_runtime_key,
    validate_key,
    LLMClient,  # Added for proxy bridge
)


# ═══════════════════════════════════════════════════════════════════════════════
# APP BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("claw.app")

if not os.environ.get("NVIDIA_API_KEY"):
    log.warning("No NVIDIA_API_KEY set. AI features require a valid key.")

WORKSPACE_DIR = Path(os.path.abspath(
    os.environ.get("CLAW_WORKSPACE", "agent_workspace")
))
os.environ["CLAW_WORKSPACE"] = str(WORKSPACE_DIR)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

ACTIVE_MODEL  = os.environ.get("CLAW_MODEL", DEFAULT_MODEL)
ULTRA_MODE    = os.environ.get("CLAW_ULTRA", "0") == "1"
CORS_ORIGINS  = os.environ.get("CLAW_CORS", "*")

# Directories to ignore in file listings and workspace maps.
# IDE source directories are always excluded so they never appear in the explorer
# even if CLAW_WORKSPACE is accidentally set to the project root.
_SKIP_DIRS = {"node_modules", "__pycache__", ".git", "venv", ".venv",
               ".next", "dist", "build", ".cache", ".mypy_cache", ".ruff_cache",
               "src", "static", "templates", "tests", "assets", ".local",
               ".uv", "__snapshots__"}


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _RateLimiter:
    max_calls: int   = 60    # requests per window
    window_s:  float = 60.0  # window in seconds

    def __post_init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._buckets[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.time()
        with self._lock:
            q = self._buckets[key]
            recent = sum(1 for t in q if now - t <= self.window_s)
            return max(0, self.max_calls - recent)


_limiter = _RateLimiter(
    max_calls=int(os.environ.get("CLAW_RATE_LIMIT", "120")),
    window_s=float(os.environ.get("CLAW_RATE_WINDOW", "60")),
)


def _rate_limit_key() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

SESSION_TTL_S = float(os.environ.get("CLAW_SESSION_TTL", str(3 * 3600)))  # 3 h default


@dataclass
class SessionMeta:
    session_id:  str
    created_at:  float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    total_turns: int   = 0
    total_tools: int   = 0
    is_ultra:    bool  = False
    model:       str   = DEFAULT_MODEL

    def touch(self) -> None:
        self.last_active = time.time()

    def age_s(self) -> float:
        return time.time() - self.created_at

    def idle_s(self) -> float:
        return time.time() - self.last_active

    def to_dict(self) -> dict:
        return {
            "session_id":  self.session_id,
            "created_at":  self.created_at,
            "last_active": self.last_active,
            "total_turns": self.total_turns,
            "total_tools": self.total_tools,
            "is_ultra":    self.is_ultra,
            "model":       self.model,
            "age_s":       round(self.age_s()),
            "idle_s":      round(self.idle_s()),
        }


class SessionRegistry:
    def __init__(self) -> None:
        self._lock     = threading.Lock()
        self._agents:  dict[str, ClawAgent | UltraWorker] = {}
        self._meta:    dict[str, SessionMeta]             = {}
        self._evict_thread = threading.Thread(
            target=self._evict_loop, daemon=True, name="session-evict"
        )
        self._evict_thread.start()

    # ── public ────────────────────────────────────────────────────────────────

    def get_or_create(self, session_id: str, ultra: bool, model: str) -> ClawAgent | UltraWorker:
        with self._lock:
            if session_id in self._agents:
                agent = self._agents[session_id]
                agent.model = model
                self._meta[session_id].touch()
                self._meta[session_id].model = model
                return agent

            agent = UltraWorker(model=model) if ultra else ClawAgent(model=model)
            self._agents[session_id] = agent
            self._meta[session_id]   = SessionMeta(
                session_id=session_id, is_ultra=ultra, model=model
            )
            log.info("Session created: %s (ultra=%s, model=%s)", session_id, ultra, model)
            return agent

    def get(self, session_id: str) -> ClawAgent | UltraWorker | None:
        return self._agents.get(session_id)

    def meta(self, session_id: str) -> SessionMeta | None:
        return self._meta.get(session_id)

    def record_turn(self, session_id: str, tools_used: int = 0) -> None:
        with self._lock:
            if m := self._meta.get(session_id):
                m.total_turns += 1
                m.total_tools += tools_used
                m.touch()

    def clear(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._agents:
                self._agents[session_id].clear_history()
                if m := self._meta.get(session_id):
                    m.touch()
                return True
            return False

    def destroy(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._agents:
                try:
                    agent = self._agents.pop(session_id)
                    if hasattr(agent, "shutdown"):
                        agent.shutdown()
                except Exception:
                    pass
                self._meta.pop(session_id, None)
                log.info("Session destroyed: %s", session_id)
                return True
            return False

    def all_meta(self) -> list[dict]:
        with self._lock:
            return [m.to_dict() for m in self._meta.values()]

    def count(self) -> int:
        return len(self._agents)

    def rollback(self, session_id: str) -> list[str]:
        agent = self._agents.get(session_id)
        if agent and hasattr(agent, "rollback_last_session"):
            return agent.rollback_last_session()
        return ["No rollback available for this session."]

    # ── eviction ──────────────────────────────────────────────────────────────

    def _evict_loop(self) -> None:
        while True:
            time.sleep(300)
            self._evict_stale()

    def _evict_stale(self) -> None:
        with self._lock:
            stale = [
                sid for sid, m in self._meta.items()
                if m.idle_s() > SESSION_TTL_S
            ]
        for sid in stale:
            self.destroy(sid)
            log.info("Evicted idle session: %s", sid)


_registry = SessionRegistry()


# ═══════════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

@app.before_request
def _before() -> Response | None:
    request._start_time = time.perf_counter()  # type: ignore[attr-defined]

    # CORS preflight
    if request.method == "OPTIONS":
        return _cors(Response())

    # Rate limiting (skip static/workspace routes)
    if request.path.startswith("/api/"):
        key = _rate_limit_key()
        if not _limiter.allow(key):
            return _error("Rate limit exceeded. Slow down.", 429, "RATE_LIMITED")

    return None


@app.after_request
def _after(response: Response) -> Response:
    elapsed = round((time.perf_counter() - getattr(request, "_start_time", 0)) * 1000, 1)
    response.headers["X-Response-Time"] = f"{elapsed}ms"
    response.headers["X-Claw-Version"]  = "5.0"
    return _cors(response)


def _cors(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"]  = CORS_ORIGINS
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Session-Id"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _ok(data: dict | None = None, **kwargs) -> Response:
    payload = {"ok": True, **(data or {}), **kwargs}
    return jsonify(payload)


def _error(message: str, status: int = 400, code: str = "ERROR") -> Response:
    return jsonify({"ok": False, "error": code, "message": message}), status


def _safe_path(rel: str) -> Path:
    """Resolve a relative path inside the workspace, raising on traversal."""
    target = (WORKSPACE_DIR / rel).resolve()
    if not str(target).startswith(str(WORKSPACE_DIR)):
        raise ValueError(f"Path traversal rejected: {rel!r}")
    return target


def _walk_workspace() -> list[dict]:
    entries: list[dict] = []
    for root, dirs, filenames in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in filenames:
            if name.startswith((".memory", ".atlas")):
                continue
            fp  = Path(root) / name
            rel = fp.relative_to(WORKSPACE_DIR).as_posix()
            try:
                stat = fp.stat()
                entries.append({
                    "path":     rel,
                    "size":     stat.st_size,
                    "modified": round(stat.st_mtime),
                })
            except OSError:
                entries.append({"path": rel, "size": 0, "modified": 0})
    return sorted(entries, key=lambda e: e["path"])


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — STATIC / UI
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index() -> Response:
    return render_template("index.html")


@app.route("/workspace/")
@app.route("/workspace/<path:filename>")
def serve_workspace(filename: str = "index.html") -> Response:
    target = (WORKSPACE_DIR / filename).resolve()
    # Prevent path traversal
    try:
        target.relative_to(WORKSPACE_DIR)
    except ValueError:
        return _error("Access denied", 403, "TRAVERSAL")

    if not target.exists():
        import html as _html
        # Escape filename to prevent reflected XSS in the placeholder response
        safe_name = _html.escape(filename, quote=True)
        placeholder = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Preview &#8212; {safe_name}</title>
<style>
  body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;
        min-height:100vh;margin:0;background:#0d1117;color:#c9d1d9;flex-direction:column;gap:16px;}}
  .icon{{font-size:3rem;}}
  h2{{margin:0;color:#e6edf3;}}
  p{{margin:0;color:#8b949e;font-size:.9rem;}}
  code{{background:#161b22;padding:2px 8px;border-radius:4px;font-size:.85rem;}}
</style></head>
<body>
  <div class="icon">&#128196;</div>
  <h2>No preview available</h2>
  <p>File <code>{safe_name}</code> does not exist in the workspace yet.</p>
  <p>Ask the agent to create it, or open a different file.</p>
</body></html>"""
        return Response(placeholder, status=200, mimetype="text/html")

    return send_from_directory(str(WORKSPACE_DIR), filename)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — HEALTH & METRICS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/health")
def health() -> Response:
    return _ok(
        status="ok",
        ultra_mode=ULTRA_MODE,
        active_model=ACTIVE_MODEL,
        active_sessions=_registry.count(),
        workspace=str(WORKSPACE_DIR),
        version="5.0",
    )


@app.route("/api/metrics")
def metrics() -> Response:
    total_size  = 0
    file_count  = 0
    for root, dirs, filenames in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in filenames:
            fp = Path(root) / name
            try:
                total_size += fp.stat().st_size
                file_count += 1
            except OSError:
                pass
    return _ok(
        sessions=_registry.all_meta(),
        workspace={
            "file_count":    file_count,
            "total_size":    total_size,
            "total_size_kb": round(total_size / 1024, 1),
        },
        rate_limiter={
            "remaining": _limiter.remaining(_rate_limit_key()),
            "max":       _limiter.max_calls,
            "window_s":  _limiter.window_s,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — FILES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/files")
def list_files() -> Response:
    return _ok(files=_walk_workspace())


@app.route("/api/files/search")
def search_files() -> Response:
    """Regex search across all workspace files."""
    import re
    pattern = request.args.get("q", "").strip()
    if not pattern:
        return _error("Missing query param 'q'")
    try:
        rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        return _error(f"Invalid regex: {exc}", code="INVALID_REGEX")

    results: list[dict] = []
    for entry in _walk_workspace():
        fp = WORKSPACE_DIR / entry["path"]
        try:
            text  = fp.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            hits: list[dict] = []
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    hits.append({"line": i, "text": line.rstrip()})
            if hits:
                results.append({"path": entry["path"], "matches": hits})
        except Exception:
            pass

    return _ok(query=pattern, results=results, total_files=len(results))


@app.route("/api/files/batch", methods=["POST"])
def batch_read() -> Response:
    """Read multiple files in one request."""
    data  = request.json or {}
    paths = data.get("paths", [])
    if not isinstance(paths, list) or len(paths) > 50:
        return _error("'paths' must be a list of up to 50 paths")

    results: dict[str, Any] = {}
    for rel in paths:
        try:
            content = (WORKSPACE_DIR / rel).read_text(encoding="utf-8", errors="replace")
            results[rel] = {"content": content, "ok": True}
        except Exception as exc:
            results[rel] = {"content": None, "ok": False, "error": str(exc)}
    return _ok(files=results)


@app.route("/api/file/<path:filepath>")
def read_file(filepath: str) -> Response:
    content = tool_file_read(filepath)
    if isinstance(content, str) and content.startswith("Error: File") and "not found" in content:
        return _error(content, 404, "NOT_FOUND")
    return _ok(path=filepath, content=content)


@app.route("/api/file/<path:filepath>", methods=["PUT"])
def write_file(filepath: str) -> Response:
    data    = request.json or {}
    content = data.get("content", "")
    result  = tool_file_edit(filepath, content)
    return _ok(result=result)


@app.route("/api/file/<path:filepath>", methods=["DELETE"])
def delete_file(filepath: str) -> Response:
    result = tool_file_delete(filepath)
    return _ok(result=result)


@app.route("/api/file/new", methods=["POST"])
def new_file() -> Response:
    data     = request.json or {}
    filepath = data.get("path", "").strip().lstrip("/")
    if not filepath:
        return _error("'path' is required")
    content = data.get("content", "")
    result  = tool_file_edit(filepath, content)
    return _ok(result=result, path=filepath)


@app.route("/api/file/rename", methods=["POST"])
def rename_file() -> Response:
    data     = request.json or {}
    src_rel  = data.get("from", "").strip()
    dst_rel  = data.get("to", "").strip()
    if not src_rel or not dst_rel:
        return _error("'from' and 'to' are required")
    try:
        src = _safe_path(src_rel)
        dst = _safe_path(dst_rel)
        if not src.exists():
            return _error(f"Source not found: {src_rel}", 404, "NOT_FOUND")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return _ok(moved_from=src_rel, moved_to=dst_rel)
    except ValueError as exc:
        return _error(str(exc), 400, "TRAVERSAL")
    except Exception as exc:
        return _error(str(exc), 500, "RENAME_FAILED")


@app.route("/api/upload", methods=["POST"])
def upload_files() -> Response:
    from src.toolbox import enforce_safe_path
    uploaded, errors = [], []
    for key in request.files:
        f        = request.files[key]
        filename = (f.filename or "").lstrip("/")
        if not filename:
            continue
        try:
            safe = enforce_safe_path(filename)
            safe.parent.mkdir(parents=True, exist_ok=True)
            f.save(str(safe))
            uploaded.append(filename)
        except Exception as exc:
            errors.append({"file": filename, "error": str(exc)})
    return _ok(uploaded=uploaded, errors=errors)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — WORKSPACE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/workspace/stats")
def workspace_stats() -> Response:
    total_size = file_count = 0
    for root, dirs, filenames in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in filenames:
            fp = Path(root) / name
            try:
                total_size += fp.stat().st_size
                file_count += 1
            except OSError:
                pass
    return _ok(
        file_count=file_count,
        total_size=total_size,
        total_size_kb=round(total_size / 1024, 1),
    )


@app.route("/api/workspace/diff")
def workspace_diff() -> Response:
    """Return files modified in the last N seconds (default 3600)."""
    since = float(request.args.get("since", time.time() - 3600))
    changed = [
        e for e in _walk_workspace()
        if e["modified"] >= since
    ]
    return _ok(since=since, changed=changed)


@app.route("/api/workspace/download")
def download_workspace() -> Response:
    try:
        from src.toolbox import tool_workspace_zip
        data = tool_workspace_zip()
        return send_file(
            io.BytesIO(data),
            mimetype="application/zip",
            as_attachment=True,
            download_name="agent_workspace.zip",
        )
    except Exception as exc:
        return _error(str(exc), 500, "ZIP_FAILED")


@app.route("/api/workspace/upload", methods=["POST"])
def upload_workspace() -> Response:
    if "file" not in request.files:
        return _error("No file attached", code="NO_FILE")
    f = request.files["file"]
    if not f.filename:
        return _error("Empty filename", code="NO_FILE")
    try:
        from src.toolbox import tool_workspace_unzip
        msg = tool_workspace_unzip(f.read())
        return _ok(message=msg)
    except Exception as exc:
        return _error(str(exc), 500, "UNZIP_FAILED")


@app.route("/api/workspace/open", methods=["POST"])
def open_workspace() -> Response:
    global WORKSPACE_DIR
    data = request.json or {}
    new_path = data.get("path", "").strip()
    if not new_path:
        return _error("'path' is required")
    try:
        p = Path(os.path.abspath(new_path))
        if not p.exists() or not p.is_dir():
            return _error(f"Path does not exist or is not a directory: {new_path}")
        WORKSPACE_DIR = p
        os.environ["CLAW_WORKSPACE"] = str(p)
        log.info("Workspace changed to: %s", WORKSPACE_DIR)
        return _ok(message=f"Workspace opened: {WORKSPACE_DIR}", path=str(WORKSPACE_DIR))
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/workspace/reset", methods=["POST"])
def reset_workspace() -> Response:
    """
    Wipe and recreate the workspace. Requires a confirmation token.
    POST body: { "confirm": "RESET_WORKSPACE" }
    """
    data = request.json or {}
    if data.get("confirm") != "RESET_WORKSPACE":
        return _error(
            "Send { \"confirm\": \"RESET_WORKSPACE\" } to confirm.",
            code="CONFIRM_REQUIRED",
        )
    try:
        shutil.rmtree(WORKSPACE_DIR)
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        log.warning("Workspace reset by %s", _rate_limit_key())
        return _ok(message="Workspace wiped and recreated.")
    except Exception as exc:
        return _error(str(exc), 500, "RESET_FAILED")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — GIT
# ═══════════════════════════════════════════════════════════════════════════════

def _git(cmd: str) -> str:
    return tool_bash_run(f"git -C {WORKSPACE_DIR} {cmd}")


@app.route("/api/git/status")
def git_status() -> Response:
    return _ok(output=_git("status --short"))


@app.route("/api/git/log")
def git_log() -> Response:
    n = min(int(request.args.get("n", 20)), 100)
    return _ok(output=_git(f"log --oneline -n {n}"))


@app.route("/api/git/diff")
def git_diff() -> Response:
    filepath = request.args.get("path", "")
    cmd      = f"diff HEAD -- {filepath}" if filepath else "diff HEAD"
    return _ok(output=_git(cmd))


@app.route("/api/git/branches")
def git_branches() -> Response:
    return _ok(output=_git("branch -a"))


@app.route("/api/git/commit", methods=["POST"])
def git_commit() -> Response:
    data    = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return _error("'message' is required", code="NO_MESSAGE")
    out = _git("add -A") + "\n" + _git(f'commit -m "{message}"')
    return _ok(output=out)


@app.route("/api/git/checkout", methods=["POST"])
def git_checkout() -> Response:
    data   = request.json or {}
    branch = data.get("branch", "").strip()
    create = data.get("create", False)
    if not branch:
        return _error("'branch' is required", code="NO_BRANCH")
    flag = "-b " if create else ""
    return _ok(output=_git(f"checkout {flag}{branch}"))


@app.route("/api/git/init", methods=["POST"])
def git_init() -> Response:
    return _ok(output=_git("init"))


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — MEMORY & ATLAS
# ═══════════════════════════════════════════════════════════════════════════════

def _read_md(filename: str) -> str:
    p = WORKSPACE_DIR / filename
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _write_md(filename: str, content: str) -> None:
    (WORKSPACE_DIR / filename).write_text(content, encoding="utf-8")


@app.route("/api/session/memory")
def get_memory() -> Response:
    return _ok(memory=_read_md(".memory.md") or "No memories yet.")


@app.route("/api/session/memory", methods=["PUT"])
def set_memory() -> Response:
    data = request.json or {}
    _write_md(".memory.md", data.get("content", ""))
    return _ok(message="Memory updated.")


@app.route("/api/session/atlas")
def get_atlas() -> Response:
    return _ok(atlas=_read_md(".atlas.md") or "No atlas yet.")


@app.route("/api/session/atlas", methods=["PUT"])
def set_atlas() -> Response:
    data = request.json or {}
    _write_md(".atlas.md", data.get("content", ""))
    return _ok(message="Atlas updated.")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — MODELS & MODE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/models")
def get_models() -> Response:
    all_models = get_all_models()
    result = [
        {
            "id":          mid,
            "label":       info["label"],
            "short":       info.get("short", ""),
            "description": info.get("description", ""),
            "context":     info.get("context", 4096),
            "tier":        info.get("tier", "free"),
            "provider":    info.get("provider", "nvidia"),
            "role":        info.get("role", "balanced"),
            "emoji":       info.get("emoji", "⚡"),
            "price_note":  info.get("price_note", "Free"),
            "active":      mid == ACTIVE_MODEL,
        }
        for mid, info in all_models.items()
    ]
    return _ok(models=result, active=ACTIVE_MODEL)


@app.route("/api/model", methods=["POST"])
def set_model() -> Response:
    global ACTIVE_MODEL
    data     = request.json or {}
    model_id = data.get("model", "")
    if model_id not in get_all_models():
        return _error(f"Unknown model: {model_id!r}", code="UNKNOWN_MODEL")
    ACTIVE_MODEL = model_id
    os.environ["CLAW_MODEL"] = model_id
    return _ok(active=ACTIVE_MODEL)


@app.route("/api/mode")
def get_mode() -> Response:
    return _ok(ultra=ULTRA_MODE, mode="ultra" if ULTRA_MODE else "standard")


@app.route("/api/mode", methods=["POST"])
def set_mode() -> Response:
    global ULTRA_MODE
    data      = request.json or {}
    mode      = data.get("mode", "standard")
    ULTRA_MODE = mode == "ultra"
    os.environ["CLAW_ULTRA"] = "1" if ULTRA_MODE else "0"
    return _ok(ultra=ULTRA_MODE, mode=mode)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — TERMINAL
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/terminal", methods=["POST"])
def terminal() -> Response:
    data    = request.json or {}
    command = data.get("command", "").strip()
    if not command:
        return _error("'command' is required", code="NO_COMMAND")
    output = tool_bash_run(command)
    return _ok(output=output)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/session/new", methods=["POST"])
def new_session() -> Response:
    sid = uuid.uuid4().hex
    _registry.get_or_create(sid, ultra=ULTRA_MODE, model=ACTIVE_MODEL)
    return _ok(session_id=sid)


@app.route("/api/session/<session_id>")
def session_info(session_id: str) -> Response:
    m = _registry.meta(session_id)
    if not m:
        return _error("Session not found", 404, "NOT_FOUND")
    return _ok(session=m.to_dict())


@app.route("/api/session/<session_id>/clear", methods=["POST"])
def clear_session(session_id: str) -> Response:
    data = request.get_json(silent=True) or {}
    ok   = _registry.clear(session_id)
    if data.get("clear_memory"):
        mem = WORKSPACE_DIR / ".memory.md"
        mem.unlink(missing_ok=True)
    return _ok(cleared=ok)


@app.route("/api/session/<session_id>/destroy", methods=["POST"])
def destroy_session(session_id: str) -> Response:
    ok = _registry.destroy(session_id)
    return _ok(destroyed=ok)


@app.route("/api/session/<session_id>/rollback", methods=["POST"])
def rollback_session(session_id: str) -> Response:
    """Undo all file changes made during the most recent run for this session."""
    results = _registry.rollback(session_id)
    return _ok(results=results)


@app.route("/api/sessions")
def list_sessions() -> Response:
    return _ok(sessions=_registry.all_meta(), count=_registry.count())


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — STOP AGENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/chat/stop", methods=["POST"])
def stop_chat() -> Response:
    data       = request.json or {}
    session_id = data.get("session_id", "default")
    agent      = _registry.get(session_id)
    if agent:
        agent.request_stop()
        return _ok(message="Stop signal sent.")
    return _error("Session not found", 404, "NOT_FOUND")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — STREAMING CHAT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/chat/stream", methods=["POST"])
def chat_stream() -> Response:
    data       = request.json or {}
    prompt     = data.get("prompt", "").strip()
    session_id = data.get("session_id", "default")
    mode_override = data.get("mode", "").strip()

    if not prompt:
        return _error("'prompt' is required", code="NO_PROMPT")

    agent = _registry.get_or_create(session_id, ultra=ULTRA_MODE, model=ACTIVE_MODEL)

    event_queue: list[dict] = []
    done_event  = threading.Event()
    error_holder: list[str] = []
    seq = [0]  # sequence counter shared via list for mutation in closure

    def run_agent() -> None:
        try:
            # Pass mode override to the active agent (ClawAgent and UltraWorker both support it)
            stream_kwargs = {}
            if hasattr(agent, "run_streaming") and mode_override:
                import inspect
                sig = inspect.signature(agent.run_streaming)
                if "mode_override" in sig.parameters:
                    stream_kwargs["mode_override"] = mode_override
            for event in agent.run_streaming(prompt, **stream_kwargs):
                seq[0] += 1
                event["seq"] = seq[0]
                event_queue.append(event)
                if event.get("type") == "done":
                    _registry.record_turn(
                        session_id,
                        tools_used=event.get("tools_used", 0),
                    )
        except Exception as exc:
            log.exception("Agent error in session %s", session_id)
            error_holder.append(str(exc))
        finally:
            done_event.set()

    threading.Thread(target=run_agent, daemon=True, name=f"agent-{session_id[:8]}").start()

    def generate():
        sent = 0
        while not done_event.is_set() or sent < len(event_queue):
            while sent < len(event_queue):
                yield f"data: {json.dumps(event_queue[sent])}\n\n"
                sent += 1
            if not done_event.is_set():
                yield ": heartbeat\n\n"
                done_event.wait(timeout=0.4)
        if error_holder:
            err_evt = {"type": "error", "message": error_holder[0], "seq": seq[0] + 1}
            yield f"data: {json.dumps(err_evt)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — MULTI-AGENT SWARM
# ═══════════════════════════════════════════════════════════════════════════════

_active_orchestrators: dict[str, "UltraMultiAgentOrchestrator"] = {}

@app.route("/api/multi-agent/stream", methods=["POST"])
def multi_agent_stream() -> Response:
    """
    Multi-agent streaming endpoint.
    Decomposes the prompt into sub-tasks and runs specialist agents in parallel.
    """
    from src.multi_agent import UltraMultiAgentOrchestrator, _INTERNAL_TOOLS_AVAILABLE

    data       = request.json or {}
    prompt     = data.get("prompt", "").strip()
    session_id = data.get("session_id", "default")
    max_agents = min(int(data.get("max_agents", 3)), 5)

    if not prompt:
        return _error("'prompt' is required", code="NO_PROMPT")

    # Hard-fail early if toolbox import failed — emit blocking error event to UI
    if not _INTERNAL_TOOLS_AVAILABLE:
        def _error_stream():
            msg = (
                "Swarm mode is unavailable: toolbox or agent modules failed to import. "
                "Check the server logs for the ImportError details. "
                "File-system tools (read/write/edit) will not work until this is resolved."
            )
            yield f"data: {json.dumps({'type': 'error', 'message': msg, 'seq': 1})}\n\n"
            yield "data: [DONE]\n\n"
        return Response(
            stream_with_context(_error_stream()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    orch = UltraMultiAgentOrchestrator(max_parallel=max_agents)
    _active_orchestrators[session_id] = orch

    event_queue: list[dict] = []
    done_event  = threading.Event()
    error_holder: list[str] = []
    seq = [0]

    def run_swarm() -> None:
        try:
            for event in orch.run(prompt):
                seq[0] += 1
                event["seq"] = seq[0]
                event_queue.append(event)
        except Exception as exc:
            log.exception("Multi-agent error in session %s", session_id)
            error_holder.append(str(exc))
        finally:
            _active_orchestrators.pop(session_id, None)
            done_event.set()

    threading.Thread(target=run_swarm, daemon=True,
                     name=f"swarm-{session_id[:8]}").start()

    def generate():
        sent = 0
        while not done_event.is_set() or sent < len(event_queue):
            while sent < len(event_queue):
                yield f"data: {json.dumps(event_queue[sent])}\n\n"
                sent += 1
            if not done_event.is_set():
                yield ": heartbeat\n\n"
                done_event.wait(timeout=0.3)
        if error_holder:
            yield f"data: {json.dumps({'type': 'error', 'message': error_holder[0], 'seq': seq[0] + 1})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.route("/api/multi-agent/stop", methods=["POST"])
def multi_agent_stop() -> Response:
    data       = request.json or {}
    session_id = data.get("session_id", "default")
    orch = _active_orchestrators.get(session_id)
    if orch:
        orch.request_stop()
        return _ok(message="Stop signal sent to all agents.")
    return _error("No active multi-agent session", 404, "NOT_FOUND")


@app.route("/api/multi-agent/roles")
def multi_agent_roles() -> Response:
    from src.multi_agent import AGENT_ROLES
    roles = [
        {
            "id": k,
            "name": v["name"],
            "emoji": v["emoji"],
            "color": v["color"],
            "description": v["description"],
        }
        for k, v in AGENT_ROLES.items()
    ]
    return _ok(roles=roles)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — BRAIN SYNC / ATLAS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/system/upgrade", methods=["POST"])
def upgrade_system() -> Response:
    data       = request.json or {}
    session_id = data.get("session_id", "default")
    agent      = _registry.get_or_create(session_id, ultra=ULTRA_MODE, model=ACTIVE_MODEL)

    summary_prompt = (
        "Perform a deep architectural analysis of the workspace. "
        "Create or update `.atlas.md` with: project purpose, file layout, "
        "key modules, data flow, and any known issues. "
        "This is your long-term architectural memory."
    )

    result_text = ""
    try:
        for event in agent.run_streaming(summary_prompt):
            if event.get("type") == "token":
                result_text += event["text"]
            elif event.get("type") == "error":
                return _error(event["message"], 500, "AGENT_ERROR")
        return _ok(message="Project Atlas updated. Brain synced.", excerpt=result_text[:400])
    except Exception as exc:
        return _error(str(exc), 500, "UPGRADE_FAILED")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — KEY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/settings/key-status")
def key_status() -> Response:
    key = get_nvidia_key()
    return _ok(nvidia={
        "configured": bool(key),
        "prefix":     key[:14] + "…" if key else "",
    })


@app.route("/api/settings/validate-key", methods=["POST"])
def api_validate_key() -> Response:
    data = request.json or {}
    key  = data.get("key", "").strip()
    if not key:
        return _error("'key' is required", code="NO_KEY")
    return jsonify(validate_key(key))


@app.route("/api/settings/set-key", methods=["POST"])
def api_set_key() -> Response:
    data = request.json or {}
    key  = data.get("key", "").strip()
    if not key:
        return _error("'key' is required", code="NO_KEY")
    set_runtime_key(key)
    os.environ["NVIDIA_API_KEY"] = key
    return _ok(
        message=(
            "NVIDIA key saved and will persist across restarts. "
            "You can also set NVIDIA_API_KEY as a Replit Secret (Tools → Secrets) "
            "to override it at the environment level."
        )
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — DEPLOY PANEL
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/deploy/status")
def deploy_status() -> Response:
    """Return current deployment / git-remote status."""
    remote = tool_bash_run("git remote -v 2>&1 || echo NO_GIT")
    branch = tool_bash_run("git rev-parse --abbrev-ref HEAD 2>&1 || echo unknown")
    last   = tool_bash_run("git log --oneline -3 2>&1 || echo NO_COMMITS")
    dirty  = tool_bash_run("git status --short 2>&1")
    return _ok(
        remote=remote.strip(),
        branch=branch.strip(),
        recent_commits=last.strip(),
        dirty_files=dirty.strip(),
    )


def _run_cmd_checked(cmd: str) -> tuple[str, bool]:
    """Run a shell command; return (output, success). success=False on error markers."""
    out = tool_bash_run(cmd)
    failed = (
        out.strip().startswith("[Command exited with code ")
        or "error:" in out.lower()
        or "fatal:" in out.lower()
        or "Permission denied" in out
        or "rejected" in out.lower()
    )
    return out, not failed


@app.route("/api/deploy/push", methods=["POST"])
def deploy_push() -> Response:
    """Push current branch to remote origin."""
    data   = request.json or {}
    # Allowlist remote to alphanumeric/dash/dot
    remote_raw = data.get("remote", "origin")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", remote_raw):
        return _error("Invalid remote name", code="INVALID_REMOTE")
    remote = remote_raw

    branch = tool_bash_run("git rev-parse --abbrev-ref HEAD 2>&1").strip()
    commit_msg = data.get("commit_message", "").strip()
    out_lines: list[str] = []

    if commit_msg:
        add_out, add_ok = _run_cmd_checked("git add -A 2>&1")
        out_lines.append(add_out)
        if not add_ok:
            return _error(f"git add failed:\n{add_out}", code="GIT_ADD_FAILED")

        commit_out, commit_ok = _run_cmd_checked(
            f"git commit -m {shlex.quote(commit_msg)} 2>&1"
        )
        out_lines.append(commit_out)
        # "nothing to commit" is not a failure
        if not commit_ok and "nothing to commit" not in commit_out.lower():
            return _error(f"git commit failed:\n{commit_out}", code="GIT_COMMIT_FAILED")

    push_out, push_ok = _run_cmd_checked(
        f"git push {shlex.quote(remote)} {shlex.quote(branch)} 2>&1"
    )
    out_lines.append(push_out)
    if not push_ok:
        return _error(f"git push failed:\n{push_out}", code="GIT_PUSH_FAILED")

    return _ok(output="\n".join(out_lines), branch=branch, remote=remote)


@app.route("/api/deploy/netlify", methods=["POST"])
def deploy_netlify() -> Response:
    """Deploy using Netlify CLI if available."""
    check = tool_bash_run("which netlify 2>&1 || echo NOT_FOUND")
    if "NOT_FOUND" in check:
        return _error(
            "Netlify CLI not installed. Run: npm i -g netlify-cli",
            code="NETLIFY_NOT_INSTALLED",
        )
    out = tool_bash_run("netlify deploy --prod 2>&1")
    return _ok(output=out)


@app.route("/api/deploy/vercel", methods=["POST"])
def deploy_vercel() -> Response:
    """Deploy using Vercel CLI if available."""
    check = tool_bash_run("which vercel 2>&1 || echo NOT_FOUND")
    if "NOT_FOUND" in check:
        return _error(
            "Vercel CLI not installed. Run: npm i -g vercel",
            code="VERCEL_NOT_INSTALLED",
        )
    out = tool_bash_run("vercel --prod 2>&1")
    return _ok(output=out)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — DOCKER PANEL
# ═══════════════════════════════════════════════════════════════════════════════

def _docker_available() -> bool:
    result = tool_bash_run("docker info --format '{{.ServerVersion}}' 2>&1")
    return "Cannot connect" not in result and "not found" not in result.lower() and result.strip()


@app.route("/api/docker/status")
def docker_status() -> Response:
    """Return Docker daemon status and container list."""
    if not _docker_available():
        return _ok(
            available=False,
            daemon="Docker daemon not running or Docker not installed",
            containers=[],
            images=[],
        )
    containers = tool_bash_run(
        'docker ps -a --format "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}" 2>&1'
    )
    images = tool_bash_run(
        'docker images --format "{{.Repository}}:{{.Tag}}|{{.Size}}|{{.CreatedSince}}" 2>&1'
    )
    info = tool_bash_run("docker info --format '{{.ServerVersion}}' 2>&1")

    container_list = []
    for line in containers.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            container_list.append({
                "name":   parts[0],
                "image":  parts[1],
                "status": parts[2],
                "ports":  parts[3] if len(parts) > 3 else "",
            })

    image_list = []
    for line in images.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            image_list.append({
                "name":    parts[0],
                "size":    parts[1],
                "created": parts[2] if len(parts) > 2 else "",
            })

    return _ok(
        available=True,
        daemon=f"Docker Engine v{info.strip()}",
        containers=container_list,
        images=image_list,
    )


@app.route("/api/docker/build", methods=["POST"])
def docker_build() -> Response:
    """Run docker build in the workspace."""
    data  = request.json or {}
    tag_raw = data.get("tag", "nexus-app:latest").strip()
    ctx_raw = data.get("context", ".").strip()

    # Validate docker tag: alphanumeric plus :/._- only
    if not re.fullmatch(r"[A-Za-z0-9:._/-]{1,128}", tag_raw):
        return _error("Invalid docker tag", code="INVALID_TAG")
    # Validate context path: must be relative and not escape workspace
    if ".." in ctx_raw or ctx_raw.startswith("/"):
        return _error("Invalid build context path", code="INVALID_CTX")

    if not _docker_available():
        return _error("Docker daemon not available", code="DOCKER_UNAVAILABLE")
    out = tool_bash_run(f"docker build -t {shlex.quote(tag_raw)} {shlex.quote(ctx_raw)} 2>&1")
    return _ok(output=out, tag=tag_raw)


@app.route("/api/docker/compose", methods=["POST"])
def docker_compose_up() -> Response:
    """Run docker compose up -d in the workspace."""
    if not _docker_available():
        return _error("Docker daemon not available", code="DOCKER_UNAVAILABLE")
    out = tool_bash_run("docker compose up -d 2>&1 || docker-compose up -d 2>&1")
    return _ok(output=out)


@app.route("/api/docker/stop", methods=["POST"])
def docker_stop() -> Response:
    """Stop a specific container by name."""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return _error("'name' is required", code="NO_NAME")
    # Validate container name: Docker allows alphanumeric, dash, underscore, dot
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
        return _error("Invalid container name", code="INVALID_NAME")
    out = tool_bash_run(f"docker stop {shlex.quote(name)} 2>&1")
    return _ok(output=out, container=name)


@app.route("/api/docker/prune", methods=["POST"])
def docker_prune() -> Response:
    """Prune stopped containers and dangling images."""
    if not _docker_available():
        return _error("Docker daemon not available", code="DOCKER_UNAVAILABLE")
    out = tool_bash_run("docker system prune -f 2>&1")
    return _ok(output=out)


@app.route("/api/docker/logs", methods=["GET"])
def docker_logs() -> Response:
    """Get logs from a container."""
    name  = request.args.get("name", "").strip()
    try:
        lines = max(1, min(int(request.args.get("lines", 100)), 2000))
    except ValueError:
        lines = 100
    if not name:
        return _error("'name' query parameter is required", code="NO_NAME")
    # Validate container name
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
        return _error("Invalid container name", code="INVALID_NAME")
    out = tool_bash_run(f"docker logs --tail {lines} {shlex.quote(name)} 2>&1")
    return _ok(output=out, container=name)


@app.route("/api/docker/exec", methods=["POST"])
def docker_exec() -> Response:
    """Run a command inside a running container (strict allowlist, no shell injection)."""
    import subprocess as _subprocess

    data = request.json or {}
    name = data.get("name", "").strip()
    cmd  = data.get("cmd", "").strip()

    if not name:
        return _error("'name' is required", code="NO_NAME")
    if not cmd:
        return _error("'cmd' is required", code="NO_CMD")

    # Validate container name: Docker allows alphanumeric, dash, underscore, dot
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
        return _error("Invalid container name", code="INVALID_NAME")

    # Strict command allowlist — explicitly excludes shell interpreters (bash/sh)
    # to prevent metacharacter injection.
    _ALLOWED_EXEC_CMDS = {
        "ls", "pwd", "env", "cat", "echo",
        "ps", "df", "du", "whoami", "id",
        "python", "python3", "node", "npm", "pip",
    }
    # Parse the command into tokens (raises ValueError on malformed input)
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        return _error(f"Malformed command: {exc}", code="BAD_CMD")

    if not tokens:
        return _error("Empty command", code="NO_CMD")

    cmd_base = tokens[0]
    if cmd_base not in _ALLOWED_EXEC_CMDS:
        return _error(
            f"Command '{cmd_base}' is not in the exec allowlist. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXEC_CMDS))}",
            code="CMD_NOT_ALLOWED",
        )

    if not _docker_available():
        return _error("Docker daemon not available", code="DOCKER_UNAVAILABLE")

    # Use subprocess arg list — never shell=True so metacharacters can't escape
    full_args = ["docker", "exec", name] + tokens
    try:
        result = _subprocess.run(
            full_args, capture_output=True, text=True, timeout=30
        )
        out = (result.stdout + "\n" + result.stderr).strip()
    except _subprocess.TimeoutExpired:
        out = "Error: exec timed out after 30s"
    except Exception as exc:
        out = f"Error: {exc}"
    return _ok(output=out, container=name, cmd=cmd)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — DATABASE EXPLORER
# ═══════════════════════════════════════════════════════════════════════════════

# In-process database connection state (per-instance, not persisted across restarts)
_db_connections: dict[str, dict] = {}  # conn_id → {type, path, url}
_db_lock = threading.Lock()


def _make_db_id(db_type: str, target: str) -> str:
    import hashlib
    return hashlib.md5(f"{db_type}:{target}".encode()).hexdigest()[:8]


def _get_sqlite_conn(path: str):
    import sqlite3
    from src.toolbox import enforce_safe_path
    full = enforce_safe_path(path)
    return sqlite3.connect(str(full))


@app.route("/api/db/connect", methods=["POST"])
def db_connect() -> Response:
    """Register a database connection (SQLite path or PostgreSQL URL)."""
    data    = request.json or {}
    db_type = data.get("type", "sqlite").lower()
    target  = data.get("path") or data.get("url", "")
    if not target:
        return _error("'path' (SQLite) or 'url' (PostgreSQL) is required", code="NO_TARGET")

    conn_id = _make_db_id(db_type, target)

    if db_type == "sqlite":
        try:
            conn = _get_sqlite_conn(target)
            conn.close()
        except Exception as exc:
            return _error(f"Cannot open SQLite database: {exc}", code="DB_OPEN_FAILED")
        with _db_lock:
            _db_connections[conn_id] = {"type": "sqlite", "path": target, "name": target}
        return _ok(conn_id=conn_id, type="sqlite", name=target)

    if db_type in ("postgres", "postgresql", "pg"):
        try:
            import psycopg2
            conn = psycopg2.connect(target)
            conn.close()
        except ImportError:
            return _error("psycopg2 not installed. Run: pip install psycopg2-binary", code="NO_PSYCOPG2")
        except Exception as exc:
            return _error(f"Cannot connect to PostgreSQL: {exc}", code="DB_CONNECT_FAILED")
        # Mask credentials in the display name (never return raw URL to frontend)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(target)
            display_name = f"pg://{parsed.hostname or '?'}:{parsed.port or 5432}{parsed.path or '/'}"
        except Exception:
            display_name = "postgresql://***"
        with _db_lock:
            _db_connections[conn_id] = {"type": "pg", "url": target, "name": display_name}
        return _ok(conn_id=conn_id, type="pg", name=display_name)

    return _error(f"Unsupported database type: {db_type!r}. Use 'sqlite' or 'postgres'.", code="UNKNOWN_TYPE")


@app.route("/api/db/connections")
def db_connections() -> Response:
    """List active database connections."""
    with _db_lock:
        conns = [
            {"conn_id": cid, "type": c["type"], "name": c.get("name", "")}
            for cid, c in _db_connections.items()
        ]
    return _ok(connections=conns)


@app.route("/api/db/tables")
def db_tables() -> Response:
    """List all tables in the selected connection."""
    conn_id = request.args.get("conn_id", "")
    with _db_lock:
        conn_meta = _db_connections.get(conn_id)

    if not conn_meta:
        # Auto-list SQLite files in workspace if no connection chosen
        from src.toolbox import get_workspace_root
        root = get_workspace_root()
        dbs = [str(p.relative_to(root)) for p in root.rglob("*.db")] + \
              [str(p.relative_to(root)) for p in root.rglob("*.sqlite")]
        return _ok(tables=[], sqlite_files=dbs, message="No active connection. Connect first or choose a .db file.")

    try:
        if conn_meta["type"] == "sqlite":
            import sqlite3
            from src.toolbox import enforce_safe_path
            full = enforce_safe_path(conn_meta["path"])
            conn = sqlite3.connect(str(full))
            cur  = conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
            tables = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
            conn.close()
            return _ok(tables=tables, conn_id=conn_id)

        if conn_meta["type"] == "pg":
            import psycopg2
            conn = psycopg2.connect(conn_meta["url"])
            cur  = conn.cursor()
            cur.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            tables = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
            conn.close()
            return _ok(tables=tables, conn_id=conn_id)

    except Exception as exc:
        return _error(str(exc), 500, "DB_TABLES_FAILED")

    return _error("Unknown database type", code="UNKNOWN_TYPE")


@app.route("/api/db/query", methods=["POST"])
def db_query() -> Response:
    """Execute a SQL query and return rows as JSON."""
    data    = request.json or {}
    conn_id = data.get("conn_id", "")
    sql     = (data.get("query") or data.get("sql", "")).strip()
    if not sql:
        return _error("'query' is required", code="NO_QUERY")

    # Safety: only allow read-only statements in the DB Explorer API.
    # WITH is excluded because writable CTEs (WITH...AS(INSERT/UPDATE)) bypass first-word check.
    # Multi-statement SQL (semicolons) is rejected to block mutation piggyback attacks.
    first_word = sql.split()[0].upper()
    if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "SHOW", "DESCRIBE", "DESC"):
        return _error(
            "Only SELECT / PRAGMA / EXPLAIN / SHOW / DESCRIBE queries are allowed "
            "via the DB Explorer. Use the agent to modify data.",
            code="WRITE_NOT_ALLOWED",
        )
    # Block multi-statement SQL (anything after a semicolon)
    if ";" in sql.rstrip(";"):
        return _error(
            "Multi-statement SQL is not allowed in the DB Explorer.",
            code="MULTI_STATEMENT",
        )

    with _db_lock:
        conn_meta = _db_connections.get(conn_id)

    if not conn_meta:
        return _error("Connection not found. Connect first via POST /api/db/connect.", code="NOT_CONNECTED")

    try:
        if conn_meta["type"] == "sqlite":
            import sqlite3
            from src.toolbox import enforce_safe_path
            full = enforce_safe_path(conn_meta["path"])
            # Open read-only via URI so SQLite enforces the restriction at engine level
            conn = sqlite3.connect(f"file:{full}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur  = conn.execute(sql)
            rows = [dict(r) for r in cur.fetchmany(500)]
            cols = [d[0] for d in cur.description] if cur.description else []
            conn.close()
            return _ok(columns=cols, rows=rows, count=len(rows))

        if conn_meta["type"] == "pg":
            import psycopg2, psycopg2.extras
            conn = psycopg2.connect(conn_meta["url"])
            # Use a read-only transaction so PostgreSQL rejects any writes at engine level
            conn.set_session(readonly=True, autocommit=False)
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchmany(500)]
            cols = [d.name for d in cur.description] if cur.description else []
            conn.rollback()
            conn.close()
            return _ok(columns=cols, rows=rows, count=len(rows))

    except Exception as exc:
        return _error(str(exc), 500, "QUERY_FAILED")

    return _error("Unknown database type", code="UNKNOWN_TYPE")


@app.route("/api/db/schema")
def db_schema() -> Response:
    """Return the schema (CREATE statement) for a specific table."""
    conn_id = request.args.get("conn_id", "")
    table   = request.args.get("table", "").strip()
    if not table:
        return _error("'table' query parameter is required", code="NO_TABLE")

    with _db_lock:
        conn_meta = _db_connections.get(conn_id)
    if not conn_meta:
        return _error("Connection not found.", code="NOT_CONNECTED")

    try:
        if conn_meta["type"] == "sqlite":
            import sqlite3
            from src.toolbox import enforce_safe_path
            full = enforce_safe_path(conn_meta["path"])
            conn = sqlite3.connect(str(full))
            cur  = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = ? AND type IN ('table','view')",
                (table,)
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return _error(f"Table '{table}' not found", 404, "NOT_FOUND")
            return _ok(schema=row[0], table=table)

        if conn_meta["type"] == "pg":
            import psycopg2
            conn = psycopg2.connect(conn_meta["url"])
            cur  = conn.cursor()
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, is_nullable "
                "FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position",
                (table,)
            )
            cols = [{"column": r[0], "type": r[1], "max_len": r[2], "nullable": r[3]} for r in cur.fetchall()]
            conn.close()
            return _ok(columns=cols, table=table)

    except Exception as exc:
        return _error(str(exc), 500, "SCHEMA_FAILED")

    return _error("Unknown database type", code="UNKNOWN_TYPE")


# ═══════════════════════════════════════════════════════════════════════════════
# GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════════

import atexit

@atexit.register
def _shutdown() -> None:
    log.info("Shutting down — draining %d session(s)…", _registry.count())
    for meta in _registry.all_meta():
        agent = _registry.get(meta["session_id"])
        if agent and hasattr(agent, "shutdown"):
            try:
                agent.shutdown()
            except Exception:
                pass
    log.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — ANTHROPIC API BRIDGE (OFFICIAL CLI SUPPORT)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/v1/messages", methods=["POST"])
def anthropic_bridge() -> Response:
    """
    Anthropic-to-NVIDIA API Bridge.
    Allows the official Claude Code CLI to talk to NVIDIA NIM models.
    """
    data = request.json or {}
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    model = data.get("model", "claude-3-5-sonnet-20240620")

    # Map Anthropic messages to OpenAI format
    openai_msgs = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            content = text
        openai_msgs.append({"role": role, "content": content})

    # Use our NVIDIA-backed LLM client
    client = LLMClient(model=ACTIVE_MODEL)
    msg_id = f"msg_nv_{uuid.uuid4().hex[:12]}"

    if not stream:
        resp_text = client.chat(openai_msgs)
        return jsonify({
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": resp_text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        })

    def generate():
        # SSE Events
        yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"
        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
        
        for token in client.chat_stream(openai_msgs):
            if token:
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': token}})}\n\n"

        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    mode_label = "⚡ ULTRA" if ULTRA_MODE else "Standard"
    print("=" * 54)
    print("  Claw IDE — Advanced Edition v5.0")
    print(f"  Mode      : {mode_label}")
    print(f"  Model     : {ACTIVE_MODEL}")
    print(f"  Workspace : {WORKSPACE_DIR}")
    print(f"  Rate limit: {_limiter.max_calls} req / {_limiter.window_s}s")
    print("  URL       : http://localhost:5000")
    print("=" * 54)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)