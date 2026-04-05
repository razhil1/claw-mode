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

MAX_AGENT_TURNS        = 40    # Hard ceiling for any single run
MAX_HISTORY_PAIRS      = 20    # Conversation pairs kept across sessions
MAX_RETRIES            = 3     # Per-tool LLM-corrected retry limit
LOOP_DETECT_WINDOW     = 6     # Turns scanned for repetitive cycle detection
CONTEXT_COMPRESS_AT    = 16    # Compress history after N message pairs
MAX_CONSECUTIVE_ERRORS = 6     # Error-cascade breaker threshold
RESULT_TRIM_CHARS      = 2_500 # Tool output trimmed to this in history


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are BRAIN — an elite autonomous full-stack coding agent running inside a \
developer IDE. You have complete, unrestricted control over the workspace. \
You operate with surgical precision and professional transparency.

Your primary environment and root directory is 'agent_workspace'. You have full, \
unrestricted access to this folder, including permission to create, read, \
modify, and delete any files or directories within it. This is your active \
domain; treat it as your own production environment.

══════════════════════════════════════════
 MANDATORY OPERATING PROCEDURE
══════════════════════════════════════════

STEP 0 — REASONING (every turn)
  You MUST start EVERY response with a <thought> block.
  In this block:
  1. ANALYSE the user's intent and the current workspace state.
  2. CONSIDER at least 2-3 different ways to achieve the goal.
  3. EVALUATE pros/cons, risks, and trade-offs for each approach.
  4. DECIDE on the optimal path and justify it.
  Format:
  <thought>
  Intent: <summary>
  Approaches:
  - Option A: <pros/cons>
  - Option B: <pros/cons>
  Decision: <chosen path + reason>
  Risks: <what could go wrong + mitigation>
  </thought>

STEP 1 — ORIENT
  ALWAYS start by scanning the workspace if unsure:
    TOOL: ListDirTool | .
  Then read every file you'll touch BEFORE editing.

STEP 2 — PLAN (turn 0 or when strategy changes)
  Output a numbered PLAN: block.
  PLAN:
  1. <what you'll do>
  2. <what you'll do>

STEP 3 — EXECUTE
  Issue exactly ONE tool call per response. Chain steps across turns.

STEP 4 — VERIFY
  After every write, confirm with BashTool or FileReadTool.
  If anything is wrong, patch it immediately.

STEP 5 — FINISH
  End with a DONE: block listing every file changed and why.

══════════════════════════════════════════
 COMMUNICATION RULES (CRITICAL)
══════════════════════════════════════════
• ZERO CONVERSATIONAL FILLER. No pleasantries like "Sure thing" or "I can help with that".
• Output <thought> then PLAN:/TOOL: blocks immediately.
• Use ThinkTool for internal, cross-turn reasoning.

══════════════════════════════════════════
 TOOLS (copy format exactly)
══════════════════════════════════════════
TOOL: ListDirTool       | <path>
TOOL: FileReadTool      | <path>
TOOL: ViewFileLinesTool | <path> ::: <start>,<end>
TOOL: SearchTool        | <path> ::: <regex>
TOOL: FileEditTool      | <path> ::: <full content>
TOOL: FilePatchTool     | <path> ::: <old> === <new>
TOOL: FileDeleteTool    | <path>
TOOL: BashTool          | <command>
TOOL: ThinkTool         | <deep reasoning trace>
TOOL: WorkspaceZipTool  | <backup_name.zip>
TOOL: WorkspaceUnzipTool| <backup_name.zip>

══════════════════════════════════════════
 EDITING RULES (non-negotiable)
══════════════════════════════════════════
• NEVER overwrite a whole file to change a few lines — use FilePatchTool.
• Match exact whitespace and indentation. Verify every write immediately.

