"""
╔══════════════════════════════════════════════════════════════════════════════════╗
║        ULTRA MULTI-AGENT ORCHESTRATOR — NVIDIA × SPECIALIST SWARM              ║
║                                                                                  ║
║  ARCHITECTURE (matches multi_agent_nvidia_flow.svg)                              ║
║  ──────────────────────────────────────────────────────────────────────────────  ║
║  Phase 0 → Mode Detector    — Llama 3.1 8B decides: Conversation vs Coding      ║
║                                                                                  ║
║  CONVERSATION FLOW                                                               ║
║  Phase 1 → Router           — Llama 3.1 8B selects 1-3 NVIDIA model agents      ║
║  Phase 2 → Parallel Agents  — Thinker | Coder | Balanced | Writer | Long-ctx    ║
║                               + on-demand: CoderHeavy | Powerful | Enterprise    ║
║                                            Compact | Fast                        ║
║  Phase 3 → Aggregator       — Llama 3.3 70B merges, deduplicates, polishes      ║
║  Phase 4 → Final Response                                                        ║
║                                                                                  ║
║  CODING FLOW                                                                     ║
║  Phase 1 → TaskDecomposer   — DeepSeek R1 70B breaks into sub-tasks             ║
║  Phase 2 → Specialist Swarm — Architect | Coder | Reviewer | Terminal | Research ║
║             (parallel, file-locked, tool-enabled, SSE-streamed)                  ║
║  Phase 3 → Aggregator       — Llama 3.3 70B merges all specialist outputs       ║
║  Phase 4 → Final Response                                                        ║
║                                                                                  ║
║  CROSS-CUTTING                                                                   ║
║  • Token trimmer   — per-model context-window management                         ║
║  • History compressor — Phi-4 Mini summarizes long conversations                 ║
║  • FileLockManager — prevents concurrent file-write conflicts                    ║
║  • SSE event bus   — every phase emits structured events                         ║
╚══════════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Any

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

from openai import OpenAI
from .llm import get_nvidia_key

# ──────────────────────────────────────────────────────────────────────────────
# TRY to import internal tooling (only present when inside the Claw workspace)
# ──────────────────────────────────────────────────────────────────────────────
import logging as _logging
_ma_log = _logging.getLogger("claw.multi_agent")

try:
    from .toolbox import (
        tool_bash_run,
        tool_file_read,
        tool_file_edit,
        tool_file_patch,
        tool_file_delete,
        tool_list_dir,
        tool_search,
        tool_view_file_lines,
    )
    from .agent import (
        SYSTEM_PROMPT,
        _execute_tool,
        _parse_all_tool_calls,
        _clean_payload,
        _strip_tool_lines,
        _detect_done,
        _extract_plan,
        _is_error,
        _get_workspace_map,
        classify_task,
        MAX_AGENT_TURNS,
        RESULT_TRIM_CHARS,
    )
    _INTERNAL_TOOLS_AVAILABLE = True
except ImportError as _import_err:
    # Log the real error so it shows up in server logs — silent failures waste hours
    _ma_log.warning(
        "Internal tools not available (ImportError: %s). "
        "Swarm agents will run WITHOUT file-system tools. "
        "Ensure src/toolbox.py and src/agent.py are present.",
        _import_err,
    )
    _INTERNAL_TOOLS_AVAILABLE = False
    SYSTEM_PROMPT = "You are a helpful coding assistant."
    MAX_AGENT_TURNS = 15
    RESULT_TRIM_CHARS = 4000

    def _execute_tool(name, payload):
        return (
            f"[ERROR] Tool '{name}' unavailable — toolbox import failed. "
            "Check server logs for the ImportError details."
        )

    def _parse_all_tool_calls(text):
        return []

    def _clean_payload(p):
        return p

    def _strip_tool_lines(text):
        return text

    def _detect_done(text):
        return True

    def _extract_plan(text):
        return ""

    def _is_error(text):
        return "error" in text.lower()

    def _get_workspace_map():
        return str(Path.cwd())

    def classify_task(text):
        return "default"


# ══════════════════════════════════════════════════════════════════════════════
# NVIDIA CLIENT HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _get_client() -> OpenAI:
    """Initialize the OpenAI-compatible NVIDIA client using the latest runtime key."""
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=get_nvidia_key(),
    )

# ══════════════════════════════════════════════════════════════════════════════
# MODEL REGISTRY — Unified with LLMClient
# ══════════════════════════════════════════════════════════════════════════════
# key            → (model_id,                                   ctx_tokens)
MODELS: dict[str, tuple[str, int]] = {
    "router":       ("meta/llama-3.1-8b-instruct",               128_000),
    "thinker":      ("deepseek-ai/deepseek-r1-distill-llama-70b", 128_000),
    "coder":        ("qwen/qwen2.5-coder-32b-instruct",            32_768),
    "coder_heavy":  ("mistralai/mistral-small-24b-instruct-2501",   32_768),
    "fast":         ("meta/llama-3.1-8b-instruct",               128_000),
    "balanced":     ("meta/llama-3.3-70b-instruct",              128_000),
    "powerful":     ("nvidia/llama-3.3-nemotron-super-49b-v1",    32_768),
    "enterprise":   ("nvidia/nemotron-3-super-120b-a12b",          32_768),
    "long_context": ("minimaxai/minimax-m2.5",                 1_000_000),
    "compact":      ("microsoft/phi-4-mini-instruct",              16_384),
    "writer":       ("google/gemma-3-12b-it",                     131_072),
    "aggregator":   ("meta/llama-3.3-70b-instruct",              128_000),
    "decomposer":   ("deepseek-ai/deepseek-r1-distill-llama-70b", 128_000),
}


# ══════════════════════════════════════════════════════════════════════════════
# NVIDIA AGENT PERSONAS
# ══════════════════════════════════════════════════════════════════════════════

NVIDIA_PERSONAS: dict[str, str] = {
    "thinker": (
        "You are a deep reasoning and chain-of-thought expert (DeepSeek R1 70B).\n"
        "Think step-by-step. Show your full reasoning. Break complex problems into smaller "
        "steps. Be systematic and thorough. Tackle math, logic, planning, philosophy, analysis."
    ),
    "coder": (
        "You are an expert software engineer (Qwen2.5 Coder 32B).\n"
        "Write clean, efficient, well-commented code. Always include what the code does, "
        "how to run it, edge cases, and improvement ideas.\n"
        "Specialise in: Python, JavaScript, APIs, scripts, debugging."
    ),
    "coder_heavy": (
        "You are a senior software architect (Mistral Small 4).\n"
        "Handle complex system design, large codebases, refactoring, and architecture. "
        "Write production-grade code with error handling, tests, and documentation."
    ),
    "fast": (
        "You are a fast, direct assistant (Llama 3.1 8B). Give concise answers only. "
        "No fluff, no preamble. Just the answer."
    ),
    "balanced": (
        "You are a well-rounded expert assistant (Llama 3.3 70B). Excellent at coding, "
        "reasoning, and following complex instructions. Provide thorough, accurate, "
        "well-structured responses."
    ),
    "powerful": (
        "You are a powerful AI (Nemotron Super 49B) for complex tasks. Handle multi-step "
        "problems, long-form generation, and nuanced analysis. Be comprehensive but organised."
    ),
    "enterprise": (
        "You are an enterprise-grade AI (Nemotron 120B) for professional tasks. Handle "
        "complex reasoning, technical documentation, and advanced problem solving. "
        "Maintain high accuracy and professional tone."
    ),
    "long_context": (
        "You are a long-context specialist (MiniMax M2.5). You excel at processing and "
        "understanding very large documents, long conversations, and tasks requiring "
        "retention of many details across a huge input."
    ),
    "compact": (
        "You are a compact but capable assistant (Phi-4 Mini). Fast, efficient, great for "
        "quick coding tasks and concise reasoning."
    ),
    "writer": (
        "You are a professional writer (Gemma 3 12B). Write clearly, engagingly, with good "
        "structure. Excellent at: summaries, explanations, blog posts, documentation, and "
        "creative writing."
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# SPECIALIST AGENT ROLES  (coding swarm — used in CODING FLOW)
# ══════════════════════════════════════════════════════════════════════════════

SPECIALIST_ROLES: dict[str, dict] = {
    "architect": {
        "name": "Architect",
        "emoji": "🏗️",
        "color": "#a78bfa",
        "description": "Plans structure, designs APIs, creates scaffolding",
        "system_suffix": (
            "\nYou are the ARCHITECT agent.\n"
            "• Design file structures and directory layouts\n"
            "• Create project scaffolding and boilerplate\n"
            "• Define API contracts and interfaces\n"
            "• Write configuration files (package.json, tsconfig, pyproject.toml, etc.)\n"
            "• Focus on overall structure — NOT implementation details\n"
        ),
        "tools": ["ListDirTool", "FileReadTool", "FileEditTool", "BashTool", "ThinkTool"],
        "nvidia_model": "balanced",
    },
    "coder": {
        "name": "Coder",
        "emoji": "⚡",
        "color": "#00d4ff",
        "description": "Writes production code, implements features, creates components",
        "system_suffix": (
            "\nYou are the CODER agent.\n"
            "• Write production-grade implementation code\n"
            "• Create React/Vue/Angular components\n"
            "• Implement business logic and algorithms\n"
            "• Write CSS/styling and UI code\n"
            "• Focus on clean, efficient, working code\n"
            "• You can write MULTIPLE files per turn — be aggressive and fast\n"
        ),
        "tools": ["FileReadTool", "FileEditTool", "FilePatchTool", "BashTool",
                  "ListDirTool", "ThinkTool", "ViewFileLinesTool"],
        "nvidia_model": "coder",
    },
    "reviewer": {
        "name": "Reviewer",
        "emoji": "🔍",
        "color": "#f59e0b",
        "description": "Reviews code quality, finds bugs, suggests improvements",
        "system_suffix": (
            "\nYou are the REVIEWER agent.\n"
            "• Review code for bugs, security issues, and anti-patterns\n"
            "• Check for missing error handling and edge cases\n"
            "• Verify type safety and input validation\n"
            "• Suggest performance optimisations\n"
            "• Apply fixes directly using FilePatchTool\n"
        ),
        "tools": ["FileReadTool", "ViewFileLinesTool", "SearchTool", "FilePatchTool",
                  "ThinkTool", "ListDirTool"],
        "nvidia_model": "coder_heavy",
    },
    "terminal": {
        "name": "Terminal",
        "emoji": "💻",
        "color": "#10b981",
        "description": "Runs commands, installs packages, executes tests, manages deps",
        "system_suffix": (
            "\nYou are the TERMINAL agent.\n"
            "• Install dependencies (npm, pip, cargo, go get, etc.)\n"
            "• Run build scripts and dev servers\n"
            "• Execute tests and report results\n"
            "• Run linters and formatters\n"
            "• Manage environment and tooling\n"
        ),
        "tools": ["BashTool", "FileReadTool", "ListDirTool", "ThinkTool"],
        "nvidia_model": "balanced",
    },
    "researcher": {
        "name": "Researcher",
        "emoji": "📚",
        "color": "#8b5cf6",
        "description": "Reads docs, searches code, gathers context for other agents",
        "system_suffix": (
            "\nYou are the RESEARCHER agent.\n"
            "• Read and understand the existing codebase thoroughly\n"
            "• Search for patterns, imports, and dependencies\n"
            "• Map out file relationships and data flow\n"
            "• Provide context summaries for other agents\n"
            "• NEVER modify files — only read and report\n"
        ),
        "tools": ["FileReadTool", "ViewFileLinesTool", "SearchTool", "ListDirTool",
                  "ThinkTool"],
        "nvidia_model": "thinker",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def count_tokens(text: str) -> int:
    """Estimate token count. Uses tiktoken when available, else ~4 chars/token."""
    if _TIKTOKEN_AVAILABLE:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 2)


def safe_max_tokens(model_key: str, messages: list, desired: int = 4096) -> int:
    """Calculate safe max_tokens that won't exceed the model's context limit."""
    _, ctx_limit = MODELS[model_key]
    total_input = sum(count_tokens(m.get("content", "")) for m in messages)
    remaining = ctx_limit - total_input - 500   # 500-token safety buffer
    return min(desired, max(512, remaining))


def trim_messages(messages: list, model_key: str, completion_budget: int = 4096) -> list:
    """Trim conversation history to fit within model context window."""
    _, ctx_limit = MODELS[model_key]
    max_input = ctx_limit - completion_budget - 500 # 500-token safety buffer

    system_msgs = [m for m in messages if m["role"] == "system"]
    other_msgs  = [m for m in messages if m.get("role") != "system"]

    while True:
        total = sum(count_tokens(m.get("content", "")) for m in system_msgs + other_msgs)
        if total <= max_input or len(other_msgs) <= 1:
            break
        other_msgs.pop(0)

    # FINAL RESORT: If even one message is too large, truncate it
    total = sum(count_tokens(m.get("content", "")) for m in system_msgs + other_msgs)
    if total > max_input and other_msgs:
        m = other_msgs[0]
        # Calculate how much space is left for the last message
        sys_tokens = sum(count_tokens(m.get("content", "")) for m in system_msgs)
        target_tokens = max_input - sys_tokens
        if target_tokens > 100:
            content = m.get("content", "")
            # Assume ~2 chars per token for safe truncation
            char_limit = target_tokens * 2
            m["content"] = content[:char_limit] + "... [TRUNCATED DUE TO CONTEXT LIMIT]"

    return system_msgs + other_msgs


# ══════════════════════════════════════════════════════════════════════════════
# NVIDIA LLM CALL  (streaming + non-streaming)
# ══════════════════════════════════════════════════════════════════════════════

def nvidia_call(
    model_key: str,
    messages: list,
    temperature: float = 0.7,
    desired_tokens: int = 4096,
    stream: bool = True,
    on_token: Any = None,     # optional callback(str) for each streaming token
) -> str:
    """
    Single-turn call to an NVIDIA model.

    Args:
        model_key: Key into MODELS dict.
        messages:  List of {role, content} dicts.
        temperature: Sampling temperature.
        desired_tokens: Target response length (capped by context window).
        stream: Whether to use streaming.
        on_token: Optional callback invoked for each streamed token chunk.

    Returns:
        Full response string.
    """
    model_id, _ = MODELS[model_key]
    messages = trim_messages(messages, model_key, completion_budget=desired_tokens)
    max_tok = safe_max_tokens(model_key, messages, desired=desired_tokens)

    client = _get_client()
    try:
        if stream:
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=max_tok,
                temperature=temperature,
                stream=True,
            )
            result = ""
            for chunk in response:
                delta = chunk.choices[0].delta.content or ""
                result += delta
                if on_token:
                    on_token(delta)
            return result
        else:
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=max_tok,
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
    except Exception as exc:
        return f"[{model_key} error: {exc}]"


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY COMPRESSOR  (Phi-4 Mini)
# ══════════════════════════════════════════════════════════════════════════════

def compress_history(messages: list) -> list:
    """
    When conversation history grows too long, compress it into a single
    summary message using the compact Phi-4 Mini model.
    """
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in messages
        if m["role"] != "system"
    )
    print("\n📦 Compressing conversation history with Phi-4 Mini…")

    summary = nvidia_call(
        "compact",
        messages=[{
            "role": "user",
            "content": (
                "Summarise this conversation in 6 bullet points, "
                "preserving key facts, decisions, and code snippets:\n\n"
                f"{history_text[:8000]}"
            ),
        }],
        temperature=0,
        desired_tokens=500,
        stream=False,
    )
    return [{"role": "system", "content": f"[Conversation summary]:\n{summary}"}]


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER  (Llama 3.1 8B)
# ══════════════════════════════════════════════════════════════════════════════

ROUTER_SYSTEM = """\
You are a task router for a multi-agent AI system.
Given a user prompt, choose which specialist NVIDIA agents to activate.

