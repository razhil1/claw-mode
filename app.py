from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
import os
import json
import time
import threading
from src.agent import ClawAgent
from src.toolbox import tool_bash_run, get_workspace_root, tool_file_read, tool_file_edit, tool_file_delete
from src.llm import CODING_MODELS, DEFAULT_MODEL

app = Flask(__name__)

if not os.environ.get("OPENROUTER_API_KEY"):
    print("WARNING: OPENROUTER_API_KEY is not set. AI features will be unavailable.")

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "agent_workspace"))
os.makedirs(WORKSPACE_DIR, exist_ok=True)

ACTIVE_MODEL = os.environ.get("CLAW_MODEL", DEFAULT_MODEL)

# Session store — keyed by session_id
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
        dirs[:] = [d for d in dirs if d not in ('node_modules', '__pycache__', '.git', 'venv', '.next', 'dist')]
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

# ===================== MODELS API =====================
@app.route("/api/models")
def get_models():
    result = []
    for model_id, info in CODING_MODELS.items():
        result.append({
            "id": model_id,
            "label": info["label"],
            "description": info["description"],
            "context": info["context"],
            "tier": info["tier"],
            "active": model_id == ACTIVE_MODEL
        })
    return jsonify({"models": result, "active": ACTIVE_MODEL})

@app.route("/api/model", methods=["POST"])
def set_model():
    global ACTIVE_MODEL
    data = request.json
    model_id = data.get("model", "")
    if model_id not in CODING_MODELS:
        return jsonify({"error": "Unknown model"}), 400
    ACTIVE_MODEL = model_id
    os.environ["CLAW_MODEL"] = model_id
    return jsonify({"success": True, "active": ACTIVE_MODEL})

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

# ===================== STREAMING CHAT API =====================
@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id", "default")
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    agent = get_or_create_agent(session_id)

    def generate():
        try:
            for event in agent.run_streaming(prompt):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )

if __name__ == "__main__":
    print("=" * 50)
    print("  Claw IDE — AI Coding Agent v3.0")
    print(f"  Workspace: {WORKSPACE_DIR}")
    print("  URL: http://localhost:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
