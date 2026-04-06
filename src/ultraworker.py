"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           UltraWorker v4 — Sovereign Autonomous Coding Agent                ║
║                                                                              ║
║  ARCHITECTURE                                                                ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  • Phase-driven loop: THINK → REASON → PLAN → EXECUTE → VERIFY → UPDATE    ║
║  • Multi-model routing: Opus for reasoning, Sonnet for execution            ║
║  • Language-aware tooling: auto-detects stack, adapts commands              ║
║  • Parallel tool dispatch via ThreadPoolExecutor                            ║
║  • Sliding-window context compaction with structured summaries              ║
║  • Patch auto-recovery with fallback to full rewrite                        ║
║  • Workspace-scoped terminal: all shell commands run inside the project     ║
║  • Streamed SSE events with rich phase indicators                           ║
║  • Rollback registry: per-session file snapshots with full undo             ║
║  • Loop detection: escapes stuck repetitive cycles automatically            ║
║  • Auto-retry with backoff: up to N retries per failing tool                ║
║  • Context compression: LLM-powered summarisation for long sessions         ║
║  • ThinkTool: internal reasoning scratchpad with zero side effects          ║
║  • Consecutive-error breaker: halts runaway failure cascades                ║
║  • Task classifier: routes turn type for smart-model selection              ║
║  • Structured DONE: completion signal with diff summary                     ║
║  • File-change checksum tracking for regression detection                   ║
║  • PLAN: extraction and separate streaming to UI                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Generator, Optional

from .llm import LLMClient, DEFAULT_MODEL
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
    get_workspace_root,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

MAX_TURNS              = 40       # High ceiling for complex multi-file builds
MAX_PARALLEL_TOOLS     = 6        # Concurrent tool slots
CONTEXT_WINDOW         = 20       # Message-pairs kept in sliding window
CONTEXT_COMPRESS_AT    = 16       # Compress history after N pairs
PATCH_FAIL_LIMIT       = 2        # Consecutive failures before auto-recovery
RESULT_MAX_CHARS       = 4_000    # Trim threshold for tool output
MEMORY_MAX_LINES       = 20       # Lines of .memory.md to inject
ATLAS_MAX_LINES        = 15       # Lines of .atlas.md to inject
KNOWLEDGE_MAX_LINES    = 120      # Lines of .knowledge.md to inject
WORKSPACE_MAP_MAX      = 100      # Max entries in workspace tree snapshot
MAX_RETRIES            = 3        # Per-tool auto-retry limit
MAX_CONSECUTIVE_ERRORS = 6        # Error-cascade breaker threshold
LOOP_DETECT_WINDOW     = 6        # Turns scanned for stuck loop detection


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE ENUM
# ═══════════════════════════════════════════════════════════════════════════════

class Phase(str, Enum):
    THINK   = "THINK"    # Deep reasoning about the goal
    REASON  = "REASON"   # Decompose into sub-problems
    PLAN    = "PLAN"     # Ordered action steps
    EXECUTE = "EXECUTE"  # Issue tool calls
    VERIFY  = "VERIFY"   # Confirm outcomes
    UPDATE  = "UPDATE"   # Persist knowledge, summarise


PHASE_ICONS: dict[Phase, str] = {
    Phase.THINK:   "🧠",
    Phase.REASON:  "🔍",
    Phase.PLAN:    "📋",
    Phase.EXECUTE: "⚡",
    Phase.VERIFY:  "✅",
    Phase.UPDATE:  "💾",
}

_PHASE_ORDER = [
    Phase.THINK, Phase.REASON, Phase.PLAN,
    Phase.EXECUTE, Phase.VERIFY, Phase.UPDATE,
]


# ═══════════════════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_LANG_SIGS: dict[str, list[str]] = {
    "python":     [".py", "requirements.txt", "pyproject.toml", "setup.py",
                   "Pipfile", "poetry.lock"],
    "javascript": [".js", ".mjs", ".cjs", "package.json"],
    "typescript": [".ts", ".tsx", "tsconfig.json"],
    "rust":       [".rs", "Cargo.toml", "Cargo.lock"],
    "go":         [".go", "go.mod", "go.sum"],
    "java":       [".java", "pom.xml", "build.gradle"],
    "cpp":        [".cpp", ".cc", ".cxx", ".hpp", "CMakeLists.txt"],
    "c":          [".c", ".h", "Makefile"],
    "ruby":       [".rb", "Gemfile", "Rakefile"],
    "php":        [".php", "composer.json"],
    "swift":      [".swift", "Package.swift"],
    "kotlin":     [".kt", ".kts", "build.gradle.kts"],
    "dart":       [".dart", "pubspec.yaml"],
    "elixir":     [".ex", ".exs", "mix.exs"],
    "haskell":    [".hs", ".cabal", "stack.yaml"],
    "lua":        [".lua"],
    "shell":      [".sh", ".bash", ".zsh"],
    "html":       [".html", ".htm"],
    "css":        [".css", ".scss", ".sass", ".less"],
}

_LANG_RUN_HINTS: dict[str, str] = {
    "python":     "python -m pytest  |  python main.py  |  pip install -r requirements.txt",
    "javascript": "npm install  |  npm test  |  node index.js",
    "typescript": "npm install  |  npx tsc --noEmit  |  npm test",
    "rust":       "cargo build  |  cargo test  |  cargo run",
    "go":         "go build ./...  |  go test ./...  |  go run .",
    "java":       "mvn compile  |  mvn test  |  gradle build",
    "cpp":        "cmake -B build && cmake --build build  |  make",
    "c":          "make  |  gcc -o out main.c",
    "ruby":       "bundle install  |  bundle exec rspec  |  ruby main.rb",
    "php":        "composer install  |  php artisan test  |  php index.php",
    "swift":      "swift build  |  swift test",
    "kotlin":     "gradle build  |  gradle test",
    "dart":       "dart pub get  |  dart test  |  dart run",
    "elixir":     "mix deps.get  |  mix test  |  mix run",
    "haskell":    "cabal build  |  cabal test  |  stack build",
    "lua":        "lua main.lua",
    "shell":      "bash script.sh",
}

_LANG_LINTERS: dict[str, str] = {
    ".py":   "python -m ruff check --fix {f} 2>&1 || python -m flake8 {f}",
    ".js":   "npx eslint --fix {f}",
    ".ts":   "npx eslint --fix {f}",
    ".tsx":  "npx eslint --fix {f}",
    ".rs":   "cargo clippy -- -W clippy::all 2>&1",
    ".go":   "golangci-lint run {f} 2>&1",
    ".rb":   "rubocop -a {f}",
    ".php":  "phpcs {f}",
}

_LANG_FORMATTERS: dict[str, str] = {
    ".py":   "python -m black {f} && python -m isort {f}",
    ".js":   "npx prettier --write {f}",
    ".ts":   "npx prettier --write {f}",
    ".tsx":  "npx prettier --write {f}",
    ".rs":   "rustfmt {f}",
    ".go":   "gofmt -w {f}",
    ".java": "google-java-format -i {f}",
    ".rb":   "rubocop -a {f}",
    ".lua":  "stylua {f}",
    ".sh":   "shfmt -w {f}",
}