Available agents:
- "thinker"      → math, logic, chain-of-thought, planning, analysis
- "coder"        → code generation, debugging, scripts, APIs (short-medium)
- "coder_heavy"  → complex system design, large codebases, architecture
- "fast"         → simple questions, single facts, very short answers
- "balanced"     → general questions, mixed tasks, instruction-following
- "powerful"     → complex multi-step tasks, long-form content
- "enterprise"   → advanced professional reasoning, technical documentation
- "long_context" → very long documents, large file analysis, 10k+ token inputs
- "compact"      → quick coding snippets, lightweight tasks
- "writer"       → essays, blog posts, summaries, creative writing, documentation

Rules:
- Choose 1-3 agents MAX. More is not always better.
- For coding: always include "coder". Add "thinker" if logic-heavy.
- For writing: use "writer". Add "balanced" for mixed writing+reasoning.
- For long documents: use "long_context".
- For simple questions: use only "fast".
- Never combine "fast" with heavy agents.

Respond ONLY with a valid JSON array. Example: ["coder", "thinker"]
No explanation. No markdown. Just the JSON array."""


def route_task(user_prompt: str) -> list[str]:
    """Use Llama 3.1 8B to select 1-3 optimal NVIDIA agents."""
    raw = nvidia_call(
        "router",
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user",   "content": f"Route this task: {user_prompt[:600]}"},
        ],
        temperature=0,
        desired_tokens=80,
        stream=False,
    )
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        agents = json.loads(raw)
        valid = [a for a in agents if a in NVIDIA_PERSONAS]
        if valid:
            print(f"\n🔀 Router activated: {valid}")
            return valid
    except (json.JSONDecodeError, TypeError):
        pass
    print("⚠️  Router fallback → balanced")
    return ["balanced"]


MODE_ROUTER_SYSTEM = """\
You are a task-mode classifier. Decide whether the user's request is:
- "coding"  → involves writing/editing/running files, building software, coding projects
- "conversation" → Q&A, writing, analysis, explanations, research, math, brainstorming

