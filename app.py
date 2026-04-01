from flask import Flask, request, jsonify, render_template, send_from_directory

import os
import io
import sys
from contextlib import redirect_stdout
from src.runtime import PortRuntime
from src.toolbox import tool_bash_run, get_workspace_root, tool_file_read
from src.llm import CODING_MODELS, DEFAULT_MODEL

app = Flask(__name__)

# Ensure API key is set for OpenRouter
if "OPENROUTER_API_KEY" not in os.environ:
    os.environ["OPENROUTER_API_KEY"] = "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"

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "agent_workspace"))
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# Active model — can be changed via UI
ACTIVE_MODEL = os.environ.get("CLAW_MODEL", DEFAULT_MODEL)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/workspace/")
@app.route("/workspace/<path:filename>")
def serve_workspace(filename="index.html"):
    return send_from_directory(WORKSPACE_DIR, filename)

@app.route("/api/files")
def list_files():
    files_list = []
    for root, dirs, filenames in os.walk(WORKSPACE_DIR):
        # Skip node_modules and __pycache__
        dirs[:] = [d for d in dirs if d not in ('node_modules', '__pycache__', '.git', 'venv')]
        for name in filenames:
            rel_dir = os.path.relpath(root, WORKSPACE_DIR)
            if rel_dir == ".":
                files_list.append(name)
            else:
                files_list.append(os.path.join(rel_dir, name).replace("\\", "/"))
    return jsonify({"files": sorted(files_list)})

@app.route("/api/models")
def get_models():
    """Return list of available coding models."""
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
    """Switch the active LLM model."""
    global ACTIVE_MODEL
    data = request.json
    model_id = data.get("model", "")
    if model_id not in CODING_MODELS:
        return jsonify({"error": "Unknown model"}), 400
    ACTIVE_MODEL = model_id
    os.environ["CLAW_MODEL"] = model_id
    return jsonify({"success": True, "active": ACTIVE_MODEL})

@app.route("/api/file/<path:filepath>")
def read_file(filepath):
    """Read a file's content from the workspace for the code editor."""
    content = tool_file_read(filepath)
    return jsonify({"path": filepath, "content": content})

@app.route("/api/terminal", methods=["POST"])
def terminal():
    """Run a command directly from the UI terminal."""
    data = request.json
    command = data.get("command", "")
    if not command:
        return jsonify({"output": "Error: No command provided."}), 400
    output = tool_bash_run(command)
    return jsonify({"output": output})

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    f = io.StringIO()
    with redirect_stdout(f):
        try:
            runtime = PortRuntime()
            results = runtime.run_turn_loop(prompt, max_turns=8)
            final_turn = results[-1] if results else None
            output = final_turn.output if final_turn else "No output"
        except Exception as e:
            output = f"Internal Agent Error: {str(e)}"

    stdout_log = f.getvalue()

    return jsonify({
        "response": output,
        "log": stdout_log
    })

if __name__ == "__main__":
    print("=" * 50)
    print("  Claw IDE — AI Coding Agent")
    print(f"  Workspace: {WORKSPACE_DIR}")
    print("  URL: http://localhost:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True)
