# Claw IDE — AI Coding Agent IDE

A Flask-based web IDE that wraps an agentic AI coding assistant. The agent uses OpenRouter or Groq to call various LLMs and can run bash commands, read/write files, and perform multi-turn reasoning loops.

## Architecture

- **app.py** — Flask web server; REST + SSE API endpoints
- **src/** — Python agent runtime
  - `agent.py` — `ClawAgent` class: multi-turn agentic loop with stop signal support
  - `llm.py` — LLM client supporting OpenRouter + Groq with 15+ models
  - `toolbox.py` — Tool implementations (bash, file read/write/search/list)
  - `runtime.py` — Legacy PortRuntime class (porting workspace)
  - `query_engine.py` — Legacy LLM query engine
  - `tools.py` — Tool metadata/definitions
  - `commands.py` — Agent slash-commands
- **templates/index.html** — Frontend SPA (chat UI + code editor + terminal + preview)
- **static/css/style.css** — Full dark/light theme with CSS variables
- **static/js/main.js** — Complete frontend logic
- **agent_workspace/** — Runtime workspace where the agent creates/edits files

## Key Configuration

- **Port:** 5000 (Flask dev server on 0.0.0.0)
- **LLM Providers:** OpenRouter and Groq (set via Settings modal or env vars)
- **Default Model:** `groq:llama-3.3-70b-versatile`
- **Agent workspace:** `./agent_workspace/`
- **Max agent turns:** 16 per message

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
- `GET /api/settings/key-status` — Check configured API keys
- `POST /api/settings/validate-key` — Validate an API key
- `POST /api/settings/set-key` — Set API key for current session

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

- **Multi-model support** — 15+ free and paid models via Groq + OpenRouter
- **Stop agent** — Abort ongoing agent runs mid-stream
- **Dark/light theme** — Toggle with ☾ button, persisted in localStorage
- **Session persistence** — Session ID saved in localStorage across reloads
- **File drag & drop upload** — Drop files onto the file explorer
- **File search** — Filter workspace files by name
- **File rename** — Right-click → Rename in context menu
- **Live preview** — Iframe preview of HTML files with auto-refresh
- **Built-in terminal** — Run commands in agent_workspace with history
- **Code editor** — Textarea editor with line numbers and syntax highlighting
- **Settings modal** — Configure API keys with live validation
- **Export chat** — Download chat as Markdown
- **Keyboard shortcuts** — Ctrl+/ focus prompt, Ctrl+Shift+N new session, Ctrl+S save file, Esc close modals

## Dependencies

- Python 3.12
- Flask 3.x
- Gunicorn (for production)
- All other imports use Python standard library only

## Running

- **Development:** `python3 app.py` (runs on port 5000, debug mode)
- **Production:** `gunicorn --bind=0.0.0.0:5000 --reuse-port app:app`