Respond ONLY with one word: "coding" or "conversation". No explanation."""


def detect_mode(user_prompt: str) -> str:
    """Quickly decide whether to use the coding swarm or the conversation flow."""
    result = nvidia_call(
        "fast",
        messages=[
            {"role": "system", "content": MODE_ROUTER_SYSTEM},
            {"role": "user",   "content": user_prompt[:400]},
        ],
        temperature=0,
        desired_tokens=5,
        stream=False,
    ).strip().lower()
    mode = "coding" if "coding" in result else "conversation"
    print(f"\n🧭 Mode detected: {mode.upper()}")
    return mode


# ══════════════════════════════════════════════════════════════════════════════
# AGGREGATOR  (Llama 3.3 70B)
# ══════════════════════════════════════════════════════════════════════════════

AGGREGATOR_SYSTEM = """\
You are a senior AI aggregator. You receive outputs from multiple specialist AI agents
who each answered the same user question.

Your job:
1. Synthesise the best insights from every agent into ONE cohesive response
2. Eliminate redundancy — never repeat the same point twice
3. Resolve contradictions — pick the most accurate/logical answer
4. Organise with clear headers (##) where helpful
5. Preserve technical details from coder/thinker agents
6. Maintain the writing quality of the writer agent
7. Make the final answer MORE complete than any single agent alone

Output a single, polished, final answer.
Do NOT mention the agents or that you are aggregating."""


def aggregate_responses(user_prompt: str, agent_results: dict[str, str]) -> Generator[dict, None, None]:
    """
    Merge multiple agent outputs into one polished answer.
    Yields SSE events while streaming.
    """
    combined = "\n\n".join(
        f"━━━ {name.upper()} AGENT ━━━\n{output}"
        for name, output in agent_results.items()
    )

    messages = [
        {"role": "system", "content": AGGREGATOR_SYSTEM},
        {"role": "user",   "content": f"User question:\n{user_prompt}\n\nAgent outputs:\n{combined}"},
    ]
    messages = trim_messages(messages, "aggregator", completion_budget=4096)

    yield {"type": "aggregator_start", "agent_count": len(agent_results)}

    result = ""

    def on_token(delta: str):
        nonlocal result
        result += delta

    try:
        result = nvidia_call(
            "aggregator",
            messages=messages,
            temperature=0.5,
            desired_tokens=4096,
            stream=True,
            on_token=on_token,
        )
    except Exception as exc:
        result = max(agent_results.values(), key=len)
        yield {"type": "aggregator_error", "message": str(exc)}

    yield {"type": "aggregator_done", "text": result}
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TASK DECOMPOSER  (DeepSeek R1 70B)  — used in CODING FLOW
# ══════════════════════════════════════════════════════════════════════════════

DECOMPOSE_PROMPT = """\
You are a task decomposition engine for a multi-agent coding system.
Given a user request, break it into parallel sub-tasks for specialised agents.

Available specialist roles:
- architect  : Plans structure, creates scaffolding and configuration files
- coder      : Writes implementation code, components, logic (uses Qwen2.5 Coder)
- reviewer   : Reviews code quality, finds bugs, applies fixes (uses Mistral)
- terminal   : Runs commands, installs packages, executes tests
- researcher : Reads existing code, searches patterns, gathers context — never modifies files

Rules:
1. Each sub-task should be independent enough to run in parallel
2. Mark dependent tasks clearly in their description
3. Keep sub-tasks focused — one clear objective each
4. For small requests, 1-2 sub-tasks. For large ones, up to 6.
5. Always pick the best-suited role

Output a JSON array ONLY — no markdown, no explanation:
[
  {"role": "architect", "description": "Create project structure with src/, tests/, package.json"},
  {"role": "coder",     "description": "Implement the main App component in src/App.tsx"},
  {"role": "terminal",  "description": "Install npm packages: react-router-dom axios"}
]"""


@dataclass
class SubTask:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    role: str = "coder"
    description: str = ""
    status: str = "pending"   # pending | running | done | error
    result: str = ""
    files_changed: list[str] = field(default_factory=list)
    turns_used: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    nvidia_model: str = "coder"

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "role":          self.role,
            "description":   self.description,
            "status":        self.status,
            "result":        self.result[:500],
            "files_changed": self.files_changed,
            "turns_used":    self.turns_used,
            "elapsed_ms":    round((self.finished_at - self.started_at) * 1000)
                             if self.finished_at else 0,
        }


def decompose_task(prompt: str) -> list[SubTask]:
    """Use DeepSeek R1 to decompose a coding prompt into parallel sub-tasks."""
    response = nvidia_call(
        "decomposer",
        messages=[
            {"role": "system", "content": DECOMPOSE_PROMPT},
            {"role": "user",   "content": f"User request:\n{prompt}\n\nDecompose into sub-tasks (JSON array):"},
        ],
        temperature=0.2,
        desired_tokens=1024,
        stream=False,
    )

    try:
        json_match = re.search(r'\[[\s\S]*?\]', response)
        if json_match:
            tasks_data = json.loads(json_match.group())
            tasks = []
            for td in tasks_data:
                role = td.get("role", "coder")
                if role not in SPECIALIST_ROLES:
                    role = "coder"
                tasks.append(SubTask(
                    role=role,
                    description=td.get("description", prompt),
                    nvidia_model=SPECIALIST_ROLES[role]["nvidia_model"],
                ))
            if tasks:
                return tasks
    except (json.JSONDecodeError, KeyError):
        pass
    return [SubTask(role="coder", description=prompt, nvidia_model="coder")]


# ══════════════════════════════════════════════════════════════════════════════
# FILE LOCK MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class FileLockManager:
    """Prevent two coding agents from writing the same file simultaneously."""

    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._master = threading.Lock()

    def acquire(self, path: str) -> threading.Lock:
        with self._master:
            if path not in self._locks:
                self._locks[path] = threading.Lock()
            lock = self._locks[path]
        lock.acquire()
        return lock

    def release(self, path: str):
        with self._master:
            lock = self._locks.get(path)
            if lock and lock.locked():
                lock.release()


# ══════════════════════════════════════════════════════════════════════════════
# SPECIALIST AGENT  (coding swarm worker)
# ══════════════════════════════════════════════════════════════════════════════

class SpecialistAgent:
    """
    Executes a single SubTask with role-specific system prompt and tool access.
    Powered by its assigned NVIDIA model via the nvidia_call() helper.
    Yields structured SSE events tagged with agent_id and role.
    """

    def __init__(self, task: SubTask, file_locks: FileLockManager):
        self.task = task
        self.file_locks = file_locks
        self.role_cfg = SPECIALIST_ROLES.get(task.role, SPECIALIST_ROLES["coder"])
        self._stop = threading.Event()

    def request_stop(self):
        self._stop.set()

    def run(self) -> Generator[dict, None, None]:
        task = self.task
        role = self.role_cfg
        agent_id = task.id
        model_key = task.nvidia_model
        model_id, _ = MODELS[model_key]
        max_turns = min(MAX_AGENT_TURNS, 15)

        # Build system prompt: base + role suffix + workspace map
        workspace = _get_workspace_map() if _INTERNAL_TOOLS_AVAILABLE else str(Path.cwd())
        system = (
            SYSTEM_PROMPT
            + role["system_suffix"]
            + f"\n\n## WORKSPACE MAP\n<workspace>\n{workspace}\n</workspace>"
            + f"\n\n## YOUR MODEL\nYou are powered by {model_id}.\n"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": task.description},
        ]

        task.status = "running"
        task.started_at = time.time()
        files_changed: list[str] = []

        yield {
            "type":        "agent_start",
            "agent_id":    agent_id,
            "role":        task.role,
            "role_name":   role["name"],
            "emoji":       role["emoji"],
            "color":       role["color"],
            "model_id":    model_id,
            "description": task.description,
        }

        for turn in range(max_turns):
            if self._stop.is_set():
                break

            yield {
                "type":     "agent_thinking",
                "agent_id": agent_id,
                "role":     task.role,
                "text":     f"Turn {turn + 1}/{max_turns} [{model_id}]",
            }

            # Stream from NVIDIA
            response_text = nvidia_call(
                model_key,
                messages=messages,
                temperature=0.7,
                desired_tokens=4096,
                stream=True,
            )

            if not response_text or response_text.startswith("[") and "error" in response_text:
                yield {
                    "type":     "agent_error",
                    "agent_id": agent_id,
                    "role":     task.role,
                    "message":  response_text,
                }
                break

            # Emit clean prose
            clean = _strip_tool_lines(response_text)
            clean = re.sub(r"PLAN:[\s\S]+?(?=\n\n|\Z)", "", clean).strip()
            if clean:
                yield {
                    "type":     "agent_token",
                    "agent_id": agent_id,
                    "role":     task.role,
                    "text":     clean,
                }

            plan = _extract_plan(response_text)
            if plan and turn == 0:
                yield {
                    "type":     "agent_plan",
                    "agent_id": agent_id,
                    "role":     task.role,
                    "text":     plan,
                }

            # Done check
            if _detect_done(response_text) and not _parse_all_tool_calls(response_text):
                task.turns_used = turn + 1
                task.result = response_text[:RESULT_TRIM_CHARS]
                break

            # Parse & execute tool calls
            tool_calls = _parse_all_tool_calls(response_text)
            if not tool_calls:
                task.turns_used = turn + 1
                task.result = response_text[:RESULT_TRIM_CHARS]
                break

            messages.append({"role": "assistant", "content": response_text})
            combined_results = []

            for tool_name, payload in tool_calls:
                payload = _clean_payload(payload)

                # Role-based tool gating
                if tool_name not in role["tools"]:
                    msg = f"Tool {tool_name} not available for {role['name']} agent."
                    combined_results.append(f"[Tool Result — {tool_name}]\n{msg}")
                    continue

                yield {
                    "type":     "agent_tool",
                    "agent_id": agent_id,
                    "role":     task.role,
                    "tool":     tool_name,
                    "summary":  payload[:100],
                }

                # File locking for write tools
                lock = None
                if tool_name in {"FileEditTool", "FilePatchTool", "FileDeleteTool"}:
                    path = payload.split(":::", 1)[0].strip()
                    if path:
                        lock = self.file_locks.acquire(path)
                        if path not in files_changed:
                            files_changed.append(path)

                t0 = time.time()
                try:
                    result_str = _execute_tool(tool_name, payload)
                except Exception as exc:
                    result_str = f"Tool error: {exc}"
                elapsed = round((time.time() - t0) * 1000)

                if lock and tool_name in {"FileEditTool", "FilePatchTool", "FileDeleteTool"}:
                    path = payload.split(":::", 1)[0].strip()
                    self.file_locks.release(path)

                success = not _is_error(result_str)
                trimmed = result_str[:RESULT_TRIM_CHARS] + ("…" if len(result_str) > RESULT_TRIM_CHARS else "")

                yield {
                    "type":           "agent_tool_result",
                    "agent_id":       agent_id,
                    "role":           task.role,
                    "tool":           tool_name,
                    "success":        success,
                    "elapsed_ms":     elapsed,
                    "result_preview": trimmed[:200],
                }
                combined_results.append(f"[Tool Result — {tool_name}]\n{trimmed}")

            messages.append({"role": "user", "content": "\n\n".join(combined_results)})
            task.turns_used = turn + 1

        task.status = "done"
        task.finished_at = time.time()
        task.files_changed = files_changed

        yield {
            "type":          "agent_done",
            "agent_id":      agent_id,
            "role":          task.role,
            "role_name":     role["name"],
            "model_id":      model_id,
            "turns_used":    task.turns_used,
            "files_changed": files_changed,
            "elapsed_ms":    round((task.finished_at - task.started_at) * 1000),
        }


# ══════════════════════════════════════════════════════════════════════════════
# NVIDIA CONVERSATION AGENT  (used in CONVERSATION FLOW)
# ══════════════════════════════════════════════════════════════════════════════

def run_nvidia_agent(
    agent_key: str,
    user_prompt: str,
    context: str = "",
    conversation_history: list | None = None,
    event_queue: list | None = None,
    queue_lock: threading.Lock | None = None,
) -> str:
    """
    Execute a single NVIDIA agent (conversation mode).
    Pushes SSE events into event_queue if provided.
    """
    model_id, _ = MODELS[agent_key]
    persona = NVIDIA_PERSONAS.get(agent_key, NVIDIA_PERSONAS["balanced"])
    agent_id = uuid.uuid4().hex[:8]

    def _emit(event: dict):
        if event_queue is not None and queue_lock is not None:
            with queue_lock:
                event_queue.append(event)
        else:
            print(f"[{agent_key}] {event.get('type','event')}")

    _emit({
        "type":       "agent_start",
        "agent_id":   agent_id,
        "role":       agent_key,
        "role_name":  agent_key.replace("_", " ").title(),
        "model_id":   model_id,
        "description": user_prompt[:120],
    })

    messages: list[dict] = [{"role": "system", "content": persona}]

    if context:
        messages.append({
            "role":    "system",
            "content": f"[Context from parallel agents — use if relevant]:\n{context[:2000]}",
        })

    if conversation_history:
        messages.extend(conversation_history[-6:])   # last 3 turns

    messages.append({"role": "user", "content": user_prompt})

    t0 = time.time()
    result = nvidia_call(
        agent_key,
        messages=messages,
        temperature=0.7,
        desired_tokens=4096,
        stream=False,
    )
    elapsed = round((time.time() - t0) * 1000)

    _emit({
        "type":       "agent_token",
        "agent_id":   agent_id,
        "role":       agent_key,
        "text":       result,
    })
    _emit({
        "type":       "agent_done",
        "agent_id":   agent_id,
        "role":       agent_key,
        "model_id":   model_id,
        "elapsed_ms": elapsed,
    })

    return result


def run_nvidia_agents_parallel(
    agents: list[str],
    user_prompt: str,
    conversation_history: list | None = None,
    event_queue: list | None = None,
    queue_lock: threading.Lock | None = None,
) -> dict[str, str]:
    """Run multiple NVIDIA agents simultaneously."""
    results: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(len(agents), 5)) as executor:
        futures = {
            executor.submit(
                run_nvidia_agent,
                agent,
                user_prompt,
                "",
                conversation_history,
                event_queue,
                queue_lock,
            ): agent
            for agent in agents
        }
        for future in as_completed(futures):
            agent = futures[future]
            try:
                results[agent] = future.result()
            except Exception as exc:
                results[agent] = f"[{agent} failed: {exc}]"

    return results


# ══════════════════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class UltraMultiAgentOrchestrator:
    """
    The top-level orchestrator combining both flows:

    CONVERSATION FLOW  (Q&A, writing, reasoning, analysis)
        detect_mode → route_task → run_nvidia_agents_parallel → aggregate_responses

    CODING FLOW  (software projects, file editing, multi-file tasks)
        detect_mode → decompose_task → SpecialistAgent swarm (parallel, file-locked) → aggregate

    Usage:
        orch = UltraMultiAgentOrchestrator()
        for event in orch.run("Build a FastAPI backend with SQLite"):
            process(event)

        # Or single-turn:
        final = orch.chat("Explain quantum entanglement")
    """

    def __init__(self, max_parallel: int = 4, history_compress_every: int = 10):
        self.max_parallel = max_parallel
        self.history_compress_every = history_compress_every
        self.conversation_history: list[dict] = []
        self.turn_count = 0
        self._stop = threading.Event()
        self._specialist_agents: list[SpecialistAgent] = []
        self.file_locks = FileLockManager()

    def request_stop(self):
        self._stop.set()
        for a in self._specialist_agents:
            a.request_stop()

    def reset(self):
        self.conversation_history = []
        self.turn_count = 0
        self._stop.clear()
        print("🔄 Conversation history cleared.")

    # ──────────────────────────────────────────────────────────────────────────
    # PUBLIC: SSE GENERATOR
    # ──────────────────────────────────────────────────────────────────────────

    def run(self, user_prompt: str) -> Generator[dict, None, None]:
        """
        Main entry point. Yields SSE-style event dicts throughout execution.
        Callers can forward these directly to an HTTP SSE response or process locally.
        """
        self._stop.clear()
        self.turn_count += 1

        yield {
            "type":   "orchestrator_start",
            "turn":   self.turn_count,
            "prompt": user_prompt[:200],
        }

        # ── History compression ───────────────────────────────────────────────
        if (
            self.turn_count % self.history_compress_every == 0
            and self.conversation_history
        ):
            yield {"type": "orchestrator_phase", "phase": "compressing_history"}
            self.conversation_history = compress_history(self.conversation_history)

        # ── Mode detection ────────────────────────────────────────────────────
        yield {"type": "orchestrator_phase", "phase": "detecting_mode",
               "text": "Detecting optimal execution mode…"}
        mode = detect_mode(user_prompt)
        yield {"type": "orchestrator_mode", "mode": mode}

        final_text = ""

        if mode == "coding":
            yield from self._coding_flow(user_prompt)
        else:
            yield from self._conversation_flow(user_prompt)

        # We need the final text for history — pull it from the last done event
        # (callers should track orchestrator_done for the full result)

    # ──────────────────────────────────────────────────────────────────────────
    # CONVERSATION FLOW
    # ──────────────────────────────────────────────────────────────────────────

    def _conversation_flow(self, user_prompt: str) -> Generator[dict, None, None]:
        # Phase 1: Route
        yield {"type": "orchestrator_phase", "phase": "routing",
               "text": "Selecting optimal NVIDIA agents…"}
        agents = route_task(user_prompt)
        yield {"type": "orchestrator_routing", "agents": agents}

        # Phase 2: Parallel execution
        yield {"type": "orchestrator_phase", "phase": "executing",
               "text": f"Launching {len(agents)} NVIDIA agent(s) in parallel…"}

        event_queue: list[dict] = []
        queue_lock = threading.Lock()
        running_count = [len(agents)]
        all_done_event = threading.Event()
        results_holder: dict[str, str] = {}

        def run_and_collect(agent_key: str):
            result = run_nvidia_agent(
                agent_key,
                user_prompt,
                conversation_history=self.conversation_history,
                event_queue=event_queue,
                queue_lock=queue_lock,
            )
            with queue_lock:
                results_holder[agent_key] = result
                running_count[0] -= 1
                if running_count[0] <= 0:
                    all_done_event.set()

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            for a in agents:
                pool.submit(run_and_collect, a)

            sent = 0
            while not all_done_event.is_set() or sent < len(event_queue):
                with queue_lock:
                    batch = event_queue[sent:]
                    sent = len(event_queue)
                yield from batch
                if not all_done_event.is_set():
                    all_done_event.wait(timeout=0.15)

            with queue_lock:
                for evt in event_queue[sent:]:
                    yield evt

        # Phase 3: Aggregate
        if len(results_holder) == 1:
            final_text = next(iter(results_holder.values()))
            yield {"type": "orchestrator_phase", "phase": "single_agent_done"}
        else:
            yield {"type": "orchestrator_phase", "phase": "aggregating",
                   "text": f"Aggregating {len(results_holder)} agent outputs…"}
            agg_final = ""
            for evt in aggregate_responses(user_prompt, results_holder):
                yield evt
                if evt["type"] == "aggregator_done":
                    agg_final = evt.get("text", "")
            final_text = agg_final

        # Update history
        self.conversation_history.append({"role": "user",      "content": user_prompt})
        self.conversation_history.append({"role": "assistant",  "content": final_text[:2000]})

        yield {
            "type":   "orchestrator_done",
            "mode":   "conversation",
            "agents": list(results_holder.keys()),
            "text":   final_text,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # CODING FLOW
    # ──────────────────────────────────────────────────────────────────────────

    def _coding_flow(self, user_prompt: str) -> Generator[dict, None, None]:
        # Phase 1: Decompose
        yield {"type": "orchestrator_phase", "phase": "decomposing",
               "text": "DeepSeek R1 decomposing task into parallel sub-tasks…"}
        subtasks = decompose_task(user_prompt)
        yield {
            "type":         "orchestrator_plan",
            "tasks":        [t.to_dict() for t in subtasks],
            "total_agents": len(subtasks),
        }

        # Phase 2: Create specialist agents
        self._specialist_agents = [
            SpecialistAgent(task, self.file_locks) for task in subtasks
        ]

        yield {"type": "orchestrator_phase", "phase": "executing",
               "text": f"Launching {len(self._specialist_agents)} specialist agents…"}

        event_queue: list[dict] = []
        queue_lock = threading.Lock()
        running_count = [len(self._specialist_agents)]
        all_done_event = threading.Event()
        agent_results: dict[str, str] = {}

        def run_specialist(agent: SpecialistAgent):
            try:
                for event in agent.run():
                    if self._stop.is_set():
                        break
                    with queue_lock:
                        event_queue.append(event)
                    if event["type"] == "agent_done":
                        with queue_lock:
                            agent_results[agent.task.role + "_" + agent.task.id] = (
                                agent.task.result or event.get("text", "")
                            )
            except Exception as exc:
                with queue_lock:
                    event_queue.append({
                        "type":     "agent_error",
                        "agent_id": agent.task.id,
                        "role":     agent.task.role,
                        "message":  str(exc),
                    })
            finally:
                with queue_lock:
                    running_count[0] -= 1
                    if running_count[0] <= 0:
                        all_done_event.set()

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            for agent in self._specialist_agents:
                pool.submit(run_specialist, agent)

            sent = 0
            while not all_done_event.is_set() or sent < len(event_queue):
                with queue_lock:
                    batch = event_queue[sent:]
                    sent = len(event_queue)
                yield from batch
                if not all_done_event.is_set():
                    all_done_event.wait(timeout=0.15)

            with queue_lock:
                for evt in event_queue[sent:]:
                    yield evt

        # Phase 3: Aggregate specialist outputs
        all_files: list[str] = []
        total_turns = 0
        for task in subtasks:
            all_files.extend(task.files_changed)
            total_turns += task.turns_used

        final_text = ""
        if agent_results:
            yield {"type": "orchestrator_phase", "phase": "aggregating",
                   "text": f"Aggregating {len(agent_results)} specialist outputs…"}
            for evt in aggregate_responses(user_prompt, agent_results):
                yield evt
                if evt["type"] == "aggregator_done":
                    final_text = evt.get("text", "")

        # Update history
        self.conversation_history.append({"role": "user",     "content": user_prompt})
        self.conversation_history.append({"role": "assistant", "content": final_text[:2000]})

        yield {
            "type":               "orchestrator_done",
            "mode":               "coding",
            "total_agents":       len(subtasks),
            "total_turns":        total_turns,
            "total_files_changed": list(set(all_files)),
            "tasks":              [t.to_dict() for t in subtasks],
            "text":               final_text,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # CONVENIENCE: simple blocking chat()
    # ──────────────────────────────────────────────────────────────────────────

    def chat(self, user_prompt: str, verbose: bool = True) -> str:
        """
        Blocking wrapper around run().
        Prints live progress to stdout and returns the final text.
        """
        final = ""
        _COLORS = {
            "agent_start":      "\033[96m",     # cyan
            "agent_token":      "\033[97m",      # white
            "agent_tool":       "\033[93m",      # yellow
            "agent_done":       "\033[92m",      # green
            "orchestrator_phase": "\033[95m",    # magenta
            "aggregator_start": "\033[94m",      # blue
        }
        RESET = "\033[0m"

        for event in self.run(user_prompt):
            etype = event.get("type", "")

            if not verbose:
                if etype == "orchestrator_done":
                    final = event.get("text", "")
                continue

            color = _COLORS.get(etype, "")

            if etype == "orchestrator_start":
                print(f"\n{'╔' + '═'*68 + '╗'}")
                print(f"║  TURN {self.turn_count:<62}║")
                print(f"║  {user_prompt[:64]:<64}  ║")
                print(f"{'╚' + '═'*68 + '╝'}")

            elif etype == "orchestrator_mode":
                mode = event.get("mode", "")
                icon = "💻" if mode == "coding" else "💬"
                print(f"\n{icon}  Mode: {mode.upper()}")

            elif etype == "orchestrator_phase":
                print(f"\n{color}▶ {event.get('text', event.get('phase',''))}{RESET}")

            elif etype == "orchestrator_routing":
                print(f"  Agents: {event.get('agents', [])}")

            elif etype == "orchestrator_plan":
                tasks = event.get("tasks", [])
                print(f"\n📋 Task plan ({len(tasks)} sub-tasks):")
                for t in tasks:
                    role_cfg = SPECIALIST_ROLES.get(t["role"], {})
                    emoji = role_cfg.get("emoji", "•")
                    print(f"  {emoji} [{t['role'].upper()}] {t['description'][:80]}")

            elif etype == "agent_start":
                emoji = event.get("emoji", "🤖")
                model = event.get("model_id", "")
                print(f"\n{'─'*60}")
                print(f"{color}{emoji}  [{event.get('role_name','').upper()}]  ({model}){RESET}")
                print(f"   Task: {event.get('description','')[:80]}")

            elif etype == "agent_thinking":
                print(f"   ⏳ {event.get('text','')}", end="\r")

            elif etype == "agent_token":
                text = event.get("text", "")
                print(f"\n{text}")

            elif etype == "agent_plan":
                print(f"\n   📝 Plan: {event.get('text','')[:200]}")

            elif etype == "agent_tool":
                print(f"   {color}🔧 {event.get('tool','')} — {event.get('summary','')[:60]}{RESET}")

            elif etype == "agent_tool_result":
                ok = "✅" if event.get("success") else "❌"
                print(f"   {ok} {event.get('tool','')} ({event.get('elapsed_ms',0)}ms)")

            elif etype == "agent_error":
                print(f"\n   ❌ {event.get('role','')}: {event.get('message','')}")

            elif etype == "agent_done":
                emoji = SPECIALIST_ROLES.get(event.get("role",""), {}).get("emoji", "✓")
                print(f"\n   {color}{emoji} Done — {event.get('turns_used',0)} turns, "
                      f"{len(event.get('files_changed',[]))} files changed, "
                      f"{event.get('elapsed_ms',0)}ms{RESET}")

            elif etype == "aggregator_start":
                print(f"\n{'═'*60}")
                print(f"{color}🔗 AGGREGATING {event.get('agent_count','')} outputs…{RESET}")

            elif etype == "aggregator_done":
                final = event.get("text", "")
                print(f"\n{final}")

            elif etype == "orchestrator_done":
                final = event.get("text", final)
                mode = event.get("mode", "")
                print(f"\n{'╔' + '═'*68 + '╗'}")
                print(f"║  ✅ FINAL ANSWER  ({mode.upper()} MODE){' ' * max(0, 35-len(mode))}║")
                print(f"{'╚' + '═'*68 + '╝'}")

        return final


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

BANNER = """\
╔══════════════════════════════════════════════════════════════════════════════════╗
║     ULTRA MULTI-AGENT ORCHESTRATOR — NVIDIA × SPECIALIST SWARM                 ║
║                                                                                  ║
║  NVIDIA Models:                                                                  ║
║  ⚡ Llama 3.1 8B       → router, fast answers, mode detection                   ║
║  🧠 DeepSeek R1 70B    → thinker, deep reasoning, task decomposition            ║
║  💻 Qwen2.5 Coder 32B  → coder agent, code generation                           ║
║  💻 Mistral Small 4    → coder_heavy, complex coding, reviewer                  ║
║  ⚡ Llama 3.3 70B      → balanced, aggregator, architect                        ║
║  🚀 Nemotron Super 49B → powerful complex tasks                                  ║
║  🏢 Nemotron 120B      → enterprise reasoning                                    ║
║  📄 MiniMax M2.5       → long context (1 million tokens!)                       ║
║  ⚡ Phi-4 Mini         → compact tasks, history compression                      ║
║  ✍️  Gemma 3 12B        → writer, summaries, creative writing                    ║
║                                                                                  ║
║  Specialist Swarm (coding mode):                                                  ║
║  🏗️  Architect  |  ⚡ Coder  |  🔍 Reviewer  |  💻 Terminal  |  📚 Researcher   ║
║                                                                                  ║
║  Commands: 'exit' | 'reset' | 'mode' to see current state                       ║
╚══════════════════════════════════════════════════════════════════════════════════╝
"""


def main():
    print(BANNER)
    orch = UltraMultiAgentOrchestrator(max_parallel=4)

    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            if not user_input:
                continue

            if user_input.lower() == "exit":
                print("👋 Goodbye!")
                break
            elif user_input.lower() == "reset":
                orch.reset()
                continue
            elif user_input.lower() == "mode":
                print(f"   Turn: {orch.turn_count}  |  "
                      f"History turns: {len(orch.conversation_history) // 2}")
                continue

            orch.chat(user_input, verbose=True)

        except KeyboardInterrupt:
            print("\n\n👋 Interrupted. Goodbye!")
            break
        except Exception as exc:
            print(f"\n❌ Unexpected error: {exc}")


if __name__ == "__main__":
    main()