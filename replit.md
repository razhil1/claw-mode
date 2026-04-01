# Claw IDE — AI Coding Agent IDE

A Flask-based web IDE that wraps an agentic AI coding assistant. The agent uses NVIDIA's API (OpenAI-compatible) to call LLMs and can run bash commands, read/write files, and perform multi-turn reasoning loops.

## Architecture

- **app.py** — Flask web server; REST + SSE API endpoints
- **src/** — Python agent runtime
  - `agent.py` — `ClawAgent` class: multi-turn agentic loop with stop signal support
  - `llm.py` — LLM client using NVIDIA's API (via openai package) with 7 free models
  - `toolbox.py` — Tool implementations (bash, file read/write/search/list)
  - `tools.py` — Tool metadata/definitions
  - `commands.py` — Agent slash-commands
- **templates/index.html** — Frontend SPA (chat UI + code editor + terminal + preview)
- **static/css/style.css** — Full dark/light theme with CSS variables
- **static/js/main.js** — Complete frontend logic
- **agent_workspace/** — Runtime workspace where the agent creates/edits files

## Key Configuration

- **Port:** 5000 (gunicorn gthread, 4 threads, 300s timeout)
- **LLM Provider:** NVIDIA (https://integrate.api.nvidia.com/v1) — OpenAI-compatible
- **API Key env var:** `NVIDIA_API_KEY`
- **Default Model:** `nvidia:phi-4-mini-instruct` → `microsoft/phi-4-mini-instruct`
- **Agent workspace:** `./agent_workspace/`
- **Max agent turns:** 16 per message

## Available Models (all free via NVIDIA)

- `nvidia:phi-4-mini-instruct` — Phi-4 Mini (default, fast, 16K ctx)
- `nvidia:llama-3.3-70b-instruct` — Llama 3.3 70B (best all-rounder, 128K ctx)
- `nvidia:llama-3.1-8b-instruct` — Llama 3.1 8B (ultra-fast, 128K ctx)
- `nvidia:deepseek-r1-distill-llama-70b` — DeepSeek R1 Distill (thinking/reasoning, 128K ctx)
- `nvidia:qwen2.5-coder-32b` — Qwen2.5 Coder 32B (code specialist, 32K ctx)
- `nvidia:nemotron-super-49b` — Nemotron Super 49B (powerful, 32K ctx)
- `nvidia:gemma-3-12b` — Gemma 3 12B (balanced, 131K ctx)

## API Endpoints

- `GET /` — Serve the SPA
- `GET /workspace/<path>` — Serve files from agent_workspace
- `GET /api/files` — List workspace files
- `GET /api/file/<path>` — Read file
- `PUT /api/file/<path>` — Write file
- `DELETE /api/file/<path>` — Delete file
- `POST /api/file/new` — Create new file
- `POST /api/upload` — Upload files (multipart)
- `GET /api/workspace/stats` — Workspace statistics
- `GET /api/models` — List available LLM models
- `POST /api/model` — Switch active model
- `POST /api/terminal` — Run bash command in workspace
- `POST /api/session/new` — Create new session
- `POST /api/session/<id>/clear` — Clear session history
- `POST /api/chat/stream` — Stream agent SSE events
- `POST /api/chat/stop` — Send stop signal to running agent
- `GET /api/settings/key-status` — Check NVIDIA API key status
- `POST /api/settings/validate-key` — Validate NVIDIA API key
- `POST /api/settings/set-key` — Set NVIDIA API key for current session

## SSE Event Types

- `thinking` — Agent reasoning text
- `tool_call` — Tool being invoked `{tool, payload}`
- `tool_result` — Tool execution result `{tool, result, elapsed}`
- `token` — Text output chunk
- `done` — Final stats `{turns, files_changed, history_len}`
- `error` — Error message
- `key_error` — API key issue `{error_type, message}` (auto-opens settings)
- `stopped` — Agent stopped by user signal

## Features

- **7 free NVIDIA models** — Switch between them in the sidebar
- **Stop agent** — Abort ongoing agent runs mid-stream
- **Dark/light theme** — Toggle with ☾ button, persisted in localStorage
- **Session persistence** — Session ID saved in localStorage across reloads
- **File drag & drop upload** — Drop files onto the file explorer
- **File search** — Filter workspace files by name
- **File rename** — Right-click → Rename in context menu
- **Live preview** — Iframe preview of HTML files with auto-refresh
- **Built-in terminal** — Run commands in agent_workspace with history
- **Code editor** — Textarea editor with line numbers and syntax highlighting
- **Settings modal** — Configure NVIDIA API key with live validation
- **Export chat** — Download chat as Markdown
- **Keyboard shortcuts** — Ctrl+/ focus prompt, Ctrl+Shift+N new session, Ctrl+S save file, Esc close modals

## Dependencies

- Python 3.11+
- Flask 3.x
- Gunicorn with gthread workers (4 threads, 300s timeout)
- openai (Python package, used with NVIDIA's OpenAI-compatible endpoint)

## Running

- **Production:** `gunicorn --bind 0.0.0.0:5000 --reuse-port --reload --worker-class gthread --threads 4 --timeout 300 app:app`