Full autonomy. Think deeply. Act precisely.\
"""


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

    def record(self, tool_name: str, payload_hash: str) -> None:
        self._history.append(f"{tool_name}:{payload_hash}")
        if len(self._history) > self._window * 2:
            self._history = self._history[-self._window * 2:]

    def is_looping(self) -> bool:
        if len(self._history) < self._window:
            return False
        recent = self._history[-self._window:]
        return len(set(recent)) <= 2

    def hint(self) -> str:
        recent = self._history[-self._window:]
        return (
            f"LOOP DETECTED: You've made {self._window} identical or highly repetitive tool calls. "
            "CRITICAL: Stop repeating edits! If your changes aren't satisfying your goals, you might be editing the wrong file or missing a dependency. "
            "Use SearchTool to find related code or ListDirTool to explore the full directory tree for better candidates."
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
            return tool_workspace_zip(payload or "workspace_backup.zip")

        if tool_name == "WorkspaceUnzipTool":
            return tool_workspace_unzip(payload or "workspace_backup.zip")

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

    def _build_messages(self, user_prompt: str) -> list[dict]:
        prompt = SYSTEM_PROMPT
        try:
            from .toolbox import get_workspace_root
            root = get_workspace_root()

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

        msgs: list[dict] = [{"role": "system", "content": prompt}]
        for item in self.history[-(MAX_HISTORY_PAIRS * 2):]:
            msgs.append({
                "role":    item["role"],
                "content": item.get("content", "").strip() or "(empty)",
            })
        msgs.append({"role": "user", "content": user_prompt.strip() or "(empty)"})
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

    def run_streaming(self, user_prompt: str) -> Generator[dict, None, None]:  # noqa: C901
        """
        Yield SSE event dicts describing every step of the agentic loop.
        See class docstring for the full event schema reference.
        """
        self.clear_stop()

        llm        = LLMClient(model=self.model)
        messages   = self._build_messages(user_prompt)
        registry   = RollbackRegistry()
        tracker    = ChangeTracker()
        loop_guard = LoopDetector(window=LOOP_DETECT_WINDOW)
        task_class = classify_task(user_prompt)
        self._last_rollback = registry

        full_content:      list[str] = []
        consecutive_errors: int      = 0
        total_turns:        int      = 0

        if self._is_smart():
            info = SMART_MODELS.get(self.model, {})
            yield {
                "type": "thinking",
                "text": f"Smart routing active — {info.get('description', self.model)}",
            }

        # ── turn loop ─────────────────────────────────────────────────────────
        for turn in range(MAX_AGENT_TURNS):
            if self._stop_event.is_set():
                yield {"type": "stopped", "message": "Agent stopped by user.",
                       "turns": total_turns}
                break

            # Consecutive-error cascade breaker
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                yield {
                    "type":    "error",
                    "message": f"Halted: {consecutive_errors} consecutive errors.",
                }
                break

            total_turns = turn + 1
            turn_type   = self._resolve_turn_type(turn, messages, task_class)

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
                       "text": f"Turn {turn + 1} / {MAX_AGENT_TURNS}…"}

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

            # ── PLAN extraction (turn 0 only) ─────────────────────────────────
            if turn == 0:
                plan = _extract_plan(response_text)
                if plan:
                    yield {"type": "plan", "text": plan}

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

            # Pre-snapshot + pre-track
            self._snapshot_if_write(tool_name, payload, registry)
            self._pretrack(tool_name, payload, tracker)

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

            yield {
                "type":    "tool_result",
                "tool":    tool_name,
                "result":  result[:RESULT_TRIM_CHARS],
                "elapsed": elapsed,
                "success": not _is_error(result),
                "attempt": attempt,
            }

            # Error accounting
            if _is_error(result):
                consecutive_errors += 1
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
        self.history.append({"role": "assistant",  "content": combined[:8_000]})
        if len(self.history) > MAX_HISTORY_PAIRS * 2:
            self.history = self.history[-(MAX_HISTORY_PAIRS * 2):]

        if not self._stop_event.is_set():
            yield {
                "type":               "done",
                "turns":              total_turns,
                "files_changed":      tracker.modified_paths(),
                "file_diffs":         tracker.summaries(),
                "history_len":        len(self.history) // 2,
                "rollback_available": registry.has_snapshots,
            }