def detect_language(root: Path) -> str:
    """Infer the dominant programming language from workspace file extensions."""
    scores: dict[str, int] = {}
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            ext  = path.suffix.lower()
            name = path.name
            for lang, sigs in _LANG_SIGS.items():
                for sig in sigs:
                    if sig.startswith(".") and ext == sig:
                        scores[lang] = scores.get(lang, 0) + 1
                    elif name == sig:
                        scores[lang] = scores.get(lang, 0) + 5
    except Exception:
        pass
    return max(scores, key=lambda k: scores[k]) if scores else "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class ModelTier(str, Enum):
    FRONTIER = "frontier"   # claude-opus-4-5        — deep reasoning
    SMART    = "smart"      # claude-sonnet-4-5      — balanced execution
    FAST     = "fast"       # claude-haiku-4-5       — quick / cheap tasks


_TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.FRONTIER: "claude-opus-4-5",
    ModelTier.SMART:    "claude-sonnet-4-5",
    ModelTier.FAST:     "claude-haiku-4-5-20251001",
}

_PHASE_TIER: dict[Phase, ModelTier] = {
    Phase.THINK:   ModelTier.FRONTIER,
    Phase.REASON:  ModelTier.FRONTIER,
    Phase.PLAN:    ModelTier.SMART,
    Phase.EXECUTE: ModelTier.SMART,
    Phase.VERIFY:  ModelTier.SMART,
    Phase.UPDATE:  ModelTier.FAST,
}

_TIER_RANK: dict[ModelTier, int] = {
    ModelTier.FAST: 0, ModelTier.SMART: 1, ModelTier.FRONTIER: 2,
}


def model_for_phase(phase: Phase, ceiling: str) -> str:
    """Return the best model for this phase, never exceeding the user's chosen ceiling."""
    want_tier    = _PHASE_TIER[phase]
    ceiling_tier = next(
        (t for t, m in _TIER_MODELS.items() if m == ceiling), ModelTier.SMART
    )
    if _TIER_RANK[want_tier] > _TIER_RANK[ceiling_tier]:
        return ceiling
    return _TIER_MODELS[want_tier]


# ═══════════════════════════════════════════════════════════════════════════════
# TASK CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════════

def classify_task(prompt: str) -> str:
    """
    Classify the user's task so the model router can pick the optimal tier.

    Returns one of: "debugging", "coding", "thinking", "default"
    """
    p = prompt.lower()
    if any(w in p for w in ["debug", "fix", "error", "bug", "broken", "crash", "fail"]):
        return "debugging"
    if any(w in p for w in ["create", "build", "make", "write", "add", "generate",
                              "refactor", "improve", "optimise", "optimize", "clean"]):
        return "coding"
    if any(w in p for w in ["read", "explain", "what", "why", "how", "describe",
                              "analyse", "analyze", "review"]):
        return "thinking"
    return "default"


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

_PROMPT_TEMPLATE = """\
You are NEXUS ULTRAWORKER — a sovereign autonomous coding agent embedded in \
NEXUS IDE. You operate in a structured phase-driven loop, explain every \
decision clearly, and learn from each session so future sessions are faster \
and smarter.
Your primary workspace is 'agent_workspace/'. You have full read/write/exec \
access. Treat it as a real production environment.

━━━ WORKSPACE STACK ━━━
Detected language : {lang}
Typical commands  : {hints}

━━━ PRIME DIRECTIVES ━━━
1.  REASON BEFORE ACTING — Start every response with a <thought> block.
    Think through: user intent, workspace state, 2–3 options, trade-offs,
    chosen path and WHY it was chosen over the alternatives.
2.  PLAN FIRST — on turn 0 output a PLAN: block with numbered steps.
    After the plan add: Approach: <2–4 sentences explaining the strategy choice>
3.  INTERPRET EVERY TOOL RESULT — after each tool runs, write one → line
    explaining what you found and what it means for the next action. Examples:
      → No files yet — building from scratch.
      → Tests: 3 passed, 1 failed (ImportError in utils.py) — fixing import now.
      → Patch applied cleanly — will verify with a syntax check.
    Never silently skip a tool result. If it's surprising, explain why.
4.  READ BEFORE WRITE — FileReadTool is mandatory before edits on existing files.
5.  PATCH OVER REWRITE — use FilePatchTool for changes < whole file.
6.  VERIFY EVERY CHANGE — after each write, run a lint/test/syntax check and
    explain the result.
7.  UPDATE MEMORY — before emitting DONE, write .memory.md so the next session
    instantly knows project state and user preferences. Use FileEditTool for it.
8.  SIGNAL COMPLETION — end with a DONE: block that teaches, not just reports.

━━━ DONE BLOCK FORMAT ━━━
DONE:
Summary: <one clear sentence — what was built/fixed>
Why it works: <key technical decision or technique, 1–2 sentences>
Files changed:
  • <file> — <what changed and why>
Verified: <exact command run and result>
What you can do next: <1–3 concrete follow-up suggestions>

━━━ MEMORY FILE FORMAT ━━━
Write '.memory.md' inside agent_workspace/ before DONE. Content:
  # Project Memory
  Updated: <date>
  ## What exists
  <stack / architecture / key files>
  ## What was done last session
  <2–3 bullets of what was built or fixed>
  ## User preferences
  <language, style, frameworks the user likes>
  ## Known issues / next steps
  <unresolved items or logical next tasks>

━━━ PHASE HEADERS (use exactly one per response) ━━━
## 🧠 THINK    — Strategy, assumptions, risk assessment.
## 🔍 REASON   — Sub-problems and dependencies.
## 📋 PLAN     — Numbered steps and approach rationale.
## ⚡ EXECUTE  — Tool calls and result interpretation.
## ✅ VERIFY   — Outcome confirmation and regression check.
## 💾 UPDATE   — Writing .memory.md with session learnings.

━━━ FULL TOOL REFERENCE ━━━
TOOL: ListDirTool    | <path>
TOOL: FileReadTool   | <path>
TOOL: FileEditTool   | <path> ::: <complete_file_content>
TOOL: FilePatchTool  | <path> ::: <exact_old_block> === <new_block>
TOOL: BashTool       | <shell_command>
TOOL: SearchTool     | <path> ::: <regex>
TOOL: ViewLinesTool  | <path> ::: <start_line>,<end_line>
TOOL: FileDeleteTool | <path>
TOOL: ThinkTool      | <internal reasoning — no side effects>
TOOL: LintTool       | <path>
TOOL: FormatTool     | <path>
TOOL: TestRunTool    | <test_command>
TOOL: DepsInstall    | <package_manager_command>
TOOL: GitTool        | <git_subcommand_and_args>
TOOL: WorkspaceZipTool  | <backup_name.zip>
TOOL: WorkspaceUnzipTool| <backup_name.zip>

━━━ EDITING RULES ━━━
• FilePatchTool: match the old_block EXACTLY — whitespace, indentation, every character.
• FileEditTool: provide the COMPLETE file content. Never truncate.
• After editing: follow up with LintTool or TestRunTool. Explain the result.
• Verify every write with FileReadTool or BashTool immediately.

━━━ CODE STANDARDS ━━━
• Idiomatic, production-grade {lang}. Match the workspace's existing style.
• Full error handling — no bare except, no uncaught panics, no silent failures.
• No TODO stubs, no placeholder comments, no incomplete implementations.
• Responsive UI if applicable. Clean, well-structured code throughout.

━━━ KNOWLEDGE BASE ━━━
• AGENT KNOWLEDGE BASE is injected from .knowledge.md — treat it as your
  built-in expert handbook. It contains: core engineering principles, specialist
  role playbooks (Architect, Planner, Code Reviewer, Build-Error Resolver,
  Security Reviewer, Debugger), architectural patterns for Backend/Frontend/Data,
  language-specific rules for Python/JS/TS/Rust/Go/Shell, production-grade
  code review examples (good vs bad), and a pre-flight done checklist.
• Before starting any non-trivial task, mentally check the relevant section of
  the knowledge base: Are you following the right patterns? Applying the right
  role? Meeting the code quality thresholds?
• Security rules in the knowledge base are HARD constraints — never override them.

━━━ LEARNING FROM SESSION MEMORY ━━━
• If SESSION MEMORY appears in the context, treat it as ground truth.
• Apply the user's documented preferences automatically.
• If a request contradicts past patterns, flag it and confirm before proceeding.
• After each session, update .memory.md with new learnings about the project
  and the user's preferences so every future session gets smarter.

━━━ SAFETY CONSTRAINTS ━━━
• Never delete files unless the user's message explicitly instructs it.
• Never expose .env values in output or logs.
• Never run destructive shell commands without explicit user confirmation.
• Use ThinkTool to reason about risky operations before executing them.
"""


