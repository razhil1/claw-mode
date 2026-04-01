from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
import os
import json
import time
import threading
from src.agent import ClawAgent
from src.toolbox import tool_bash_run, tool_file_read, tool_file_edit, tool_file_delete
from src.llm import get_all_models, DEFAULT_MODEL, validate_key, set_runtime_key, get_nvidia_key

app = Flask(__name__)

if not os.environ.get("NVIDIA_API_KEY"):
    print("WARNING: No NVIDIA_API_KEY set. AI features require a valid NVIDIA API key.")

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "agent_workspace"))
os.makedirs(WORKSPACE_DIR, exist_ok=True)

ACTIVE_MODEL = os.environ.get("CLAW_MODEL", DEFAULT_MODEL)

_sessions: dict[str, ClawAgent] = {}
_sessions_lock = threading.Lock()


def get_or_create_agent(session_id: str) -> ClawAgent:
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = ClawAgent(model=ACTIVE_MODEL)
        else:
            _sessions[session_id].model = ACTIVE_MODEL
        return _sessions[session_id]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/workspace/")
@app.route("/workspace/<path:filename>")
def serve_workspace(filename="index.html"):
    return send_from_directory(WORKSPACE_DIR, filename)


# ===================== FILES API =====================
@app.route("/api/files")
def list_files():
    files_list = []
    for root, dirs, filenames in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if d not in ('node_modules', '__pycache__', '.git', 'venv', '.next', 'dist', '.cache')]
        for name in filenames:
            rel_dir = os.path.relpath(root, WORKSPACE_DIR)
            if rel_dir == ".":
                files_list.append(name)
            else:
                files_list.append(os.path.join(rel_dir, name).replace("\\", "/"))
    return jsonify({"files": sorted(files_list)})


@app.route("/api/file/<path:filepath>")
def read_file(filepath):
    content = tool_file_read(filepath)
    return jsonify({"path": filepath, "content": content})


@app.route("/api/file/<path:filepath>", methods=["PUT"])
def write_file(filepath):
    data = request.json
    content = data.get("content", "")
    result = tool_file_edit(filepath, content)
    return jsonify({"ok": True, "result": result})


@app.route("/api/file/<path:filepath>", methods=["DELETE"])
def delete_file(filepath):
    result = tool_file_delete(filepath)
    return jsonify({"ok": True, "result": result})


@app.route("/api/file/new", methods=["POST"])
def new_file():
    data = request.json
    filepath = data.get("path", "").strip().lstrip("/")
    if not filepath:
        return jsonify({"error": "No path provided"}), 400
    content = data.get("content", "")
    result = tool_file_edit(filepath, content)
    return jsonify({"ok": True, "result": result})


@app.route("/api/upload", methods=["POST"])
def upload_files():
    from src.toolbox import enforce_safe_path
    uploaded = []
    errors = []
    for key in request.files:
        f = request.files[key]
        filename = f.filename.lstrip("/") if f.filename else None
        if not filename:
            continue
        try:
            safe_path = enforce_safe_path(filename)
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            f.save(str(safe_path))
            uploaded.append(filename)
        except Exception as e:
            errors.append({"file": filename, "error": str(e)})
    return jsonify({"ok": True, "uploaded": uploaded, "errors": errors})


# ===================== WORKSPACE STATS =====================
@app.route("/api/workspace/stats")
def workspace_stats():
    total_size = 0
    file_count = 0
    for root, dirs, filenames in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if d not in ('node_modules', '__pycache__', '.git')]
        for name in filenames:
            fp = os.path.join(root, name)
            try:
                total_size += os.path.getsize(fp)
                file_count += 1
            except OSError:
                pass
    return jsonify({
        "file_count": file_count,
        "total_size": total_size,
        "total_size_kb": round(total_size / 1024, 1),
    })


