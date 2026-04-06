# NEXUS IDE

An AI-powered coding assistant and IDE built with Flask. Provides a web-based interface for interacting with multiple AI providers (NVIDIA, OpenAI, OpenRouter, Groq, Ollama) to perform coding tasks like building features, debugging, and refactoring within a sandboxed workspace.

## Architecture

- **Backend**: Flask (Python 3.11) — `app.py` is the main server
- **Frontend**: Vanilla JS + CSS served via Flask templates (`templates/index.html`, `static/`)
- **Agent logic**: `src/agent.py` (ClawAgent) and `src/ultraworker.py` (UltraWorker)
- **Multi-agent**: `src/multi_agent.py` — Swarm mode with Architect/Coder/Reviewer/Terminal/Researcher specialists
- **LLM client**: `src/llm.py` — supports NVIDIA, OpenAI, OpenRouter, Groq, Ollama
- **Tool execution**: `src/toolbox.py` — sandboxed file I/O and bash execution
- **Workspace**: `agent_workspace/` — isolated directory where AI agents operate
- **Knowledge base**: `agent_workspace/.knowledge.md` — built-in coding patterns and best practices

## Agent Modes

1. **Standard** (ClawAgent): Single-agent with mode detection (builder/debugger/refactorer/researcher/reviewer). Uses weighted keyword scoring with word-boundary matching for accurate task classification.
2. **Ultra** (UltraWorker): 40-turn deep reasoning loop with phased execution. Model tiers: Frontier (DeepSeek R1 70B for reasoning), Smart (Qwen 2.5 Coder 32B for implementation), Fast (Phi-4 Mini for quick tasks).
3. **Swarm** (Multi-agent): Parallel specialist agents with task decomposition. Includes dependency-aware decomposition prompt to prevent file conflicts between agents.

## Agent Behaviour

- **Sequential execution**: After planning, agents proceed step-by-step through their plan without re-orienting or re-planning. Progress hints (`[Progress: step N/M — next: ...]`) are injected into tool results to keep the agent moving forward.
- **Professional completion**: DONE blocks follow a structured format (Summary/Files/Verified). Context dumps, workspace trees, and memory contents are explicitly suppressed from output.
- **File explorer**: `_SKIP_DIRS` only hides build artifacts (node_modules, __pycache__, .git, etc.). IDE system dirs (src, static, templates) are only hidden if the workspace is the IDE root itself — user project folders with these names are always shown.

## Running the App

```bash
python3 app.py
```

The app runs on port 5000 and is configured as a webview workflow.

## Entry Points

- **Development**: `python3 app.py` (debug mode, auto-reload)
- **Production (gunicorn)**: `gunicorn --bind 0.0.0.0:5000 main:app`
- `main.py` imports `app` from `app.py` for gunicorn compatibility

## Environment Variables

- `NVIDIA_API_KEY` — NVIDIA AI API key (set as Replit env var)
- `OPENAI_API_KEY` — OpenAI API key (optional)
- `GROQ_API_KEY` — Groq API key (optional)
- `CLAW_WORKSPACE` — workspace directory path (default: `agent_workspace/`)
- `CLAW_MODEL` — default model to use
- `CLAW_ULTRA` — set to "1" to enable UltraWorker mode
- `SESSION_SECRET` — Flask session secret (set as Replit secret)

Provider keys can also be configured via the IDE Settings panel — they are stored in `~/.config/nexus/providers.json`.

## Key Dependencies

- `flask` — web framework
- `openai` — LLM API client (used for all providers via OpenAI-compatible APIs)
- `tiktoken` — token counting for context window management
- `psutil` — system monitoring (CPU/RAM metrics)
- `requests` — HTTP client for Ollama discovery
- `gunicorn` — production WSGI server
