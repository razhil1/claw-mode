# Claw Code — AI Coding Agent IDE

A Flask-based web IDE that wraps an agentic AI coding assistant. The agent uses OpenRouter to call various LLMs (DeepSeek, Gemini, Claude, GPT-4o) and can run bash commands, read/write files, and perform multi-turn reasoning loops.

## Architecture

- **app.py** — Flask web server, serves the UI and exposes REST API endpoints
- **src/** — Python agent runtime
  - `llm.py` — OpenRouter API client and model registry
  - `runtime.py` — `PortRuntime` class: manages multi-turn agent loops
  - `toolbox.py` — Tool implementations (bash, file read/write)
  - `query_engine.py` — LLM query engine with tool execution
  - `tools.py` — Tool definitions (bash, grep, file ops)
  - `commands.py` — Agent slash-commands
- **templates/index.html** — Frontend SPA (chat UI + code editor + terminal)
- **static/** — CSS and JS assets
- **agent_workspace/** — Runtime workspace where the agent creates/edits files

## Key Configuration

- **Port:** 5000 (Flask dev server on 0.0.0.0)
- **LLM Provider:** OpenRouter (key hardcoded in app.py; set `OPENROUTER_API_KEY` env var to override)
- **Default Model:** `deepseek/deepseek-chat-v3-0324:free`
- **Agent workspace:** `./agent_workspace/`

## Dependencies

- Python 3.12
- Flask 3.x
- Gunicorn (for production)
- All other imports use Python standard library only

## Running

- **Development:** `python3 app.py` (runs on port 5000, debug mode)
- **Production:** `gunicorn --bind=0.0.0.0:5000 --reuse-port app:app`
