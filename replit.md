# NEXUS IDE — AI Coding Agent IDE

A Flask-based web IDE that wraps an agentic AI coding assistant. The agent uses multiple AI providers (NVIDIA, OpenAI, OpenRouter, Groq, Ollama, Custom) to call LLMs and can run bash commands, read/write files, and perform multi-turn reasoning loops.

## Architecture

- **app.py** — Flask web server; REST + SSE API endpoints
- **src/** — Python agent runtime
  - `agent.py` — `ClawAgent` class: multi-turn agentic loop with stop signal support
  - `ultraworker.py` — Ultra mode worker with enhanced capabilities
  - `llm.py` — LLM client supporting multiple providers (NVIDIA, OpenAI, OpenRouter, Groq, Ollama, Custom)
  - `toolbox.py` — Tool implementations (bash, file read/write/search/list)
  - `tools.py` — Tool metadata/definitions
  - `commands.py` — Agent slash-commands
  - `plans.py` — Plans & licensing system (Community/Pro/Enterprise tiers)
  - `telegram_bot.py` — Telegram bot for purchase processing & license activation
  - `security.py` — CSRF protection, security headers, rate limiting, input sanitization
- **templates/index.html** — Frontend SPA (chat UI + code editor + terminal + preview)
- **static/css/nexus.css** — Full dark/light theme with CSS variables
- **static/js/** — Frontend JavaScript modules:
  - `nexus.js` — Core UI functions, settings, modal management
  - `editor.js` — CodeMirror editor, language picker, indent settings
  - `agent.js` — Agent chat, streaming, mode switching
  - `terminal.js` — Terminal emulation, split terminals, run/debug
  - `files.js` — File tree, drag & drop, upload
  - `git.js` — Git operations, branch management, git config
  - `models.js` — Model selector, provider management
  - `tools.js` — Search, diff viewer, replace all
  - `keybindings.js` — Keyboard shortcuts
  - `ui.js` — UI utilities
  - `plans.js` — Plans/pricing modal, license activation, profile, guide, community
  - `main.js` — Fallback stubs for functions
- **agent_workspace/** — Runtime workspace where the agent creates/edits files
- **agent_workspace/.knowledge.md** — Built-in knowledge base (30 specialist agents, 135 skills)

## Key Configuration

- **Port:** 5000 (Flask dev server or gunicorn)
- **LLM Providers:** NVIDIA, OpenAI, OpenRouter, Groq, Ollama, Custom
- **Provider prefix format:** `provider:model_name` (e.g., `nvidia:phi-4-mini-instruct`)
- **Provider config:** `~/.config/nexus/providers.json`
- **Default Model:** `nvidia:phi-4-mini-instruct`
- **Agent workspace:** `./agent_workspace/`
- **Max agent turns:** 16 per message

## Plans & Licensing

- **Community** — Free, 50 messages/day, 3 agent modes
- **Pro** — $19/mo, unlimited messages, all 6 modes, priority support
- **Enterprise** — $49/mo, unlimited, multi-agent, team features, custom models
- **License keys:** `NX-PRO-XXXXXXXX` or `NX-ENT-XXXXXXXX`
- **License storage:** `~/.config/nexus/plan.json`
- **Purchase flow:** IDE → purchase code → Telegram bot `/activate CODE` → license key → activate in IDE

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
- `GET /api/settings/key-status` — Check API key status
- `POST /api/settings/validate-key` — Validate API key
- `POST /api/settings/set-key` — Set API key for current session
- `GET /api/plans` — Get all plans and current plan info
- `GET /api/plans/current` — Current plan details
- `POST /api/plans/check-limit` — Check message limit
- `POST /api/plans/purchase-code` — Generate purchase code
- `POST /api/license/activate` — Activate license key
- `POST /api/license/deactivate` — Deactivate license
- `GET /api/profile` — User profile with plan info
- `POST /api/telegram/webhook` — Telegram bot webhook
- `POST /api/telegram/setup` — Configure Telegram bot
- `GET /api/telegram/status` — Telegram bot status
- `GET /api/guide` — Getting started guide
- `GET /api/community` — Community links

## SSE Event Types

- `thinking` — Agent reasoning text
- `tool_call` — Tool being invoked `{tool, payload}`
- `tool_result` — Tool execution result `{tool, result, elapsed}`
- `token` — Text output chunk
- `done` — Final stats `{turns, files_changed, history_len}`
- `error` — Error message
- `key_error` — API key issue `{error_type, message}`
- `stopped` — Agent stopped by user signal

## Security

- Security headers (CSP, XSS protection, etc.) via `src/security.py`
- CSRF token support for state-changing endpoints
- Rate limiting: 120 requests per minute
- Input sanitization and path traversal protection
- Agent workspace isolation

## EXE Packaging

- `build_exe.bat` (Windows) or `build_exe.sh` (Mac/Linux)
- Output: `dist/NEXUS_IDE.exe`
- Uses PyInstaller; requires Python 3.11 or 3.12

## Dependencies

- Python 3.11+
- Flask 3.x, Flask-SQLAlchemy, Flask-Login
- openai (Python package, multi-provider)
- psycopg2-binary (PostgreSQL)
- PyInstaller (for EXE builds)

## Running

- **Dev:** `python app.py`
- **Production:** `gunicorn --bind 0.0.0.0:5000 --reuse-port --reload --worker-class gthread --threads 4 --timeout 300 app:app`
- **Local:** `./start.sh` (Mac/Linux) or `start.bat` (Windows)
