"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         ClawAgent v2 — Elite Autonomous Coding Agent                        ║
║                                                                              ║
║  ARCHITECTURE                                                                ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  • Plan → Execute → Verify → Done structured loop                           ║
║  • Multi-tool parsing per turn (executes first, queues rest)                ║
║  • Auto-retry with LLM correction on tool failure (up to MAX_RETRIES)       ║
║  • RollbackRegistry — per-session file snapshots with full undo             ║
║  • LoopDetector — escapes stuck repetitive cycles automatically             ║
║  • ChangeTracker — SHA-256 file diff for regression detection               ║
║  • Context compression — LLM-powered summarisation for long sessions        ║
║  • ThinkTool — internal reasoning scratchpad with zero side effects         ║
║  • Task classifier — routes turn_type for smart model selection             ║
║  • Consecutive-error breaker — halts runaway failure cascades               ║
║  • Smart model routing — per-turn model selection based on last tool used   ║
║  • Streamed SSE events with granular phase indicators                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

from .llm import LLMClient, DEFAULT_MODEL, get_all_models, SMART_MODELS
from .toolbox import (
    tool_bash_run,
    tool_file_read,
    tool_file_edit,
    tool_file_patch,
    tool_file_delete,
    tool_list_dir,
    tool_search,
    tool_view_file_lines,
    tool_workspace_zip,
    tool_workspace_unzip,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

MAX_AGENT_TURNS        = 16    # Hard ceiling — forces concise, plan-driven execution
MAX_HISTORY_PAIRS      = 30    # Conversation pairs kept across sessions
MAX_RETRIES            = 2     # Per-tool retry limit — fail fast, don't burn tokens
LOOP_DETECT_WINDOW     = 3     # Catch repetition early (3 identical calls = looping)
CONTEXT_COMPRESS_AT    = 12    # Compress history proactively to save context
MAX_CONSECUTIVE_ERRORS = 3     # Stop quickly on cascading failures
RESULT_TRIM_CHARS      = 2_000 # Tool output trimmed to this in history
NO_PROGRESS_TURNS      = 4     # Halt if no file changes after this many turns


# ═══════════════════════════════════════════════════════════════════════════════
# TASK MODES
# ═══════════════════════════════════════════════════════════════════════════════

# Canonical set of allowed specialist mode keys.
# Used to validate mode_override inputs across all agents.
VALID_MODES: frozenset[str] = frozenset(
    {"builder", "debugger", "refactorer", "researcher", "reviewer"}
)

TASK_MODES: dict[str, dict] = {
    "builder": {
        "emoji":   "🏗",
        "label":   "Builder",
        "desc":    "Creating new features, files, or applications from scratch",
        "hint":    "Focus on clean architecture, sensible defaults, and complete implementations.",
    },
    "debugger": {
        "emoji":   "🔍",
        "label":   "Debugger",
        "desc":    "Investigating and fixing errors, crashes, or unexpected behaviour",
        "hint":    "Read error messages carefully. Reproduce first, isolate root cause, then fix.",
    },
    "refactorer": {
        "emoji":   "♻",
        "label":   "Refactorer",
        "desc":    "Improving code quality, structure, or performance without changing behaviour",
        "hint":    "Preserve behaviour. Improve readability, remove duplication, apply best practices.",
    },
    "researcher": {
        "emoji":   "📚",
        "label":   "Researcher",
        "desc":    "Reading, understanding, and explaining code or concepts",
        "hint":    "Be thorough and precise. Cite exact file/line references. Summarise clearly.",
    },
    "reviewer": {
        "emoji":   "👁",
        "label":   "Reviewer",
        "desc":    "Auditing code for bugs, security issues, or style violations",
        "hint":    "Be direct and specific. Rate severity (Critical/High/Medium/Low). Suggest fixes.",
    },
}

# Per-mode execution policies — full contract per specialist mode:
#   max_turns       : hard ceiling on agent iterations
#   turn_type       : routing key passed to LLMClient.route() for model selection
#   preferred_model : preferred NVIDIA model key (None = use configured model)
#   tool_priority   : ordered guidance list for the mode's system-prompt addendum
#                     (INFORMATIONAL — surfaced in prompt/docs, not enforced in execution)
#   read_only       : when True, write/exec tools are blocked at execution time
_MODE_POLICIES: dict[str, dict] = {
    "builder": {
        "max_turns":       16,
        "turn_type":       "coding",
        "preferred_model": "nvidia:qwen2.5-coder-32b",
        "tool_priority":   ["FileEditTool", "BashTool", "FilePatchTool",
                            "FileReadTool", "ListDirTool"],
        "read_only":       False,
    },
    "debugger": {
        "max_turns":       12,
        "turn_type":       "debugging",
        "preferred_model": "nvidia:llama-3.3-70b-instruct",
        "tool_priority":   ["BashTool", "FileReadTool", "SearchTool",
                            "FileEditTool", "ListDirTool"],
        "read_only":       False,
    },
    "refactorer": {
        "max_turns":       10,
        "turn_type":       "coding",
        "preferred_model": "nvidia:nemotron-super-49b",
        "tool_priority":   ["FileReadTool", "FilePatchTool", "FileEditTool",
                            "ListDirTool", "SearchTool"],
        "read_only":       False,
    },
    "researcher": {
        "max_turns":       8,
        "turn_type":       "thinking",
        "preferred_model": "nvidia:deepseek-r1-distill-llama-70b",
        "tool_priority":   ["SearchTool", "FileReadTool", "ListDirTool",
                            "ViewLinesTool"],
        "read_only":       True,
    },
    "reviewer": {
        "max_turns":       8,
        "turn_type":       "thinking",
        "preferred_model": "nvidia:deepseek-r1-distill-llama-70b",
        "tool_priority":   ["FileReadTool", "SearchTool", "ListDirTool",
                            "ViewLinesTool"],
        "read_only":       True,
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are NEXUS — a senior autonomous full-stack developer agent embedded inside \
NEXUS IDE. You are efficient and precise. You plan once, execute each step \
exactly once, and finish. You never repeat yourself or revisit completed work.

Your sandbox is 'agent_workspace/'. You have full read/write/exec access to \
every file and directory inside it. Treat it as a real production environment.

══════════════════════════════════════════
 CRITICAL: EFFICIENCY RULES
══════════════════════════════════════════
• You have a LIMITED number of turns. Every turn costs tokens. Be surgical.
• NEVER re-read a file you already read this session.
• NEVER re-edit a file you already edited unless a verification step found a bug.
• NEVER repeat the same tool call twice with the same arguments.
• If a tool call fails, analyse the error and try a DIFFERENT approach — \
  do NOT retry the exact same thing.
• Combine related work: if you need to create 3 files, plan all 3 upfront \
  and execute them in sequence without re-planning.
• When the task is done, STOP. Do not add unnecessary extras or loop back.

══════════════════════════════════════════
 OPERATING PROCEDURE
══════════════════════════════════════════

── PHASE 1: ORIENT (1 turn) ─────────────
Quickly assess the workspace:
• List the workspace root: TOOL: ListDirTool | .
• If SESSION MEMORY (.memory.md) exists, read it first — it has project context.
• Read only the files directly relevant to the task.

── PHASE 2: PLAN (1 turn) ──────────────
Output a numbered plan. Keep it short — max 8 steps.
Use this exact format (parsed by the IDE):

PLAN:
1. [STEP] <concise action>
2. [STEP] <concise action>
...

Approach: <1–2 sentences — why this plan>

Each step = one tool call. Plan once, then execute. Do NOT re-plan mid-task \
unless you discover something that fundamentally changes the approach.

── PHASE 3: EXECUTE ─────────────────────
Follow your plan step by step. One tool call per turn.
After each tool result, write ONE brief line about the outcome, then proceed.

Rules:
• Read before write. Always.
• Use FilePatchTool for targeted edits. FileEditTool only for new files.
• If a tool result is unexpected, explain it and adapt — but do NOT start over.

── PHASE 4: VERIFY (1 turn) ────────────
After the main work is done, run ONE verification:
• TOOL: BashTool | python -c "import <module>" OR run tests.
• If it passes, proceed to DONE. If it fails, fix the specific issue (1 turn).

── PHASE 5: UPDATE MEMORY ───────────────
Before finishing, update '.memory.md' so future sessions have context:

TOOL: FileEditTool | .memory.md ::: <content>

The memory file captures:
  # Project Memory
  Updated: <date>
  ## What exists
  <files / stack / architecture>
  ## What was done last
  <2–3 bullets>
  ## User preferences
  <style, patterns>
  ## Known issues / next steps
  <what's left>

── PHASE 6: DONE ────────────────────────
When the task is complete, end with a DONE block:

DONE:
Summary: <one clear sentence — what was built/fixed>
Why it works: <1–2 sentences explaining the key decision or technique used>
Files changed:
  • <file> — <what changed and why>
Verified: <exactly how you confirmed it works — command output or test result>
What you can do next: <1–3 concrete follow-up ideas>

══════════════════════════════════════════
 COMMUNICATION RULES
══════════════════════════════════════════
• Skip filler phrases ("Sure!", "Great question!", "I'll help you with that").
• After every tool result, interpret it — never silently move on.
• Explain WHY each choice was made, not just WHAT you did.
• When you hit an error, name it precisely, explain its cause, then fix it.
• Use plain language — the user may not know the internals. Teach as you go.
• Surface all trade-offs: if there are two ways to do something, say which \
  you chose and why.

══════════════════════════════════════════
 TOOLS (exact format required)
══════════════════════════════════════════
TOOL: ListDirTool       | <path>
TOOL: FileReadTool      | <path>
TOOL: ViewFileLinesTool | <path> ::: <start_line>,<end_line>
TOOL: SearchTool        | <path> ::: <regex_pattern>
TOOL: FileEditTool      | <path> ::: <full file content>
TOOL: FilePatchTool     | <path> ::: <exact old text> === <replacement text>
TOOL: FileDeleteTool    | <path>
TOOL: BashTool          | <shell command>
TOOL: ThinkTool         | <internal reasoning — no side effects>
TOOL: WorkspaceZipTool  | <backup_name.zip>
TOOL: WorkspaceUnzipTool| <backup_name.zip>

══════════════════════════════════════════
 EDITING RULES (non-negotiable)
══════════════════════════════════════════
• Read before write. ALWAYS.
• Prefer FilePatchTool for any change smaller than the whole file.
• Match exact indentation and whitespace when patching.
• Never write placeholder comments like "# TODO: implement this".
• Every function/class you write must be complete and functional.

══════════════════════════════════════════
 AGENT KNOWLEDGE BASE
══════════════════════════════════════════
• AGENT KNOWLEDGE BASE is injected from .knowledge.md at session start.
  It is your built-in expert handbook — compiled from 30 specialist agents,
  135 production skills, and 10+ months of battle-tested engineering patterns.
  It covers: core principles, specialist role playbooks (Architect, Planner,
  Code Reviewer, Build-Error Resolver, Security Reviewer, Debugger), Backend /
  Frontend / Data architectural patterns, language-specific rules for Python /
  JS / TS / Rust / Go / Shell, code review examples (good vs bad), and a
  pre-flight done checklist.
• Consult it before starting any task: am I using the right role? Right pattern?
  Meeting code-quality thresholds? Following security rules?
• Security rules in the knowledge base are HARD constraints — never override them.
• Never hardcode secrets, keys, or tokens. Never log sensitive data.
• Never skip tests: write or verify tests before shipping any code change.

══════════════════════════════════════════
 LEARNING FROM HISTORY
══════════════════════════════════════════
• If SESSION MEMORY is present in the context, treat it as ground truth about \
  the project state. Refer to it when making decisions.
• If the user's request contradicts past patterns, flag the inconsistency and \
  ask before proceeding.
• Learn the user's preferred style from past interactions and apply it \
  automatically (indentation, naming, frameworks, etc.).

Think deeply. Explain clearly. Deliver working code.\
"""

# ── Mode-specific system prompt addendums ──────────────────────────────────────
_MODE_ADDENDUMS: dict[str, str] = {
    "builder": (
        "\n\n══ ACTIVE MODE: BUILDER 🏗 ══\n"
        "You are building something new. Prioritise:\n"
        "• Complete, working implementations over stubs\n"
        "• Sensible project structure and naming conventions\n"
        "• Dependency management (requirements.txt, package.json, etc.)\n"
        "• At minimum a README.md describing what was built and how to run it\n"
    ),
    "debugger": (
        "\n\n══ ACTIVE MODE: DEBUGGER 🔍 ══\n"
        "You are hunting and fixing a bug. Protocol:\n"
        "1. REPRODUCE: confirm the bug exists and understand the exact symptom\n"
        "2. ISOLATE: find the root cause — trace the call stack, read error messages carefully\n"
        "3. FIX: make the minimal targeted change to fix root cause, not symptoms\n"
        "4. VERIFY: confirm the bug is gone and nothing else broke\n"
        "Never mask errors with try/except without handling them properly.\n"
    ),
    "refactorer": (
        "\n\n══ ACTIVE MODE: REFACTORER ♻ ══\n"
        "You are improving code quality without changing behaviour. Rules:\n"
        "• Preserve ALL existing behaviour — run tests before and after\n"
        "• Apply DRY, SOLID, KISS principles as appropriate\n"
        "• Improve naming, reduce complexity, eliminate dead code\n"
        "• Document non-obvious decisions inline\n"
    ),
    "researcher": (
        "\n\n══ ACTIVE MODE: RESEARCHER 📚 ══\n"
        "You are reading and explaining code or concepts. Standards:\n"
        "• Read every relevant file before forming conclusions\n"
        "• Cite exact file paths and line numbers in your explanations\n"
        "• Structure your response: Overview → Details → Examples → Summary\n"
        "• Be honest about uncertainty — say 'I'm not sure' rather than guess\n"
    ),
    "reviewer": (
        "\n\n══ ACTIVE MODE: REVIEWER 👁 ══\n"
        "You are auditing code for quality, bugs, and security. Format:\n"
        "• Rate each finding: 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low\n"
        "• Include: file, line(s), issue description, concrete fix suggestion\n"
        "• Check: correctness, security, performance, style, test coverage\n"
        "• End with: an overall grade (A–F) and a 1-paragraph summary\n"
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# TASK CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════════

def classify_task(prompt: str) -> str:
    """
    Classify the user's intent for smart-model routing.

    Returns one of: "debugging" | "coding" | "thinking" | "default"
    """
    p = prompt.lower()
    if any(w in p for w in ["debug", "fix", "error", "bug", "broken", "crash", "fail"]):
        return "debugging"
    if any(w in p for w in ["create", "build", "make", "write", "add", "generate",
                              "refactor", "improve", "optimise", "optimize", "clean"]):
        return "coding"
    if any(w in p for w in ["read", "explain", "what", "why", "how", "describe",
                              "analyse", "analyze", "review", "summarise", "summarize"]):
        return "thinking"
    return "default"


def detect_mode(prompt: str) -> str:
    """
    Detect the specialist mode from the prompt text or an explicit prefix.

    Explicit prefix: '@debugger fix the login bug' → 'debugger'
    Keyword detection fallback.

    Returns one of: "builder" | "debugger" | "refactorer" | "researcher" | "reviewer" | ""
    """
    p = prompt.strip()
    # Explicit @mode prefix
    m = re.match(r"^@(\w+)\b", p)
    if m:
        mode = m.group(1).lower()
        if mode in TASK_MODES:
            return mode

    pl = p.lower()
    if any(w in pl for w in ["review", "audit", "check quality", "code review"]):
        return "reviewer"
    if any(w in pl for w in ["refactor", "clean up", "restructure", "reorganise", "reorganize", "improve code"]):
        return "refactorer"
    if any(w in pl for w in ["debug", "fix bug", "fix the error", "traceback", "crash", "exception", "broken"]):
        return "debugger"
    if any(w in pl for w in ["research", "explain", "what does", "how does", "describe", "analyse", "analyze"]):
        return "researcher"
    if any(w in pl for w in ["build", "create", "make", "implement", "generate", "write a", "write the", "add feature"]):
        return "builder"
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ROLLBACK REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

class RollbackRegistry:
    """
    Snapshot file contents before any destructive tool runs.
    Enables full per-session undo on demand.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, str | None] = {}

    def snapshot(self, path: str) -> None:
        """Capture current content of a file. No-op if already snapshotted."""
        if path in self._snapshots:
            return
        try:
            self._snapshots[path] = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            self._snapshots[path] = None  # file is new — rollback = delete

    def rollback(self, path: str) -> str:
        original = self._snapshots.get(path)
        p = Path(path)
        try:
            if original is None:
                p.unlink(missing_ok=True)
                return f"Rolled back: deleted newly-created {path}"
            p.write_text(original, encoding="utf-8")
            return f"Rolled back: restored {path} to original state"
        except Exception as exc:
            return f"Rollback failed for {path}: {exc}"

    def rollback_all(self) -> list[str]:
        return [self.rollback(p) for p in list(self._snapshots)]

    @property
    def has_snapshots(self) -> bool:
        return bool(self._snapshots)

    @property
    def tracked_paths(self) -> list[str]:
        return list(self._snapshots.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# LOOP DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class LoopDetector:
    """Detect when the agent is stuck repeating the same tool call."""

    def __init__(self, window: int = LOOP_DETECT_WINDOW) -> None:
        self._window  = window
        self._history: list[str] = []
        self._loop_count = 0

    def record(self, tool_name: str, payload_hash: str) -> None:
        self._history.append(f"{tool_name}:{payload_hash}")
        if len(self._history) > self._window * 3:
            self._history = self._history[-self._window * 3:]

    def is_looping(self) -> bool:
        if len(self._history) < self._window:
            return False
        recent = self._history[-self._window:]
        if len(set(recent)) <= 2:
            self._loop_count += 1
            return True
        return False

    @property
    def should_force_stop(self) -> bool:
        return self._loop_count >= 2

    def hint(self) -> str:
        if self._loop_count >= 2:
            return (
                "LOOP DETECTED TWICE: You are stuck in a cycle. STOP NOW. "
                "Emit a DONE block with what you have completed so far. "
                "Do NOT make any more tool calls."
            )
        return (
            f"LOOP DETECTED: You repeated the same call {self._window} times. "
            "STOP and try a completely different approach. If stuck, emit DONE "
            "with a summary of what was completed and what remains."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FileChange:
    path:       str
    before_sha: str | None
    after_sha:  str | None = None

    @staticmethod
    def sha(path: str) -> str | None:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
        except Exception:
            return None

    def record_after(self) -> None:
        self.after_sha = self.sha(self.path)

    @property
    def changed(self) -> bool:
        return self.before_sha != self.after_sha

    def summary(self) -> str:
        if self.before_sha is None:
            return f"{self.path} (created)"
        if self.after_sha is None:
            return f"{self.path} (deleted)"
        if self.changed:
            return f"{self.path} ({self.before_sha} → {self.after_sha})"
        return f"{self.path} (unchanged)"


class ChangeTracker:
    """Track file checksums before/after edits for regression detection."""

    def __init__(self) -> None:
        self._changes: dict[str, FileChange] = {}

    def pre(self, path: str) -> None:
        if path not in self._changes:
            self._changes[path] = FileChange(path=path, before_sha=FileChange.sha(path))

    def post(self, path: str) -> None:
        if path in self._changes:
            self._changes[path].record_after()

    def summaries(self) -> list[str]:
        return [c.summary() for c in self._changes.values() if c.changed]

    def modified_paths(self) -> list[str]:
        return [p for p, c in self._changes.items() if c.changed]


# ═══════════════════════════════════════════════════════════════════════════════
# PARAM CLEANING
# ═══════════════════════════════════════════════════════════════════════════════

_PARAM_PREFIX_RE = re.compile(
    r"^(?:path|file|filepath|directory|dir|command|cmd|query|pattern|content|input|args?)\s*[:=]\s*",
    re.IGNORECASE,
)


def _clean_payload(payload: str) -> str:
    """Strip LLM-generated parameter prefixes and wrapping from tool payloads."""
    cleaned = payload.strip()
    
    # Strip hallucinated tags like [TOOL_CALL], [/TOOL_CALL], or any <tag>
    cleaned = re.sub(r'\[/?\w+\]', '', cleaned).strip()
    cleaned = re.sub(r'</?\w+>', '', cleaned).strip()

    # If the entire payload is wrapped in backticks (e.g., `command`)
    if cleaned.startswith("`") and cleaned.endswith("`"):
        cleaned = cleaned.strip("`").strip()
        
    # Handle the case where the LLM writes: path ::: ```python\n ... \n```
    if ":::" in cleaned:
        parts = cleaned.split(":::", 1)
        p1 = parts[0].strip()
        p2 = parts[1].strip()
        # Remove leftover backticks from p1 if the LLM wrote `path` ::: ...
        p1 = p1.strip("`").strip()
        
        # Strip code blocks from p2
        if p2.startswith("```"):
            p2 = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", p2)
            if p2.endswith("```"):
                p2 = p2[:-3].strip()
        return f"{p1} ::: {p2}"

    if cleaned.startswith("{"):
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                vals = list(data.values())
                if len(vals) == 1:
                    return str(vals[0]).strip()
                if len(vals) == 2:
                    return f"{vals[0]} ::: {vals[1]}"
        except Exception:
            pass

    return _PARAM_PREFIX_RE.sub("", cleaned).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

_KNOWN_TOOLS = {
    "ListDirTool", "FileReadTool", "ViewFileLinesTool", "SearchTool",
    "FileEditTool", "FilePatchTool", "FileDeleteTool", "BashTool", "ThinkTool",
    "WorkspaceZipTool", "WorkspaceUnzipTool",
}


def _execute_tool(tool_name: str, payload: str) -> str:
    """Dispatch a single tool call and return its output string."""
    payload = _clean_payload(payload)
    try:
        if tool_name == "ThinkTool":
            return f"[Internal Reasoning]: {payload}"

        if tool_name == "ListDirTool":
            return tool_list_dir(payload or ".")

        if tool_name == "FileReadTool":
            return tool_file_read(payload)

        if tool_name == "ViewFileLinesTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                path  = parts[0].strip()
                nums  = parts[1].strip().split(",")
                start = int(nums[0].strip())
                end   = int(nums[1].strip()) if len(nums) > 1 else start + 50
                return tool_view_file_lines(path, start, end)
            return tool_file_read(payload)

        if tool_name == "SearchTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                return tool_search(parts[0].strip(), parts[1].strip())
            return tool_search(".", payload)

        if tool_name == "FileEditTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                return tool_file_edit(parts[0].strip(), parts[1])
            return "Error: FileEditTool requires 'path ::: content'."

        if tool_name == "FilePatchTool":
            p1 = payload.split(":::", 1)
            if len(p1) == 2:
                path = p1[0].strip()
                p2   = p1[1].split("===", 1)
                if len(p2) == 2:
                    return tool_file_patch(path, p2[0], p2[1])
            return "Error: FilePatchTool expects 'path ::: old === new'."

        if tool_name == "FileDeleteTool":
            return tool_file_delete(payload)

        if tool_name == "BashTool":
            return tool_bash_run(payload)

        if tool_name == "WorkspaceZipTool":
            # tool_workspace_zip() now returns bytes; we save to a file and return a message
            zip_bytes = tool_workspace_zip()
            name = payload or "workspace_backup.zip"
            from .toolbox import get_workspace_root
            dest = get_workspace_root() / name
            dest.write_bytes(zip_bytes)
            return f"Success: Workspace backed up to '{name}' ({len(zip_bytes):,} bytes)."

        if tool_name == "WorkspaceUnzipTool":
            # payload is a filename inside the workspace
            from .toolbox import get_workspace_root
            name = payload or "workspace_backup.zip"
            src = get_workspace_root() / name
            if not src.exists():
                return f"Error: '{name}' not found in workspace."
            return tool_workspace_unzip(src.read_bytes())

        return f"Unknown tool '{tool_name}'. Known: {', '.join(sorted(_KNOWN_TOOLS))}"

    except Exception as exc:
        return f"Tool error ({tool_name}): {exc}"


def _is_error(output: str) -> bool:
    low = output.lower()
    return (
        low.startswith("error")
        or low.startswith("tool error")
        or "permission denied" in low
        or "no such file" in low
        or "command not found" in low
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PARSING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_TOOL_RE = re.compile(
    r"(?:TOOL:\s*)?(\w+)\s*[|:]+\s*([\s\S]*?)(?=\n(?:TOOL:\s*)?\w+\s*[|:]|\Z)",
    re.MULTILINE,
)


def _parse_all_tool_calls(text: str) -> list[tuple[str, str]]:
    """Extract ALL tool calls from an LLM response (multi-tool support)."""
    calls = [
        (m.group(1).strip(), m.group(2).strip())
        for m in _TOOL_RE.finditer(text)
        if m.group(1).strip() in _KNOWN_TOOLS
    ]
    
    # Fallback: If no explicit tools are found, look for Markdown code blocks with filenames.
    if not calls:
        blocks = re.finditer(r"```[a-zA-Z0-9_-]*\n([\s\S]*?)```", text)
        for b in blocks:
            code = b.group(1)
            # Try to pull a filename from the first line (e.g. // path/to/file.js or # main.py)
            first_line = code.split("\n", 1)[0].strip()
            name_match = re.match(r"^(?://|#|/\*|<!--)\s*([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)", first_line)
            if name_match:
                filename = name_match.group(1)
                # Remove the first line from the code
                pure_code = code[len(first_line):].strip()
                calls.append(("FileEditTool", f"{filename} ::: {pure_code}"))

    return calls


def _parse_first_tool_call(text: str) -> tuple[str, str] | None:
    calls = _parse_all_tool_calls(text)
    return calls[0] if calls else None


def _strip_tool_lines(text: str) -> str:
    return re.sub(r"\n?TOOL:[\s\S]*", "", text).strip()


def _detect_done(text: str) -> bool:
    return bool(re.search(r"\bDONE\s*:", text, re.IGNORECASE))


def _extract_plan(text: str) -> str | None:
    m = re.search(r"PLAN:\s*([\s\S]+?)(?=\nTOOL:|\Z)", text)
    return m.group(1).strip() if m else None


def _extract_thought(text: str) -> str | None:
    m = re.search(r"<thought>([\s\S]*?)</thought>", text)
    return m.group(1).strip() if m else None


def _last_tool_used(messages: list[dict]) -> str | None:
    """Scan backwards for the most recent tool-result tag in user messages."""
    for m in reversed(messages):
        if m["role"] == "user":
            match = re.search(r"\[Tool Result — (\w+)\]", m["content"])
            if match:
                return match.group(1)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT COMPRESSOR
# ═══════════════════════════════════════════════════════════════════════════════

def _compress_history(history: list[dict], llm: LLMClient) -> list[dict]:
    """
    Summarise older turns and keep the most recent pairs verbatim.
    Keeps last 8 pairs intact; everything older becomes a bullet summary.
    """
    if len(history) < CONTEXT_COMPRESS_AT * 2:
        return history

    keep_items   = 8 * 2
    old_items    = history[:-keep_items]
    recent_items = history[-keep_items:]

    old_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}" for m in old_items
    )
    try:
        summary = ""
        for chunk in llm.chat_stream(
            [
                {"role": "system", "content": "You are a concise technical summariser."},
                {
                    "role": "user",
                    "content": (
                        "Summarise this coding-agent conversation for context. "
                        "Focus on: files modified, decisions made, bugs fixed, current task state.\n\n"
                        f"{old_text}\n\nSummary (3–8 bullet points):"
                    ),
                },
            ],
            turn_type="thinking",
        ):
            summary += chunk
        summary = summary.strip() or "Previous work completed."
    except Exception:
        summary = "Previous work completed (summary unavailable)."

    return [
        {"role": "user",      "content": "📋 COMPRESSED HISTORY SUMMARY:"},
        {"role": "assistant", "content": summary},
    ] + recent_items


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSPACE MAP
# ═══════════════════════════════════════════════════════════════════════════════

def _get_workspace_map() -> str:
    _SKIP = {".git", "__pycache__", "node_modules", "venv", ".venv",
             ".mypy_cache", ".ruff_cache", "dist", "build", ".next"}
    try:
        from .toolbox import get_workspace_root
        root  = get_workspace_root()
        lines: list[str] = []
        for item in sorted(root.rglob("*")):
            if any(p in item.parts for p in _SKIP):
                continue
            rel = item.relative_to(root)
            if item.is_dir():
                lines.append(f"📁 {rel}/")
            else:
                lines.append(f"📄 {rel} ({item.stat().st_size:,} bytes)")
        return "\n".join(lines[:400])
    except Exception:
        return "Unable to map workspace."


# ═══════════════════════════════════════════════════════════════════════════════
# CLAW AGENT v2
# ═══════════════════════════════════════════════════════════════════════════════

class ClawAgent:
    """
    Stateful autonomous coding agent — Plan → Execute → Verify → Done.

    What's new in v2
    ────────────────
    • RollbackRegistry    — snapshot + public rollback_last_session()
    • LoopDetector        — escape stuck repetitive cycles
    • ChangeTracker       — SHA-256 file diff for regression detection
    • ThinkTool support   — internal reasoning with zero side effects
    • Auto-retry          — up to MAX_RETRIES with LLM-produced correction
    • Context compression — LLM summarises old history when session grows large
    • Task classifier     — classify_task() routes turn_type for model selection
    • PLAN: extraction    — streams numbered plan as a dedicated UI event
    • DONE: detection     — structured completion signal ends the loop cleanly
    • Consecutive-error breaker — halts runaway failure cascades
    • Smart turn routing  — adapts model tier based on last tool used
    • _extract_plan / _detect_done  — cleaner helpers replacing inline regex
    • file_diffs in done event — SHA summaries of every changed file
    • Workspace map includes byte sizes

    Streaming API
    ─────────────
    run_streaming(user_prompt) → Generator[dict, None, None]

    SSE event schemas
    ─────────────────
    { type: "thinking",   text }
    { type: "plan",       text }
    { type: "live_text",  text }
    { type: "token",      text }
    { type: "tool_call",  tool, payload }
    { type: "tool_result",tool, result, elapsed, success, attempt }
    { type: "retry",      attempt, tool, error }
    { type: "loop_warn",  text }
    { type: "compressed", text }
    { type: "done",       turns, files_changed, file_diffs, history_len,
                          rollback_available }
    { type: "error",      message }
    { type: "key_error",  error_type, message }
    { type: "stopped",    message, turns }
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model       = model
        self.history:    list[dict[str, str]] = []
        self.last_error: str = ""
        self._stop_event = threading.Event()
        self._last_rollback: RollbackRegistry | None = None

    # ── public controls ───────────────────────────────────────────────────────

    def clear_history(self) -> None:
        self.history.clear()

    def request_stop(self) -> None:
        self._stop_event.set()

    def clear_stop(self) -> None:
        self._stop_event.clear()

    def rollback_last_session(self) -> list[str]:
        """Undo all file changes from the most recent run_streaming call."""
        if self._last_rollback is None:
            return ["No session to roll back."]
        return self._last_rollback.rollback_all()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _is_smart(self) -> bool:
        return self.model.startswith("smart:")

    def _build_messages(self, user_prompt: str, mode: str = "") -> list[dict]:
        prompt = SYSTEM_PROMPT

        # Inject mode-specific addendum
        if mode and mode in _MODE_ADDENDUMS:
            prompt += _MODE_ADDENDUMS[mode]

        try:
            from .toolbox import get_workspace_root
            root = get_workspace_root()

            knowledge = root / ".knowledge.md"
            if knowledge.exists() and knowledge.stat().st_size > 0:
                prompt += (
                    "\n\n## AGENT KNOWLEDGE BASE (.knowledge.md)\n"
                    f"<knowledge>\n{knowledge.read_text(encoding='utf-8')[-6_000:]}\n</knowledge>"
                )

            mem = root / ".memory.md"
            if mem.exists() and mem.stat().st_size > 0:
                prompt += (
                    "\n\n## SHORT-TERM MEMORY (.memory.md)\n"
                    f"<memory>\n{mem.read_text(encoding='utf-8')[-3_000:]}\n</memory>"
                )

            prompt += f"\n\n## WORKSPACE MAP\n<workspace>\n{_get_workspace_map()}\n</workspace>"

            atlas = root / ".atlas.md"
            if atlas.exists() and atlas.stat().st_size > 0:
                prompt += (
                    "\n\n## PROJECT ATLAS (.atlas.md)\n"
                    f"<atlas>\n{atlas.read_text(encoding='utf-8')[-4_000:]}\n</atlas>"
                )
        except Exception:
            pass

        # Strip @mode prefix from the actual user prompt sent to LLM
        clean_prompt = re.sub(r"^@\w+\s*", "", user_prompt).strip() or user_prompt

        msgs: list[dict] = [{"role": "system", "content": prompt}]
        for item in self.history[-(MAX_HISTORY_PAIRS * 2):]:
            msgs.append({
                "role":    item["role"],
                "content": item.get("content", "").strip() or "(empty)",
            })
        msgs.append({"role": "user", "content": clean_prompt or "(empty)"})
        return msgs

    def _snapshot_if_write(
        self, tool_name: str, payload: str, registry: RollbackRegistry
    ) -> None:
        if tool_name in {"FileEditTool", "FilePatchTool", "FileDeleteTool"}:
            path = payload.split(":::", 1)[0].strip()
            if path:
                registry.snapshot(path)

    def _pretrack(self, tool_name: str, payload: str, tracker: ChangeTracker) -> None:
        if tool_name in {"FileEditTool", "FilePatchTool"}:
            path = payload.split(":::", 1)[0].strip()
            if path:
                tracker.pre(path)

    def _posttrack(self, tool_name: str, payload: str, tracker: ChangeTracker) -> None:
        if tool_name in {"FileEditTool", "FilePatchTool"}:
            path = payload.split(":::", 1)[0].strip()
            if path:
                tracker.post(path)

    def _resolve_turn_type(
        self, turn: int, messages: list[dict], task_class: str
    ) -> str:
        if turn == 0:
            return "thinking"
        last_tool = _last_tool_used(messages)
        if last_tool in {"ListDirTool", "FileReadTool", "ViewFileLinesTool", "SearchTool"}:
            return "coding"
        if last_tool == "BashTool":
            return "default"
        return task_class

    # ── main streaming loop ───────────────────────────────────────────────────

    def run_streaming(self, user_prompt: str, mode_override: str = "") -> Generator[dict, None, None]:  # noqa: C901
        """
        Yield SSE event dicts describing every step of the agentic loop.
        See class docstring for the full event schema reference.
        """
        self.clear_stop()

        # Validate mode_override against canonical allowed keys
        mode       = mode_override if mode_override in VALID_MODES else detect_mode(user_prompt)
        # Use mode-preferred model when running under a specialist mode
        _mode_pol  = _MODE_POLICIES.get(mode, {})
        _pref_model = _mode_pol.get("preferred_model", self.model)
        llm        = LLMClient(model=_pref_model or self.model)
        messages   = self._build_messages(user_prompt, mode=mode)
        registry   = RollbackRegistry()
        tracker    = ChangeTracker()
        loop_guard = LoopDetector(window=LOOP_DETECT_WINDOW)
        task_class = classify_task(user_prompt)
        self._last_rollback = registry

        full_content:      list[str] = []
        consecutive_errors: int      = 0
        total_turns:        int      = 0
        plan_steps:        list[str] = []
        step_index:        int       = 0
        commands_run:      list[str] = []
        errors_encountered: list[str] = []
        plan_emitted:      bool      = False

        # Apply mode-specific execution policy
        policy       = _MODE_POLICIES.get(mode, {})
        _max_turns   = policy.get("max_turns", MAX_AGENT_TURNS)
        _mode_tt     = policy.get("turn_type", "")     # override base turn_type if set
        _read_only   = policy.get("read_only", False)  # block write tools

        # Emit mode event so the UI can display the active specialist mode
        if mode and mode in TASK_MODES:
            mode_info = TASK_MODES[mode]
            yield {
                "type":  "mode",
                "mode":  mode,
                "emoji": mode_info["emoji"],
                "label": mode_info["label"],
                "hint":  mode_info["hint"],
            }

        if self._is_smart():
            info = SMART_MODELS.get(self.model, {})
            yield {
                "type": "thinking",
                "text": f"Smart routing active — {info.get('description', self.model)}",
            }

        # ── turn loop ─────────────────────────────────────────────────────────
        for turn in range(_max_turns):
            if self._stop_event.is_set():
                yield {"type": "stopped", "message": "Agent stopped by user.",
                       "turns": total_turns}
                break

            # Consecutive-error cascade breaker
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                yield {
                    "type":    "error",
                    "message": f"Halted: {consecutive_errors} consecutive errors. Review the approach and try a new prompt.",
                }
                break

            # Force-stop on repeated loop detection
            if loop_guard.should_force_stop:
                yield {
                    "type":    "error",
                    "message": "Agent stopped: stuck in a repetitive loop. Try rephrasing or breaking the task into smaller parts.",
                }
                break

            # No-progress detection — if no files changed after several turns, stop
            if turn >= NO_PROGRESS_TURNS and not tracker.modified_paths() and not _read_only:
                yield {
                    "type": "thinking",
                    "text": f"No files modified after {turn} turns — wrapping up to save tokens.",
                }
                messages.append({"role": "user", "content": (
                    "[System] You have used several turns without making any file changes. "
                    "Either complete the task now with a DONE block, or make the necessary "
                    "edits immediately. Do not keep exploring without producing output."
                )})

            total_turns = turn + 1
            # Mode policy overrides the task-class turn_type when set
            base_tt   = _mode_tt if _mode_tt else task_class
            turn_type = self._resolve_turn_type(turn, messages, base_tt)

            # Emit per-turn label
            if self._is_smart():
                concrete     = llm.route(turn_type)
                info         = get_all_models().get(concrete, {})
                active_label = info.get("label", concrete)
                yield {
                    "type": "thinking",
                    "text": f"{info.get('emoji', '')} {active_label} · turn {turn + 1}",
                }
            else:
                yield {"type": "thinking",
                       "text": f"Turn {turn + 1} / {_max_turns}…"}

            # ── stream LLM response ───────────────────────────────────────────
            response_text = ""
            try:
                for chunk in llm.chat_stream(messages, turn_type=turn_type):
                    if self._stop_event.is_set():
                        break
                    response_text += chunk
                    if not response_text.startswith("CLAW_ERROR:"):
                        yield {"type": "live_text",
                               "text": re.sub(r"\n?TOOL:[\s\S]*", "", response_text)}
            except Exception as exc:
                yield {"type": "error", "message": f"LLM stream error: {exc}"}
                break

            if not response_text:
                yield {"type": "error", "message": "Empty LLM response."}
                break

            if response_text.startswith("CLAW_ERROR:"):
                parts = response_text.split("|", 1)
                yield {
                    "type":       "key_error",
                    "error_type": parts[0].replace("CLAW_ERROR:", "").strip(),
                    "message":    parts[1] if len(parts) > 1 else response_text,
                }
                break

            # ── thought extraction ────────────────────────────────────────────
            thought = _extract_thought(response_text)
            if thought:
                yield {"type": "thought", "text": thought}

            # ── PLAN extraction ────────────────────────────────────────────────
            if not plan_emitted:
                plan = _extract_plan(response_text)
                if plan:
                    plan_emitted = True
                    yield {"type": "plan", "text": plan}
                    steps = re.findall(r"^\s*\d+[\.\)]\s+(.+)$", plan, re.MULTILINE)
                    if steps:
                        plan_steps.clear()
                        plan_steps.extend(steps)
                        yield {"type": "plan_steps", "steps": plan_steps}
                elif turn == 0:
                    yield {
                        "type": "thinking",
                        "text": "Requesting plan before execution…",
                    }
                    messages.append({"role": "assistant", "content": response_text.strip() or "(empty)"})
                    messages.append({
                        "role":    "user",
                        "content": (
                            "[System] Output a PLAN: block with numbered steps before tool calls. "
                            "Keep it concise — max 8 steps."
                        ),
                    })
                    full_content.append(response_text)
                    continue
                else:
                    plan_emitted = True

            # ── emit clean prose ──────────────────────────────────────────────
            clean_prose = _strip_tool_lines(response_text)
            clean_prose = re.sub(r"PLAN:[\s\S]+?(?=\n\n|\Z)", "", clean_prose)
            clean_prose = re.sub(r"<thought>[\s\S]*?</thought>", "", clean_prose).strip()
            if clean_prose:
                yield {"type": "token", "text": clean_prose}

            # ── completion check ──────────────────────────────────────────────
            if _detect_done(response_text) and not _parse_first_tool_call(response_text):
                full_content.append(response_text)
                break

            # ── parse tool calls ──────────────────────────────────────────────
            tool_calls = _parse_all_tool_calls(response_text)
            if not tool_calls:
                full_content.append(response_text)
                break

            tool_name, payload = tool_calls[0]
            payload = _clean_payload(payload)

            # Loop detection (full payload hash for accuracy)
            phash = hashlib.md5(payload.encode()).hexdigest()[:12]
            loop_guard.record(tool_name, phash)
            if loop_guard.is_looping():
                hint = loop_guard.hint()
                yield {"type": "loop_warn", "text": hint}
                messages.append({"role": "user", "content": f"[System Warning] {hint}"})
                consecutive_errors += 1
                continue

            # Read-only mode: block any write/execute tools
            _WRITE_TOOLS = {"FileEditTool", "FilePatchTool", "FileDeleteTool",
                            "BashTool", "WorkspaceZipTool", "WorkspaceUnzipTool"}
            if _read_only and tool_name in _WRITE_TOOLS:
                result = (
                    f"[Read-only mode] Tool '{tool_name}' is not permitted in "
                    f"{mode} mode. Only read/search tools are allowed."
                )
                errors_encountered.append(result)
                yield {"type": "tool_result", "tool": tool_name, "result": result,
                       "elapsed": 0.0, "success": False, "attempt": 1}
                messages.append({"role": "assistant", "content": response_text.strip() or "(empty)"})
                messages.append({"role": "user", "content": f"[Tool Result — {tool_name}]\n{result}"})
                full_content.append(response_text)
                continue

            # Pre-snapshot + pre-track
            self._snapshot_if_write(tool_name, payload, registry)
            self._pretrack(tool_name, payload, tracker)

            # Emit step_start if we have a plan step for this index
            if plan_steps and step_index < len(plan_steps):
                yield {
                    "type":  "step_start",
                    "index": step_index,
                    "label": plan_steps[step_index],
                    "tool":  tool_name,
                }

            # Track bash commands
            if tool_name == "BashTool":
                commands_run.append(payload[:120])

            yield {"type": "tool_call", "tool": tool_name, "payload": payload[:400]}

            # ── auto-retry with LLM correction ────────────────────────────────
            result  = ""
            elapsed = 0.0
            attempt = 1

            for attempt in range(1, MAX_RETRIES + 1):
                if self._stop_event.is_set():
                    break
                t_start = time.time()
                result  = _execute_tool(tool_name, payload)
                elapsed = round(time.time() - t_start, 2)

                if not _is_error(result) or attempt == MAX_RETRIES:
                    break

                yield {
                    "type":    "retry",
                    "attempt": attempt,
                    "tool":    tool_name,
                    "error":   result[:300],
                }

                # Ask LLM to produce a corrected tool call
                messages.append({"role": "assistant", "content": response_text})
                messages.append({
                    "role":    "user",
                    "content": (
                        f"[Tool Error — {tool_name} attempt {attempt}]\n"
                        f"{result}\nFix the error and retry with a corrected tool call."
                    ),
                })
                fix_resp = ""
                try:
                    for chunk in llm.chat_stream(messages, turn_type=turn_type):
                        fix_resp += chunk
                except Exception:
                    break
                new_call = _parse_first_tool_call(fix_resp)
                if new_call:
                    tool_name, payload = new_call
                    self._snapshot_if_write(tool_name, payload, registry)
                    self._pretrack(tool_name, payload, tracker)
                    yield {"type": "tool_call", "tool": tool_name, "payload": payload[:400]}

            # Post-track after write completes
            self._posttrack(tool_name, payload, tracker)

            tool_success = not _is_error(result)
            yield {
                "type":    "tool_result",
                "tool":    tool_name,
                "result":  result[:RESULT_TRIM_CHARS],
                "elapsed": elapsed,
                "success": tool_success,
                "attempt": attempt,
            }

            # Emit step_done or step_failed for the tracked plan step
            if plan_steps and step_index < len(plan_steps):
                if tool_success:
                    yield {
                        "type":  "step_done",
                        "index": step_index,
                        "label": plan_steps[step_index],
                    }
                else:
                    yield {
                        "type":    "step_failed",
                        "index":   step_index,
                        "label":   plan_steps[step_index],
                        "error":   result[:200],
                        "attempt": attempt,
                    }
                if tool_success or attempt >= MAX_RETRIES:
                    step_index += 1

            # Error accounting + error collection
            if _is_error(result):
                consecutive_errors += 1
                errors_encountered.append(f"{tool_name}: {result[:150]}")
            else:
                consecutive_errors = max(0, consecutive_errors - 1)

            # Append to messages
            messages.append({"role": "assistant", "content": response_text.strip() or "(empty)"})
            messages.append({
                "role":    "user",
                "content": f"[Tool Result — {tool_name}]\n{result}",
            })
            full_content.append(response_text)
            full_content.append(f"[{tool_name}] → {result[:400]}")

            # Queue any additional tool calls from the same response
            if len(tool_calls) > 1:
                pending = "\n".join(f"TOOL: {n} | {p}" for n, p in tool_calls[1:])
                messages.append({
                    "role":    "user",
                    "content": f"[Pending tool calls — execute these next]\n{pending}",
                })

            # Context compression for long sessions
            if len(messages) > CONTEXT_COMPRESS_AT * 2 + 4:
                sys_msg  = messages[0]
                rest     = _compress_history(messages[1:], llm)
                messages = [sys_msg] + rest
                yield {"type": "compressed", "text": "Context compressed to save tokens."}

        # ── persist conversation history ──────────────────────────────────────
        combined = "\n\n".join(full_content)
        self.history.append({"role": "user",      "content": user_prompt})
        self.history.append({"role": "assistant",  "content": combined[:16_000]})
        if len(self.history) > MAX_HISTORY_PAIRS * 2:
            self.history = self.history[-(MAX_HISTORY_PAIRS * 2):]

        # ── auto-write .memory.md safety net ─────────────────────────────────
        # If the agent didn't write .memory.md itself, create a baseline so
        # the next session always has project context to learn from.
        from .toolbox import get_workspace_root
        _mem_path = get_workspace_root() / ".memory.md"
        _files_changed = tracker.modified_paths()
        if not _mem_path.exists() and _files_changed:
            try:
                from datetime import datetime as _dt
                _mem_path.write_text(
                    "# Project Memory\n"
                    f"Updated: {_dt.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    "## What exists\n"
                    f"Files modified this session: {', '.join(_files_changed)}\n\n"
                    "## What was done last session\n"
                    f"- Mode: {mode_override or 'auto'}\n"
                    f"- Turns taken: {total_turns}\n"
                    f"- Files modified: {', '.join(_files_changed)}\n\n"
                    "## User preferences\n"
                    "- (Update this section as you learn more)\n\n"
                    "## Known issues / next steps\n"
                    "- (Update as tasks are completed)\n",
                    encoding="utf-8",
                )
            except Exception:
                pass

        if not self._stop_event.is_set():
            files_changed = tracker.modified_paths()
            file_diffs    = tracker.summaries()
            yield {
                "type":               "done",
                "turns":              total_turns,
                "files_changed":      files_changed,
                "file_diffs":         file_diffs,
                "history_len":        len(self.history) // 2,
                "rollback_available": registry.has_snapshots,
            }
            # Build a plain-English result statement
            if files_changed:
                _result_stmt = (
                    f"Task completed in {total_turns} turn(s). "
                    f"Modified {len(files_changed)} file(s): {', '.join(files_changed[:5])}"
                    + (f" and {len(files_changed) - 5} more" if len(files_changed) > 5 else ".")
                )
            elif errors_encountered:
                _result_stmt = (
                    f"Task ran {total_turns} turn(s) but encountered "
                    f"{len(errors_encountered)} error(s). No files were changed."
                )
            else:
                _result_stmt = f"Task completed in {total_turns} turn(s) with no file modifications."
            # Emit a structured done_summary event for the UI summary card
            yield {
                "type":               "done_summary",
                "turns":              total_turns,
                "files_changed":      files_changed,
                "file_diffs":         file_diffs,
                "commands_run":       commands_run,
                "steps_total":        len(plan_steps),
                "steps_done":         step_index,
                "mode":               mode,
                "errors_encountered": errors_encountered,
                "result_statement":   _result_stmt,
            }