def build_system_prompt(lang: str, mode: str = "") -> str:
    hints = _LANG_RUN_HINTS.get(lang, "No language-specific hints available.")
    base  = _PROMPT_TEMPLATE.format(lang=lang, hints=hints)
    mode_addenda: dict[str, str] = {
        "builder": (
            "\n\n══ BUILDER MODE ══\n"
            "You are a specialist builder. Focus exclusively on creating and editing files. "
            "Prefer Qwen2.5-Coder strategies: write complete, production-ready code. "
            "Priority tools: FileEditTool, BashTool, FilePatchTool."
        ),
        "debugger": (
            "\n\n══ DEBUGGER MODE ══\n"
            "You are a specialist debugger. Reproduce → isolate → fix → verify. "
            "Run the failing command first, read error messages carefully, then patch. "
            "Priority tools: BashTool, FileReadTool, SearchTool."
        ),
        "refactorer": (
            "\n\n══ REFACTORER MODE ══\n"
            "You are a specialist refactorer. Read ALL relevant code before making any edits. "
            "Prefer FilePatchTool for targeted changes. Preserve behaviour, improve structure. "
            "Priority tools: FileReadTool, FilePatchTool, SearchTool."
        ),
        "researcher": (
            "\n\n══ RESEARCHER MODE ══\n"
            "You are a specialist researcher. READ ONLY — no file writes or shell execution. "
            "Synthesise findings into a structured report. "
            "Priority tools: SearchTool, FileReadTool, ListDirTool."
        ),
        "reviewer": (
            "\n\n══ REVIEWER MODE ══\n"
            "You are a specialist code reviewer. READ ONLY — no file writes or shell execution. "
            "Rate each issue by severity (Critical/High/Medium/Low) and suggest concrete fixes. "
            "Priority tools: FileReadTool, SearchTool, ListDirTool."
        ),
    }
    addendum = mode_addenda.get(mode, "")
    return base + addendum


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
        """Capture the current content of a file. No-op if already snapshotted."""
        if path in self._snapshots:
            return
        try:
            self._snapshots[path] = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            self._snapshots[path] = None   # file is new — rollback = delete

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
            f"⚠️ Loop detected — same calls repeated: {set(recent)}. "
            "Abandon current approach. Try a completely different strategy or tool sequence."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# FILE CHANGE TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FileChange:
    path:       str
    before_sha: str | None
    after_sha:  str | None = None

    @staticmethod
    def sha(path: str) -> str | None:
        try:
            data = Path(path).read_bytes()
            return hashlib.sha256(data).hexdigest()[:12]
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
# TOOL RESULT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    tool:    str
    payload: str
    output:  str
    elapsed: float
    success: bool
    attempt: int = 1


def _compress(text: str, max_len: int = RESULT_MAX_CHARS) -> str:
    if len(text) <= max_len:
        return text
    head = max_len // 2
    tail = max_len // 3
    cut  = len(text) - head - tail
    return f"{text[:head]}\n…[{cut} chars trimmed]…\n{text[-tail:]}"