# ===================== MODELS API =====================
@app.route("/api/models")
def get_models():
    all_models = get_all_models()
    result = []
    for model_id, info in all_models.items():
        result.append({
            "id": model_id,
            "label": info["label"],
            "short": info.get("short", ""),
            "description": info.get("description", ""),
            "context": info.get("context", 4096),
            "tier": info.get("tier", "free"),
            "provider": info.get("provider", "nvidia"),
            "role": info.get("role", "balanced"),
            "emoji": info.get("emoji", "⚡"),
            "price_note": info.get("price_note", "Free"),
            "active": model_id == ACTIVE_MODEL,
        })
    return jsonify({"models": result, "active": ACTIVE_MODEL})


@app.route("/api/model", methods=["POST"])
def set_model():
    global ACTIVE_MODEL
    data = request.json
    model_id = data.get("model", "")
    all_models = get_all_models()
    if model_id not in all_models:
        return jsonify({"error": "Unknown model"}), 400
    ACTIVE_MODEL = model_id
    os.environ["CLAW_MODEL"] = model_id
    return jsonify({"success": True, "active": ACTIVE_MODEL, "provider": "nvidia"})


# ===================== TERMINAL API =====================
@app.route("/api/terminal", methods=["POST"])
def terminal():
    data = request.json
    command = data.get("command", "")
    if not command:
        return jsonify({"output": "Error: No command provided."}), 400
    output = tool_bash_run(command)
    return jsonify({"output": output})


# ===================== SESSION API =====================
@app.route("/api/session/new", methods=["POST"])
def new_session():
    import uuid
    session_id = uuid.uuid4().hex
    return jsonify({"session_id": session_id})


@app.route("/api/session/<session_id>/clear", methods=["POST"])
def clear_session(session_id):
    with _sessions_lock:
        if session_id in _sessions:
            _sessions[session_id].clear_history()
    return jsonify({"ok": True})


# ===================== STOP AGENT =====================
@app.route("/api/chat/stop", methods=["POST"])
def stop_chat():
    data = request.json or {}
    session_id = data.get("session_id", "default")
    with _sessions_lock:
        agent = _sessions.get(session_id)
    if agent:
        agent.request_stop()
        return jsonify({"ok": True, "message": "Stop signal sent."})
    return jsonify({"ok": False, "message": "Session not found."}), 404


# ===================== STREAMING CHAT API =====================
@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id", "default")
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    agent = get_or_create_agent(session_id)

    event_queue: list = []
    done_event = threading.Event()
    error_holder: list = []

    def run_agent():
        try:
            for event in agent.run_streaming(prompt):
                event_queue.append(event)
        except Exception as e:
            error_holder.append(str(e))
        finally:
            done_event.set()

    t = threading.Thread(target=run_agent, daemon=True)
    t.start()

    def generate():
        sent = 0
        while not done_event.is_set() or sent < len(event_queue):
            while sent < len(event_queue):
                yield f"data: {json.dumps(event_queue[sent])}\n\n"
                sent += 1
            if not done_event.is_set():
                yield ": heartbeat\n\n"
                done_event.wait(timeout=0.5)
        if error_holder:
            yield f"data: {json.dumps({'type': 'error', 'message': error_holder[0]})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


# ===================== SETTINGS / KEY MANAGEMENT =====================
@app.route("/api/settings/key-status")
def key_status():
    key = get_nvidia_key()
    return jsonify({
        "nvidia": {
            "configured": bool(key),
            "prefix": key[:14] + "..." if key else "",
        }
    })


@app.route("/api/settings/validate-key", methods=["POST"])
def api_validate_key():
    data = request.json
    key = data.get("key", "").strip()
    if not key:
        return jsonify({"ok": False, "message": "No key provided"}), 400
    result = validate_key(key)
    return jsonify(result)


@app.route("/api/settings/set-key", methods=["POST"])
def api_set_key():
    data = request.json
    key = data.get("key", "").strip()
    if not key:
        return jsonify({"ok": False, "message": "No key provided"}), 400
    set_runtime_key(key)
    os.environ["NVIDIA_API_KEY"] = key
    return jsonify({"ok": True, "message": "NVIDIA key saved for this session"})


if __name__ == "__main__":
    print("=" * 50)
    print("  Claw IDE — AI Coding Agent v3.2")
    print(f"  Workspace: {WORKSPACE_DIR}")
    print("  URL: http://localhost:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