def _ws_bash(cmd: str, cwd: Path, timeout: int = 120) -> str:
    """Run a shell command scoped to the workspace root."""
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
        return (proc.stdout + proc.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"


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
# TOOL DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════

_PARAM_STRIP = re.compile(
    r"^(?:path|file|filepath|directory|dir|command|cmd|query|pattern|content|input|args?)\s*[:=]\s*",
    re.IGNORECASE,
)

_KNOWN_TOOLS = {
    "ListDirTool", "FileReadTool", "ViewLinesTool", "SearchTool",
    "FileEditTool", "FilePatchTool", "FileDeleteTool",
    "BashTool", "ThinkTool",
    "LintTool", "FormatTool", "TestRunTool", "DepsInstall", "GitTool",
    "WorkspaceZipTool", "WorkspaceUnzipTool",
}


def _clean(raw: str) -> str:
    s = raw.strip()
    if s.startswith("`") and s.endswith("`"):
        s = s.strip("`").strip()
        
    if ":::" in s:
        p1, p2 = s.split(":::", 1)
        p1 = p1.strip()
        p2 = p2.strip()
        # Remove hallucinated <path> tags from the path portion
        p1 = re.sub(r'</?[\w\\]+>', '', p1).strip()
        if p2.startswith("```"):
            p2 = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", p2)
            if p2.endswith("```"):
                p2 = p2[:-3].strip()
        s = f"{p1} ::: {p2}"
    else:
        s = re.sub(r'</?[\w\\]+>', '', s).strip()

    if s.startswith("{"):
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                vals = list(data.values())
                if len(vals) == 1:
                    return str(vals[0]).strip()
                if len(vals) == 2:
                    return f"{vals[0]} ::: {vals[1]}"
        except Exception:
            pass
    return _PARAM_STRIP.sub("", s).strip()


def execute_tool(tool: str, raw: str, root: Path) -> ToolResult:
    """Dispatch a single tool call and return a structured result."""
    payload = _clean(raw)
    t0      = time.perf_counter()
    out     = ""
    ok      = True

    try:
        # ── internal / reasoning ──────────────────────────────────────────────
        if tool == "ThinkTool":
            out = f"[Thought]: {payload[:600]}"

        # ── read-only / navigation ────────────────────────────────────────────
        elif tool == "ListDirTool":
            out = tool_list_dir(payload or ".")

        elif tool == "FileReadTool":
            out = tool_file_read(payload)

        elif tool == "ViewLinesTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                nums  = parts[1].strip().split(",")
                start = int(nums[0].strip())
                end   = int(nums[1].strip()) if len(nums) > 1 else start + 60
                out   = tool_view_file_lines(parts[0].strip(), start, end)
            else:
                out = tool_file_read(payload)

        elif tool == "SearchTool":
            parts = payload.split(":::", 1)
            out   = tool_search(parts[0].strip(), parts[1].strip()) if len(parts) == 2 \
                    else tool_search(".", payload)

        elif tool == "FileDeleteTool":
            out = tool_file_delete(payload)

        # ── write ─────────────────────────────────────────────────────────────
        elif tool == "FileEditTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                out = tool_file_edit(parts[0].strip(), parts[1])
            else:
                out = "Error: FileEditTool requires 'path ::: content'."; ok = False

        elif tool == "FilePatchTool":
            p1 = payload.split(":::", 1)
            if len(p1) == 2:
                p2 = p1[1].split("===", 1)
                if len(p2) == 2:
                    out = tool_file_patch(p1[0].strip(), p2[0], p2[1])
                else:
                    out = "Error: FilePatchTool needs 'path ::: old === new'."; ok = False
            else:
                out = "Error: FilePatchTool needs 'path ::: old === new'."; ok = False

        # ── workspace-scoped shell ────────────────────────────────────────────
        elif tool == "BashTool":
            out = _ws_bash(payload, root)

        elif tool == "LintTool":
            ext  = Path(payload).suffix.lower()
            tmpl = _LANG_LINTERS.get(ext)
            out  = _ws_bash(tmpl.format(f=payload), root) if tmpl \
                   else f"No linter configured for *{ext} files."

        elif tool == "FormatTool":
            ext  = Path(payload).suffix.lower()
            tmpl = _LANG_FORMATTERS.get(ext)
            out  = _ws_bash(tmpl.format(f=payload), root) if tmpl \
                   else f"No formatter configured for *{ext} files."

        elif tool == "TestRunTool":
            out = _ws_bash(payload, root, timeout=300)

        elif tool == "DepsInstall":
            out = _ws_bash(payload, root, timeout=300)

        elif tool == "GitTool":
            out = _ws_bash(f"git {payload}", root)

        elif tool == "WorkspaceZipTool":
            # tool_workspace_zip() returns bytes; save to file in workspace root
            zip_bytes = tool_workspace_zip()
            name = payload or "workspace_backup.zip"
            dest = root / name
            dest.write_bytes(zip_bytes)
            out = f"Success: Workspace backed up to '{name}' ({len(zip_bytes):,} bytes)."

        elif tool == "WorkspaceUnzipTool":
            # payload is a filename inside the workspace root
            name = payload or "workspace_backup.zip"
            src_file = root / name
            if not src_file.exists():
                out = f"Error: '{name}' not found in workspace."; ok = False
            else:
                out = tool_workspace_unzip(src_file.read_bytes())
            
        else:
            out = (
                f"Unknown tool '{tool}'. "
                f"Known tools: {', '.join(sorted(_KNOWN_TOOLS))}"
            )
            ok = False

        if _is_error(out):
            ok = False

    except Exception as exc:
        out = f"Tool error ({tool}): {exc}"; ok = False

    return ToolResult(
        tool=tool, payload=payload,
        output=_compress(out), elapsed=round(time.perf_counter() - t0, 3),
        success=ok,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL PARSER
# ═══════════════════════════════════════════════════════════════════════════════

_MULTI_RE  = re.compile(r"TOOL:\s*(\w+)\s*\|+\s*([\s\S]*?)(?=\nTOOL:|\Z)", re.M)
_SINGLE_RE = re.compile(r"TOOL:\s*(\w+)\s*[:|]+\s*([\s\S]*)", re.M)


def parse_tools(text: str) -> list[tuple[str, str]]:
    hits = _MULTI_RE.findall(text)
    if hits:
        calls = [
            (n.strip(), p.strip())
            for n, p in hits
            if n.strip() in _KNOWN_TOOLS
        ]
        if calls:
            return calls
    
    hit = _SINGLE_RE.search(text)
    if hit and hit.group(1).strip() in _KNOWN_TOOLS:
        return [(hit.group(1).strip(), hit.group(2).strip())]
        
    # Fallback: If no explicit tools are found, look for Markdown code blocks with filenames
    calls = []
    blocks = re.finditer(r"```[a-zA-Z0-9_-]*\n([\s\S]*?)```", text)
    for b in blocks:
        code = b.group(1)
        first_line = code.split("\n", 1)[0].strip()
        name_match = re.match(r"^(?://|#|/\*|<!--)\s*([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)", first_line)
        if name_match:
            filename = name_match.group(1)
            pure_code = code[len(first_line):].strip()
            calls.append(("FileEditTool", f"{filename} ::: {pure_code}"))
    
    return calls


def strip_tools(text: str) -> str:
    return re.sub(r"\n?TOOL:[\s\S]*", "", text).strip()


def detect_done(text: str) -> bool:
    """True if the agent emitted a structured DONE: completion signal."""
    return bool(re.search(r"\bDONE\s*:", text, re.IGNORECASE))


def extract_plan(text: str) -> str | None:
    """Pull out the PLAN: block if present."""
    m = re.search(r"PLAN:\s*([\s\S]+?)(?=\nTOOL:|\Z)", text)
    return m.group(1).strip() if m else None


def extract_thought(text: str) -> str | None:
    """Pull out the <thought> block if present."""
    m = re.search(r"<thought>([\s\S]*?)</thought>", text)
    return m.group(1).strip() if m else None


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TurnRecord:
    turn_num:  int
    phase:     str
    assistant: str
    tools:     list[str] = field(default_factory=list)
    results:   list[str] = field(default_factory=list)


def _workspace_tree(root: Path) -> str:
    skip = {
        ".git", "__pycache__", "node_modules", "venv", ".venv",
        ".next", "dist", "build", ".cache", ".mypy_cache", ".ruff_cache",
    }
    entries: list[str] = []
    for item in sorted(root.rglob("*")):
        if any(p in item.parts for p in skip):
            continue
        rel   = item.relative_to(root)
        depth = len(rel.parts) - 1
        icon  = "📁" if item.is_dir() else "📄"
        size  = f" ({item.stat().st_size:,}b)" if item.is_file() else ""
        entries.append(f"{'  ' * depth}{icon} {rel.name}{size}")
        if len(entries) >= WORKSPACE_MAP_MAX:
            entries.append("  … (truncated)")
            break
    return "\n".join(entries)


class ContextManager:
    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt
        self.turns: list[TurnRecord] = []
        self._raw:  list[dict]       = []

    def add_turn(self, record: TurnRecord) -> None:
        self.turns.append(record)
        self._raw.append({"role": "assistant", "content": record.assistant})
        combined = "\n\n".join(
            f"[{t} ▶ Result]\n{r}" for t, r in zip(record.tools, record.results)
        )
        if combined:
            self._raw.append({"role": "user", "content": combined})

    def _compaction_header(self) -> str:
        lines = ["[Context Compacted — summary of earlier turns]"]
        for rec in self.turns[:-CONTEXT_WINDOW]:
            tools_str = ", ".join(rec.tools) or "none"
            lines.append(f"  Turn {rec.turn_num} [{rec.phase}]: {tools_str}")
        return "\n".join(lines)

    def compress(self, llm: LLMClient) -> None:
        """LLM-powered compression of the oldest half of _raw history."""
        if len(self._raw) < CONTEXT_COMPRESS_AT * 2:
            return
        keep     = 8
        old      = self._raw[:-keep * 2]
        recent   = self._raw[-keep * 2:]
        old_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:300]}" for m in old
        )
        try:
            summary = ""
            for chunk in llm.chat_stream(
                [
                    {"role": "system", "content": "You are a concise technical summariser."},
                    {
                        "role": "user",
                        "content": (
                            "Summarise this coding-agent conversation history. "
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

        self._raw = [
            {"role": "user",      "content": "📋 COMPRESSED HISTORY SUMMARY:"},
            {"role": "assistant", "content": summary},
        ] + recent

    def build(self, user_prompt: str, root: Path) -> list[dict]:
        extras: list[str] = []
        for fname, label, max_l in [
            (".knowledge.md", "AGENT KNOWLEDGE BASE", KNOWLEDGE_MAX_LINES),
            (".memory.md",    "SESSION MEMORY",        MEMORY_MAX_LINES),
            (".atlas.md",     "PROJECT ATLAS",          ATLAS_MAX_LINES),
        ]:
            p = root / fname
            if p.exists():
                lines = p.read_text(encoding="utf-8").splitlines()[-max_l:]
                extras.append(f"## {label}\n" + "\n".join(lines))

        ws = _workspace_tree(root)
        if ws:
            extras.append("## WORKSPACE FILES\n" + ws)

        system = self.system_prompt
        if extras:
            system += "\n\n" + "\n\n".join(extras)

        msgs: list[dict] = [{"role": "system", "content": system}]
        window = self._raw[-(CONTEXT_WINDOW * 2):]
        if len(self._raw) > CONTEXT_WINDOW * 2:
            msgs.append({"role": "user", "content": self._compaction_header()})
        msgs.extend(window)
        msgs.append({"role": "user", "content": user_prompt.strip() or "(empty)"})
        return msgs


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT STATE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentState:
    lang:               str            = "unknown"
    task_class:         str            = "default"
    current_phase:      Phase          = Phase.THINK
    files_changed:      list[str]      = field(default_factory=list)
    patch_failures:     dict[str, int] = field(default_factory=dict)
    total_tools_used:   int            = 0
    total_turns:        int            = 0
    consecutive_errors: int            = 0

    def infer_phase(self, text: str) -> Phase:
        for p in Phase:
            if f"## {PHASE_ICONS[p]} {p.value}" in text or f"## {p.value}" in text:
                self.current_phase = p
                return p
        return self.current_phase

    def record(self, tr: ToolResult) -> Optional[str]:
        """
        Persist tool outcome. Returns an auto-recovery hint string when
        FilePatchTool has failed too many consecutive times, otherwise None.
        """
        self.total_tools_used += 1

        if tr.success:
            self.consecutive_errors = max(0, self.consecutive_errors - 1)
        else:
            self.consecutive_errors += 1

        if tr.tool in ("FileEditTool", "FilePatchTool"):
            fname = tr.payload.split(":::", 1)[0].strip() if ":::" in tr.payload else ""
            if fname and tr.success and fname not in self.files_changed:
                self.files_changed.append(fname)

        if tr.tool == "FilePatchTool":
            fname = tr.payload.split(":::", 1)[0].strip() if ":::" in tr.payload else "?"
            if not tr.success:
                self.patch_failures[fname] = self.patch_failures.get(fname, 0) + 1
                if self.patch_failures[fname] >= PATCH_FAIL_LIMIT:
                    return (
                        f"\n⚠️  AUTO-RECOVERY: FilePatchTool failed"
                        f" {self.patch_failures[fname]}× on '{fname}'.\n"
                        f"MANDATORY FALLBACK — do these two steps:\n"
                        f"  1. TOOL: FileReadTool | {fname}\n"
                        f"  2. TOOL: FileEditTool | {fname} ::: <complete rewritten content>\n"
                        f"Do NOT use FilePatchTool on this file again."
                    )
            else:
                self.patch_failures.pop(fname, None)

        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ULTRAWORKER v4
# ═══════════════════════════════════════════════════════════════════════════════

class UltraWorker:
    """
    Sovereign autonomous coding agent — phase-driven, multi-model, any language.

    New in v4 vs v3
    ───────────────
    • RollbackRegistry    — snapshot + undo on demand
    • LoopDetector        — escape stuck repetitive cycles
    • ChangeTracker       — SHA-based file diff for regression detection
    • ThinkTool           — internal reasoning with no side effects
    • Auto-retry          — up to MAX_RETRIES per failing tool with LLM correction
    • Context compression — LLM summarises old history to stay within context
    • Task classifier     — routes turn_type for smart model selection
    • PLAN: extraction    — streams numbered plan to UI before execution
    • DONE: detection     — structured completion signal ends the loop cleanly
    • Consecutive-error breaker — halts runaway failure cascades
    • File-size info      — workspace tree includes byte counts

    Streaming API
    ─────────────
    run_streaming(user_prompt: str) → Generator[dict, None, None]

    SSE event schemas
    ─────────────────
    { type: "phase",       phase, icon, text }
    { type: "thinking",    text }
    { type: "live_text",   text }
    { type: "plan",        text }
    { type: "token",       text }
    { type: "tool_call",   tool, payload }
    { type: "tool_result", tool, result, elapsed, success, attempt }
    { type: "retry",       attempt, tool, error }
    { type: "recovery",    message }
    { type: "loop_warn",   text }
    { type: "compressed",  text }
    { type: "done",        turns, files_changed, file_diffs, tools_used, lang, history_len }
    { type: "error",       message }
    { type: "key_error",   error_type, message }
    { type: "stopped",     message, turns }
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model   = model
        self.history: list[dict] = []
        self._stop   = threading.Event()
        self._pool   = ThreadPoolExecutor(
            max_workers=MAX_PARALLEL_TOOLS, thread_name_prefix="uw4"
        )
        # Exposed so the UI layer can trigger rollbacks after a session
        self._last_rollback: RollbackRegistry | None = None

    # ── public controls ───────────────────────────────────────────────────────

    def clear_history(self) -> None:
        self.history.clear()

    def request_stop(self) -> None:
        self._stop.set()

    def clear_stop(self) -> None:
        self._stop.clear()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)

    def rollback_last_session(self) -> list[str]:
        """Undo all file changes made during the most recent run_streaming call."""
        if self._last_rollback is None:
            return ["No session to roll back."]
        return self._last_rollback.rollback_all()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _root() -> Path:
        try:
            return get_workspace_root()
        except Exception:
            return Path.cwd()

    def _llm(self, phase: Phase) -> LLMClient:
        return LLMClient(model=model_for_phase(phase, self.model))

    def _save_memory(self, root: Path, state: AgentState, summary: str) -> None:
        mem   = root / ".memory.md"
        ts    = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = textwrap.dedent(f"""
            ## {ts}
            - lang    : {state.lang}
            - task    : {state.task_class}
            - turns   : {state.total_turns}
            - tools   : {state.total_tools_used}
            - changed : {', '.join(state.files_changed) or 'none'}
            - note    : {summary.splitlines()[0][:120] if summary else 'n/a'}
        """).strip()
        existing = mem.read_text(encoding="utf-8") if mem.exists() else ""
        kept     = "\n".join(existing.splitlines()[-50:])
        mem.write_text(kept + "\n\n" + entry + "\n", encoding="utf-8")

    def _snapshot_if_write(
        self, tool: str, payload: str, registry: RollbackRegistry
    ) -> None:
        if tool in {"FileEditTool", "FilePatchTool", "FileDeleteTool"}:
            path = payload.split(":::", 1)[0].strip()
            if path:
                registry.snapshot(path)

    def _pretrack(self, tool: str, payload: str, tracker: ChangeTracker) -> None:
        if tool in {"FileEditTool", "FilePatchTool"}:
            path = payload.split(":::", 1)[0].strip()
            if path:
                tracker.pre(path)

    def _posttrack(self, tool: str, payload: str, tracker: ChangeTracker) -> None:
        if tool in {"FileEditTool", "FilePatchTool"}:
            path = payload.split(":::", 1)[0].strip()
            if path:
                tracker.post(path)

    # ── streaming loop ────────────────────────────────────────────────────────

    def run_streaming(self, user_prompt: str, mode_override: str = "") -> Generator[dict, None, None]:  # noqa: C901
        """
        Phase-driven streaming loop with full v4 safety and intelligence stack.

        Phases cycle: THINK → REASON → PLAN → EXECUTE → VERIFY → UPDATE → …
        The LLM may jump phases by including the appropriate header.
        The loop terminates when:
          • The LLM emits no TOOL: block (clean finish)
          • The LLM emits a DONE: block with no TOOL: block
          • MAX_TURNS is reached
          • The user calls request_stop()
          • Consecutive errors exceed MAX_CONSECUTIVE_ERRORS
        """
        self.clear_stop()

        # Import here to avoid circular; policy tables live in agent.py
        from .agent import _MODE_POLICIES, VALID_MODES, detect_mode
        # Validate or auto-detect the specialist mode
        if mode_override in VALID_MODES:
            _mode = mode_override
        else:
            # Auto-detect from prompt so specialist policies always activate
            _mode = detect_mode(user_prompt)
        _policy    = _MODE_POLICIES.get(_mode, {})
        _max_turns = _policy.get("max_turns", MAX_TURNS)
        _read_only = _policy.get("read_only", False)
        # Rebind so all local references use the validated/detected value
        mode_override = _mode

        root     = self._root()
        lang     = detect_language(root)
        state    = AgentState(lang=lang, task_class=classify_task(user_prompt))
        ctx      = ContextManager(build_system_prompt(lang, mode=mode_override))
        registry = RollbackRegistry()
        tracker  = ChangeTracker()
        loop_guard        = LoopDetector(window=LOOP_DETECT_WINDOW)
        self._last_rollback = registry

        # Use mode-preferred model when running under a specialist mode
        _pref_model = _policy.get("preferred_model", None)

        yield {"type": "thinking", "text": f"⚡ UltraWorker v4 — {lang} workspace · mode={mode_override or 'auto'} · task={state.task_class}"}

        # Emit a unified mode event so the UI status bar shows the active specialist mode
        if mode_override:
            from .agent import TASK_MODES
            mode_info = TASK_MODES.get(mode_override, {})
            yield {
                "type":  "mode",
                "mode":  mode_override,
                "label": mode_info.get("label", mode_override.title()),
                "emoji": mode_info.get("emoji", ""),
                "desc":  mode_info.get("desc", ""),
            }

        full_content:       list[str] = []
        plan_steps:         list[str] = []
        step_index:         int       = 0
        commands_run:       list[str] = []
        errors_encountered: list[str] = []
        plan_emitted:       bool      = False

        _WRITE_TOOLS = {"FileEditTool", "FilePatchTool", "FileDeleteTool",
                        "BashTool", "WorkspaceZipTool", "WorkspaceUnzipTool"}

        for turn in range(_max_turns):
            if self._stop.is_set():
                yield {"type": "stopped", "message": "Stopped by user.", "turns": state.total_turns}
                break

            # Consecutive-error breaker
            if state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                yield {
                    "type":    "error",
                    "message": f"Halted: {state.consecutive_errors} consecutive errors.",
                }
                break

            state.total_turns = turn + 1
            # When a specialist mode specifies a preferred model, use it directly;
            # otherwise fall back to phase-based model routing
            if _pref_model:
                llm = LLMClient(model=_pref_model)
            else:
                llm = self._llm(state.current_phase)
            # turn_type from mode policy overrides task-class routing when set
            _mode_tt = _policy.get("turn_type", "") or state.task_class
            messages = ctx.build(user_prompt, root)

            yield {
                "type":  "phase",
                "phase": state.current_phase.value,
                "icon":  PHASE_ICONS[state.current_phase],
                "text":  f"Turn {turn + 1} · {state.current_phase.value} [{llm.model}]",
            }

            # ── stream LLM ────────────────────────────────────────────────────
            response = ""
            try:
                for chunk in llm.chat_stream(messages, turn_type=_mode_tt):
                    response += chunk
                    if not response.startswith("CLAW_ERROR:"):
                        yield {"type": "live_text",
                               "text": re.sub(r"\n?TOOL:[\s\S]*", "", response)}
            except Exception as exc:
                yield {"type": "error", "message": str(exc)}
                break

            if not response:
                yield {"type": "error", "message": "Empty LLM response — aborting."}
                break

            if response.startswith("CLAW_ERROR:"):
                parts = response.split("|", 1)
                yield {
                    "type":       "key_error",
                    "error_type": parts[0].replace("CLAW_ERROR:", "").strip(),
                    "message":    parts[1] if len(parts) > 1 else response,
                }
                break

            state.infer_phase(response)

            # ── thought extraction ────────────────────────────────────────────
            thought = extract_thought(response)
            if thought:
                yield {"type": "thought", "text": thought}

            # ── PLAN extraction (persistent gate until plan confirmed) ───────────
            # Try extracting a plan on every turn until one is confirmed.
            # Tool execution and completion are blocked until plan_emitted = True.
            if not plan_emitted:
                plan = extract_plan(response)
                if plan:
                    plan_emitted = True
                    yield {"type": "plan", "text": plan}
                    steps = re.findall(r"^\s*\d+[\.\)]\s+(.+)$", plan, re.MULTILINE)
                    if steps:
                        plan_steps.clear()
                        plan_steps.extend(steps)
                        yield {"type": "plan_steps", "steps": plan_steps}
                else:
                    # Mandatory planning gate: no plan yet — block and re-prompt.
                    # Covers the "jumped to tools", "prose only", and "DONE without plan" cases.
                    yield {
                        "type": "thinking",
                        "text": "Planning phase required — requesting plan before execution…",
                    }
                    ctx._raw.append({"role": "assistant",
                                     "content": response.strip() or "(empty)"})
                    ctx._raw.append({
                        "role":    "user",
                        "content": (
                            "[System] You must output a PLAN: block with numbered steps "
                            "BEFORE issuing any tool calls or finishing. Please produce your plan now."
                        ),
                    })
                    full_content.append(response)
                    continue

            # ── parse tools ───────────────────────────────────────────────────
            calls = parse_tools(response)

            # Prose above tool block
            prose = re.sub(r"PLAN:[\s\S]+?(?=\n\n|\Z)", "", strip_tools(response))
            prose = re.sub(r"<thought>[\s\S]*?</thought>", "", prose).strip()
            if prose:
                yield {"type": "token", "text": prose}

            # ── completion check ──────────────────────────────────────────────
            if not calls:
                final = strip_tools(response).strip()
                if final:
                    yield {"type": "token", "text": final}
                full_content.append(response)
                try:
                    self._save_memory(root, state, final)
                    yield {
                        "type": "phase",
                        "phase": Phase.UPDATE.value,
                        "icon":  PHASE_ICONS[Phase.UPDATE],
                        "text":  "Memory persisted.",
                    }
                except Exception:
                    pass
                break

            # DONE: signal with no new tools also ends the loop cleanly
            if detect_done(response) and not calls:
                full_content.append(response)
                break

            # ── execute ───────────────────────────────────────────────────────
            MAX_BATCH = 15
            if len(calls) > MAX_BATCH:
                yield {"type": "thinking", "text": f"⚠️ Truncating tool batch from {len(calls)} to {MAX_BATCH}"}
                calls = calls[:MAX_BATCH]

            if len(calls) == 1:
                tool, payload = calls[0]

                # Read-only mode: block write/exec tools
                if _read_only and tool in _WRITE_TOOLS:
                    block_msg = (
                        f"[Read-only mode] Tool '{tool}' is not permitted in "
                        f"{mode_override or 'current'} mode. Only read/search tools are allowed."
                    )
                    errors_encountered.append(block_msg)
                    yield {"type": "tool_result", "tool": tool, "result": block_msg,
                           "elapsed": 0.0, "success": False, "attempt": 1}
                    ctx._raw.append({"role": "assistant",
                                     "content": response.strip() or "(empty)"})
                    ctx._raw.append({"role": "user",
                                     "content": f"[Tool Result — {tool}]\n{block_msg}"})
                    full_content.append(response)
                    continue

                # Loop detection
                phash = hashlib.md5(payload[:200].encode()).hexdigest()[:8]
                loop_guard.record(tool, phash)
                if loop_guard.is_looping():
                    hint = loop_guard.hint()
                    yield {"type": "loop_warn", "text": hint}
                    ctx.add_turn(TurnRecord(
                        turn_num=turn + 1, phase=state.current_phase.value,
                        assistant="[loop detected]", tools=[], results=[hint],
                    ))
                    state.consecutive_errors += 1
                    # Inject escape hint into context
                    ctx._raw.append({"role": "user", "content": f"[System Warning] {hint}"})
                    continue

                # Snapshot + track before write
                self._snapshot_if_write(tool, payload, registry)
                self._pretrack(tool, payload, tracker)

                phash = hashlib.md5(payload[:200].encode()).hexdigest()[:8]
                loop_guard.record(tool, phash)
                if loop_guard.is_looping():
                    hint = loop_guard.hint()
                    yield {"type": "loop_warn", "text": hint}
                    ctx._raw.append({"role": "user", "content": f"[System Warning] {hint}"})
                    continue

                # Emit step_start if we have a tracked plan step
                if plan_steps and step_index < len(plan_steps):
                    yield {
                        "type":  "step_start",
                        "index": step_index,
                        "label": plan_steps[step_index],
                        "tool":  tool,
                    }

                # Track bash commands
                if tool == "BashTool":
                    commands_run.append(payload[:120])

                yield {"type": "tool_call", "tool": tool, "payload": payload[:400]}

                # Auto-retry with LLM correction on failure
                tr = execute_tool(tool, payload, root)
                attempt = 1
                while not tr.success and attempt < MAX_RETRIES:
                    attempt += 1
                    yield {"type": "retry", "attempt": attempt,
                           "tool": tool, "error": tr.output[:300]}
                    # Ask the LLM to correct and produce a new tool call
                    fix_msgs = ctx.build(user_prompt, root) + [
                        {"role": "assistant", "content": response},
                        {
                            "role": "user",
                            "content": (
                                f"[Tool Error — {tool} attempt {attempt - 1}]\n"
                                f"{tr.output}\n\nFix the error and retry with a corrected tool call."
                            ),
                        },
                    ]
                    fix_resp = ""
                    try:
                        for chunk in llm.chat_stream(fix_msgs, turn_type=_mode_tt):
                            fix_resp += chunk
                    except Exception:
                        break
                    new_calls = parse_tools(fix_resp)
                    if new_calls:
                        tool, payload = new_calls[0]
                        self._snapshot_if_write(tool, payload, registry)
                        self._pretrack(tool, payload, tracker)
                        yield {"type": "tool_call", "tool": tool, "payload": payload[:400]}
                    tr = execute_tool(tool, payload, root)
                    tr.attempt = attempt

                self._posttrack(tool, payload, tracker)
                results = [tr]

            else:
                # Parallel multi-tool execution
                # Filter out write tools in read-only mode before dispatch
                if _read_only:
                    allowed, blocked = [], []
                    for t, p in calls:
                        if t in _WRITE_TOOLS:
                            blocked.append((t, p))
                        else:
                            allowed.append((t, p))
                    for t, p in blocked:
                        block_msg = (
                            f"[Read-only mode] Tool '{t}' is not permitted in "
                            f"{mode_override or 'current'} mode. Only read/search tools are allowed."
                        )
                        errors_encountered.append(block_msg)
                        yield {"type": "tool_result", "tool": t, "result": block_msg,
                               "elapsed": 0.0, "success": False, "attempt": 1}
                    calls = allowed

                if not calls:
                    # All tools were blocked — feed a message and continue
                    ctx._raw.append({"role": "assistant",
                                     "content": response.strip() or "(empty)"})
                    ctx._raw.append({"role": "user",
                                     "content": "[System] All requested tools were blocked by read-only mode."})
                    full_content.append(response)
                    continue

                yield {"type": "thinking", "text": f"⚡ Parallel: {len(calls)} tools"}

                # Emit step_start for first plan step before parallel dispatch
                if plan_steps and step_index < len(plan_steps):
                    yield {
                        "type":  "step_start",
                        "index": step_index,
                        "label": plan_steps[step_index],
                        "tool":  calls[0][0] if calls else "",
                    }

                for t, p in calls:
                    self._snapshot_if_write(t, p, registry)
                    self._pretrack(t, p, tracker)
                    # Track bash commands
                    if t == "BashTool":
                        commands_run.append(p[:120])
                    # Loop detection on all parallel tools
                    h = hashlib.md5(p[:200].encode()).hexdigest()[:8]
                    loop_guard.record(t, h)

                futures = {
                    self._pool.submit(execute_tool, t, p, root): (t, p)
                    for t, p in calls
                }
                results = []
                for f in as_completed(futures, timeout=600):
                    t, p = futures[f]
                    yield {"type": "tool_call", "tool": t, "payload": p[:400]}
                    try:
                        r = f.result()
                    except Exception as exc:
                        r = ToolResult(t, p, f"Parallel error: {exc}", 0.0, False)
                    results.append(r)
                    self._posttrack(t, p, tracker)

            # ── emit results + recovery ───────────────────────────────────────
            result_msgs: list[str] = []
            for tr in results:
                yield {
                    "type":    "tool_result",
                    "tool":    tr.tool,
                    "result":  tr.output,
                    "elapsed": tr.elapsed,
                    "success": tr.success,
                    "attempt": tr.attempt,
                }
                # Collect real errors
                if not tr.success:
                    errors_encountered.append(f"{tr.tool}: {tr.output[:150]}")
                hint = state.record(tr)
                if hint:
                    yield {"type": "recovery", "message": hint}
                result_msgs.append(
                    f"[{tr.tool} ▶ Result]\n{tr.output}" + (f"\n{hint}" if hint else "")
                )

            # ── step tracking: one advancement per turn, not per tool result ──
            # A single plan step corresponds to one agent turn (whether single or parallel).
            if plan_steps and step_index < len(plan_steps):
                all_ok = all(tr.success for tr in results)
                final_attempt = max((tr.attempt for tr in results), default=1)
                if all_ok:
                    yield {
                        "type":  "step_done",
                        "index": step_index,
                        "label": plan_steps[step_index],
                    }
                else:
                    worst = next((tr for tr in results if not tr.success), results[0])
                    yield {
                        "type":    "step_failed",
                        "index":   step_index,
                        "label":   plan_steps[step_index],
                        "error":   worst.output[:200],
                        "attempt": final_attempt,
                    }
                if all_ok or final_attempt >= MAX_RETRIES:
                    step_index += 1

            # ── update context + optional compression ─────────────────────────
            ctx.add_turn(TurnRecord(
                turn_num  = turn + 1,
                phase     = state.current_phase.value,
                assistant = response.strip() or "(empty)",
                tools     = [t for t, _ in calls],
                results   = [_compress(r.output) for r in results],
            ))
            full_content.append(response)

            # Context compression for long sessions
            if len(ctx._raw) > CONTEXT_COMPRESS_AT * 2:
                comp_llm = self._llm(Phase.UPDATE)
                ctx.compress(comp_llm)
                yield {"type": "compressed", "text": "Context compressed to save tokens."}

            # Advance phase (agent may self-select via response header)
            idx = _PHASE_ORDER.index(state.current_phase)
            state.current_phase = _PHASE_ORDER[(idx + 1) % len(_PHASE_ORDER)]

        # ── persist history ───────────────────────────────────────────────────
        combined = "\n\n".join(full_content)
        self.history.append({"role": "user",     "content": user_prompt})
        self.history.append({"role": "assistant", "content": combined[:16000]})
        if len(self.history) > 80:
            self.history = self.history[-80:]

        # ── auto-write .memory.md if the agent didn't do it ───────────────────
        # Acts as a safety net — ensures project context survives between sessions
        # even if the LLM forgets to write the memory file itself.
        _mem_path = root / ".memory.md"
        if not _mem_path.exists() and state.files_changed:
            try:
                from datetime import datetime as _dt
                _mem_path.write_text(
                    "# Project Memory\n"
                    f"Updated: {_dt.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    "## What exists\n"
                    f"Language: {state.lang}\n"
                    f"Files in workspace: {', '.join(state.files_changed)}\n\n"
                    "## What was done last session\n"
                    f"- Mode: {mode_override or 'auto'}\n"
                    f"- Turns taken: {state.total_turns}\n"
                    f"- Files modified: {', '.join(state.files_changed)}\n\n"
                    "## User preferences\n"
                    "- (Update this section as you learn more)\n\n"
                    "## Known issues / next steps\n"
                    "- (Update as tasks are completed)\n",
                    encoding="utf-8",
                )
            except Exception:
                pass

        if not self._stop.is_set():
            files_changed = state.files_changed
            file_diffs    = tracker.summaries()
            yield {
                "type":          "done",
                "turns":         state.total_turns,
                "files_changed": files_changed,
                "file_diffs":    file_diffs,
                "tools_used":    state.total_tools_used,
                "lang":          state.lang,
                "history_len":   len(self.history) // 2,
            }
            # Build plain-English result statement
            if files_changed:
                _result_stmt = (
                    f"Task completed in {state.total_turns} turn(s). "
                    f"Modified {len(files_changed)} file(s): {', '.join(files_changed[:5])}"
                    + (f" and {len(files_changed) - 5} more" if len(files_changed) > 5 else ".")
                )
            else:
                _result_stmt = f"Task completed in {state.total_turns} turn(s) with no file modifications."
            # Emit structured done_summary for UI summary card
            yield {
                "type":               "done_summary",
                "turns":              state.total_turns,
                "files_changed":      files_changed,
                "file_diffs":         file_diffs,
                "commands_run":       commands_run,
                "steps_total":        len(plan_steps),
                "steps_done":         step_index,
                "lang":               state.lang,
                "mode":               mode_override or "ultra",
                "errors_encountered": errors_encountered,
                "result_statement":   _result_stmt,
            }


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

def create_ultra_worker(model: str = DEFAULT_MODEL) -> UltraWorker:
    """Preferred entry point — instantiates and returns a ready UltraWorker."""
    return UltraWorker(model=model)