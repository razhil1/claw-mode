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
    tool_file_move,
    tool_file_copy,
    tool_file_info,
    tool_list_dir,
    tool_tree,
    tool_search,
    tool_grep,
    tool_glob,
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
    FRONTIER = "frontier"   # deepseek-r1 70B        — deep reasoning
    SMART    = "smart"      # qwen2.5-coder 32B      — code-focused execution
    FAST     = "fast"       # llama-3.1 8B           — quick / cheap tasks


_TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.FRONTIER: "nvidia:deepseek-r1-distill-llama-70b",
    ModelTier.SMART:    "nvidia:qwen2.5-coder-32b",
    ModelTier.FAST:     "nvidia:phi-4-mini-instruct",
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
    Uses weighted keyword scoring for more accurate classification.

    Returns one of: "debugging", "coding", "thinking", "default"
    """
    p = prompt.lower()

    scores = {"debugging": 0, "coding": 0, "thinking": 0}

    debug_strong = ["debug", "traceback", "stack trace", "stacktrace", "exception",
                    "segfault", "core dump", "undefined is not", "typeerror",
                    "cannot read property", "null pointer", "panic at"]
    debug_medium = ["fix", "error", "bug", "broken", "crash", "fail", "wrong",
                    "not working", "doesn't work", "issue", "problem"]

    code_strong = ["create", "build", "implement", "scaffold", "generate", "develop",
                   "set up", "setup", "bootstrap", "write a program", "write an app",
                   "make a website", "make an api", "write a script", "code a"]
    code_medium = ["add", "make", "write", "update", "modify", "change", "edit",
                   "refactor", "improve", "optimise", "optimize", "clean", "upgrade"]

    think_strong = ["explain", "describe", "analyse", "analyze", "review",
                    "summarise", "summarize", "what is", "how does", "why does",
                    "compare", "difference between"]
    think_medium = ["read", "understand", "tell me", "show me", "walk me through"]

    for w in debug_strong:
        if w in p: scores["debugging"] += 3
    for w in debug_medium:
        if w in p: scores["debugging"] += 1
    for w in code_strong:
        if w in p: scores["coding"] += 3
    for w in code_medium:
        if w in p: scores["coding"] += 1
    for w in think_strong:
        if w in p: scores["thinking"] += 3
    for w in think_medium:
        if w in p: scores["thinking"] += 1

    if p.startswith("fix ") or p.startswith("debug "):
        scores["debugging"] += 5
    if p.startswith("build ") or p.startswith("create ") or p.startswith("make "):
        scores["coding"] += 5

    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return "default"
    return best


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

_PROMPT_TEMPLATE = """\
You are NEXUS — an elite autonomous software engineer. You write production-grade \
code that works on the first try. You think like a senior architect, code like a \
10x developer, and debug like a forensic analyst.

Your workspace is 'agent_workspace/'. Full read/write/exec access.

━━━ WORKSPACE ━━━
Language : {lang}
Commands : {hints}

━━━ CORE IDENTITY ━━━
You are not a chatbot. You are a coding machine. When a user asks you to build \
something, you deliver complete, working, deployable code. You understand:
• System design — how components connect, data flows, API contracts
• Implementation — idiomatic patterns, efficient algorithms, clean architecture
• Debugging — root cause analysis, not symptom patching
• User intent — what they actually need, not just what they literally said

━━━ HOW YOU THINK ━━━
Before writing any code, reason through these in a <thought> block:
1. What is the user really asking for? (Parse intent, not just keywords)
2. What exists in the workspace? (Read before assuming)
3. What architecture fits this problem? (Design patterns, data flow, APIs)
4. What are the edge cases and failure modes?
5. What's the simplest correct solution?

━━━ HOW YOU CODE ━━━
• Write COMPLETE implementations. Every function body filled. Every import resolved.
• Handle errors properly — typed exceptions, meaningful messages, graceful degradation.
• Follow the language's idioms: Pythonic Python, idiomatic JS/TS, Rustic Rust.
• Structure code logically — separation of concerns, single responsibility.
• Name things precisely — variables, functions, files should self-document.
• When building UIs: responsive, accessible, visually polished. Use modern CSS.
• When building APIs: proper HTTP methods, status codes, validation, error responses.
• When building backends: connection pooling, proper async, input sanitisation.

━━━ HOW YOU OPERATE ━━━
1. PLAN (turn 0 only): Read .memory.md if it exists, scan workspace, then output:
   PLAN:
   1. [STEP] <action>
   Approach: <why this strategy>
2. EXECUTE: Follow your plan step by step. One tool call per turn.
   After each tool result, proceed IMMEDIATELY to the NEXT step.
   Do NOT re-read the workspace, re-plan, or repeat earlier steps.
   Do NOT go back to the top. Move FORWARD through the plan.
3. VERIFY: After the last step, run a syntax check or test.
4. DONE: Output a clean summary (see COMPLETION FORMAT below).

━━━ TOOL REFERENCE ━━━
TOOL: ListDirTool       | <path>
TOOL: TreeTool          | <path> ::: <max_depth>
TOOL: FileReadTool      | <path>
TOOL: FileEditTool      | <path> ::: <complete_file_content>
TOOL: FilePatchTool     | <path> ::: <exact_old_block> === <new_block>
TOOL: FileDeleteTool    | <path>
TOOL: FileMoveTool      | <source_path> ::: <destination_path>
TOOL: FileCopyTool      | <source_path> ::: <destination_path>
TOOL: FileInfoTool      | <path>
TOOL: BashTool          | <shell_command>
TOOL: SearchTool        | <path> ::: <regex>
TOOL: GrepTool          | <path> ::: <regex_pattern>
TOOL: GlobTool          | <glob_pattern>
TOOL: ViewLinesTool     | <path> ::: <start>,<end>
TOOL: ThinkTool         | <reasoning>
TOOL: LintTool          | <path>
TOOL: FormatTool        | <path>
TOOL: TestRunTool       | <test_command>
TOOL: DepsInstall       | <package_manager_command>
TOOL: GitTool           | <git_subcommand_and_args>
TOOL: WorkspaceZipTool  | <backup_name.zip>
TOOL: WorkspaceUnzipTool| <backup_name.zip>

━━━ TOOL CALL FORMAT (CRITICAL) ━━━
Each tool call must be on its OWN line. The pipe (|) separates tool name from argument.
The argument is ONLY the input — never include expected output, plans, or commentary.

CORRECT:   TOOL: ListDirTool | .
WRONG:     TOOL: ListDirTool | . → expected output here
WRONG:     TOOL: ListDirTool | . ### Step 2: ...

After a TOOL line, STOP. Wait for the result before continuing.

━━━ EDITING DISCIPLINE ━━━
• READ before WRITE. Always. No exceptions.
• FilePatchTool for surgical edits. FileEditTool only for new files or full rewrites.
• Match whitespace and indentation exactly when patching.
• Verify every write immediately with a syntax check or test.

━━━ ADVANCED CODING KNOWLEDGE ━━━
ARCHITECTURE PATTERNS:
• MVC/MVVM for web apps. Clean Architecture for complex backends.
• Repository pattern for data access. Factory pattern for object creation.
• Observer/pub-sub for event-driven systems. Strategy for swappable algorithms.
• Middleware chains for request processing. Circuit breaker for external services.

LANGUAGE MASTERY:
• Python: dataclasses, type hints, context managers, generators, asyncio, \
  pathlib, f-strings, list/dict comprehensions, decorators, ABC.
• JavaScript/TypeScript: async/await, destructuring, optional chaining, \
  Map/Set, Proxy, generators, template literals, modules, strict mode.
• React: hooks (useState/useEffect/useCallback/useMemo/useRef), custom hooks, \
  context, portals, error boundaries, suspense, server components.
• SQL: CTEs, window functions, indexes, joins, transactions, prepared statements.
• APIs: REST (resources/verbs/status), GraphQL (schemas/resolvers), WebSocket.
• DevOps: Docker (multi-stage builds), CI/CD, env vars, secrets management.

DEBUGGING PROTOCOL:
1. Reproduce: get the exact error message and stack trace
2. Isolate: find the smallest code path that triggers the bug
3. Hypothesise: form a theory about root cause based on the error
4. Verify: add logging/assertions to confirm the theory
5. Fix: make the minimal change that addresses root cause
6. Regression: ensure the fix doesn't break anything else

SECURITY NON-NEGOTIABLES:
• Never hardcode secrets, API keys, or tokens
• Sanitise all user input — SQL injection, XSS, path traversal
• Use parameterised queries, never string concatenation for SQL
• Validate and escape output in templates
• Use HTTPS, CORS, CSP headers, rate limiting

━━━ UNDERSTANDING USER REQUESTS ━━━
Users often describe what they want imprecisely. Your job is to infer intent:
• "make a login page" → full auth system (form, validation, session, password hash)
• "add a database" → schema design, migrations, connection pooling, CRUD operations
• "fix the bug" → reproduce, diagnose root cause, patch, verify, check for regressions
• "make it look better" → modern design, spacing, typography, colour, responsiveness
• "optimise this" → profile first, identify bottleneck, apply targeted fix, benchmark
Always deliver more than the minimum. Anticipate what they'll need next.

━━━ SESSION MEMORY ━━━
• If .memory.md exists, read it on turn 0 for context.
• After completing work, silently update .memory.md via FileEditTool.
• Do NOT output memory contents, context summaries, or workspace trees in your \
response to the user. Keep your final output clean.

━━━ COMPLETION FORMAT ━━━
When finished, end with a DONE: block. Keep it concise and professional:
DONE:
Summary: <1-2 sentence description of what was accomplished>
Files: <comma-separated list of files created or modified>
Verified: <yes/no — whether you ran a syntax check or test>

Do NOT include:
• Raw file contents or code blocks in the DONE message
• Workspace trees, context dumps, or .memory.md contents
• Long explanations — the code speaks for itself

━━━ SEQUENTIAL EXECUTION (CRITICAL) ━━━
After each tool result:
1. Read the result
2. Write one brief "→" interpretation line
3. Immediately proceed to the NEXT step in your plan
4. Issue the next TOOL: call

NEVER: Re-list the workspace. Re-read files you already read. Re-output the plan. \
Repeat orientation steps. Output filler text between steps. Go back to step 1.

━━━ SAFETY ━━━
• Never delete files unless explicitly instructed.
• Never expose .env values in output.
• Use ThinkTool before risky operations.
"""


def build_system_prompt(lang: str, mode: str = "") -> str:
    hints = _LANG_RUN_HINTS.get(lang, "No language-specific hints available.")
    base  = _PROMPT_TEMPLATE.format(lang=lang, hints=hints)
    mode_addenda: dict[str, str] = {
        "builder": (
            "\n\n══ BUILDER MODE ══\n"
            "You are building software from scratch or adding major features.\n"
            "• Design the architecture FIRST — file structure, data models, API contracts.\n"
            "• Write complete, production-ready implementations — no stubs, no placeholders.\n"
            "• Include proper error handling, input validation, and edge case coverage.\n"
            "• Set up dependency management (requirements.txt, package.json, etc.).\n"
            "• Create a README with setup instructions and usage examples.\n"
            "• If building a web app: responsive layout, clean UI, proper routing.\n"
            "• If building an API: OpenAPI-style docs, proper status codes, rate limiting.\n"
            "• If building a CLI: argument parsing, help text, exit codes.\n"
            "Priority tools: FileEditTool, BashTool, FilePatchTool, DepsInstall."
        ),
        "debugger": (
            "\n\n══ DEBUGGER MODE ══\n"
            "You are a forensic debugger. Find and fix the root cause, not symptoms.\n"
            "Protocol:\n"
            "1. REPRODUCE: Run the failing code. Get the exact error and stack trace.\n"
            "2. ISOLATE: Trace the execution path. Read the relevant source files.\n"
            "3. HYPOTHESISE: Form a theory about WHY it fails based on the evidence.\n"
            "4. FIX: Apply the minimal, targeted change that addresses root cause.\n"
            "5. VERIFY: Confirm the fix works AND nothing else broke.\n"
            "• Never mask errors with bare try/except.\n"
            "• Check for off-by-one, null/undefined, race conditions, type mismatches.\n"
            "• Look at imports, dependencies, environment — not just the code.\n"
            "Priority tools: BashTool, FileReadTool, SearchTool, FilePatchTool."
        ),
        "refactorer": (
            "\n\n══ REFACTORER MODE ══\n"
            "You are improving code quality without changing external behaviour.\n"
            "• Run existing tests FIRST to establish a baseline.\n"
            "• Apply DRY, SOLID, KISS — but don't over-engineer.\n"
            "• Extract functions/classes when logic is repeated or deeply nested.\n"
            "• Improve naming — variables and functions should read like documentation.\n"
            "• Remove dead code, unused imports, commented-out blocks.\n"
            "• Add type hints/annotations where missing.\n"
            "• Run tests AFTER to verify no regressions.\n"
            "Priority tools: FileReadTool, FilePatchTool, SearchTool, TestRunTool."
        ),
        "researcher": (
            "\n\n══ RESEARCHER MODE ══\n"
            "You are analysing code to understand and explain it. READ ONLY.\n"
            "• Map the full architecture: entry points, data flow, dependencies.\n"
            "• Cite exact file paths and line numbers in your explanations.\n"
            "• Structure: Overview → Architecture → Key Components → Data Flow → Summary.\n"
            "• Be honest about uncertainty — say 'I'm not sure' rather than guess.\n"
            "Priority tools: SearchTool, FileReadTool, ListDirTool, GrepTool."
        ),
        "reviewer": (
            "\n\n══ REVIEWER MODE ══\n"
            "You are auditing code for quality, bugs, and security. READ ONLY.\n"
            "• Rate findings: CRITICAL | HIGH | MEDIUM | LOW\n"
            "• Check: correctness, security, performance, error handling, test coverage.\n"
            "• Look for: SQL injection, XSS, hardcoded secrets, missing validation,\n"
            "  race conditions, memory leaks, unclosed resources, N+1 queries.\n"
            "• Provide concrete fix code for each finding.\n"
            "• End with: overall grade (A-F) and 1-paragraph summary.\n"
            "Priority tools: FileReadTool, SearchTool, ListDirTool, GrepTool."
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
    """Detect when the agent is stuck repeating the same tool call or cycling
    between a small set of identical calls."""

    def __init__(self, window: int = LOOP_DETECT_WINDOW) -> None:
        self._window  = window
        self._history: list[str] = []
        self._loop_count = 0
        self._recovery_turns = 0

    def record(self, tool_name: str, payload_hash: str) -> None:
        self._history.append(f"{tool_name}:{payload_hash}")
        if len(self._history) > self._window * 4:
            self._history = self._history[-self._window * 4:]
        if self._recovery_turns > 0:
            self._recovery_turns -= 1

    def _check_pattern(self, pattern_len: int) -> bool:
        needed = pattern_len * 3
        if len(self._history) < needed:
            return False
        pattern = self._history[-pattern_len:]
        for offset in range(1, 3):
            start = -(pattern_len * (offset + 1))
            end   = -(pattern_len * offset)
            if self._history[start:end] != pattern:
                return False
        return True

    def is_looping(self) -> bool:
        if self._recovery_turns > 0:
            return False
        if len(self._history) < self._window:
            return False
        recent = self._history[-self._window:]
        if len(set(recent)) == 1:
            self._loop_count += 1
            self._recovery_turns = 3
            return True
        for plen in (2, 3):
            if self._check_pattern(plen):
                self._loop_count += 1
                self._recovery_turns = 3
                return True
        return False

    @property
    def should_force_stop(self) -> bool:
        return self._loop_count >= 3

    def hint(self) -> str:
        if self._loop_count >= 3:
            return (
                "LOOP DETECTED 3 TIMES: You are stuck in a cycle. STOP NOW. "
                "Emit a DONE block with what you have completed so far."
            )
        if self._loop_count >= 2:
            return (
                "LOOP DETECTED AGAIN: You are repeating the same actions. "
                "Try a completely different approach or emit DONE."
            )
        return (
            f"⚠️ Loop detected — same calls repeated over {self._window} turns. "
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
    if low.startswith(("error", "tool error", "security error")):
        return True
    _exit_m = re.match(r"\[exit code (\d+)\]", low)
    if _exit_m and int(_exit_m.group(1)) != 0:
        return True
    _ERROR_SIGNALS = (
        "permission denied", "no such file", "command not found",
        "traceback (most recent", "syntaxerror:", "nameerror:",
        "typeerror:", "importerror:", "modulenotfounderror:",
        "filenotfounderror:", "valueerror:", "keyerror:",
        "indentationerror:", "attributeerror:",
        "fatal error", "segmentation fault",
        "cannot find module", "enoent:", "eacces:",
        "search block not found",
    )
    return any(sig in low for sig in _ERROR_SIGNALS)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════

_PARAM_STRIP = re.compile(
    r"^(?:path|file|filepath|directory|dir|command|cmd|query|pattern|content|input|args?)\s*[:=]\s*",
    re.IGNORECASE,
)

_KNOWN_TOOLS = {
    "ListDirTool", "TreeTool", "FileReadTool", "ViewLinesTool", "SearchTool",
    "GrepTool", "GlobTool",
    "FileEditTool", "FilePatchTool", "FileDeleteTool",
    "FileMoveTool", "FileCopyTool", "FileInfoTool",
    "BashTool", "ThinkTool",
    "LintTool", "FormatTool", "TestRunTool", "DepsInstall", "GitTool",
    "WorkspaceZipTool", "WorkspaceUnzipTool",
}

_SINGLE_ARG_TOOLS_UW = {
    "ListDirTool", "FileReadTool", "FileDeleteTool",
    "LintTool", "FormatTool",
    "WorkspaceZipTool", "WorkspaceUnzipTool",
}


def _sanitize_simple_payload(payload: str) -> str:
    """For single-argument tools, strip trailing junk the LLM may have appended."""
    s = payload.strip().split("\n")[0].strip()
    s = re.split(r'\s*[→►▶]\s*', s)[0].strip()
    s = re.split(r'\s*```', s)[0].strip()
    s = re.split(r'\s*###?\s', s)[0].strip()
    s = re.split(r'\s*\(this ', s, flags=re.IGNORECASE)[0].strip()
    s = s.strip("`'\"")
    return s


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
    if tool in _SINGLE_ARG_TOOLS_UW:
        payload = _sanitize_simple_payload(payload)
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

        elif tool == "TreeTool":
            parts = payload.split(":::", 1)
            path = parts[0].strip() if parts[0].strip() else "."
            depth = 4
            if len(parts) > 1:
                try: depth = int(parts[1].strip())
                except ValueError: pass
            out = tool_tree(path, depth)

        elif tool == "GrepTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                out = tool_grep(parts[0].strip(), parts[1].strip())
            else:
                out = tool_grep(".", payload)

        elif tool == "GlobTool":
            out = tool_glob(payload.strip())

        elif tool == "FileInfoTool":
            out = tool_file_info(payload.strip())

        elif tool == "FileDeleteTool":
            out = tool_file_delete(payload)

        elif tool == "FileMoveTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                out = tool_file_move(parts[0].strip(), parts[1].strip())
            else:
                out = "Error: FileMoveTool requires 'source ::: destination'."; ok = False

        elif tool == "FileCopyTool":
            parts = payload.split(":::", 1)
            if len(parts) == 2:
                out = tool_file_copy(parts[0].strip(), parts[1].strip())
            else:
                out = "Error: FileCopyTool requires 'source ::: destination'."; ok = False

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
        
    calls = []
    blocks = list(re.finditer(r"```[a-zA-Z0-9_-]*\n([\s\S]*?)```", text))
    for b in blocks:
        code = b.group(1)
        first_line = code.split("\n", 1)[0].strip()
        name_match = re.match(
            r"^(?://|#|/\*|<!--)\s*([a-zA-Z0-9_\-\./\\]+\.\w{1,10})",
            first_line,
        )
        if name_match:
            filename = name_match.group(1)
            pure_code = code[len(first_line):].strip()
            calls.append(("FileEditTool", f"{filename} ::: {pure_code}"))
            continue

        preceding = text[:b.start()]
        last_line = preceding.rstrip().rsplit("\n", 1)[-1].strip()
        ctx_match = re.search(
            r"(?:`([a-zA-Z0-9_\-\./\\]+\.\w{1,10})`|"
            r"(?:create|write|save|update|file)\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?"
            r"(?:called\s+|named\s+|at\s+)?"
            r"[`'\"]?([a-zA-Z0-9_\-\./\\]+\.\w{1,10})[`'\"]?)",
            last_line, re.IGNORECASE,
        )
        if ctx_match:
            filename = ctx_match.group(1) or ctx_match.group(2)
            calls.append(("FileEditTool", f"{filename} ::: {code.strip()}"))

    return calls


def strip_tools(text: str) -> str:
    return re.sub(r"\n?TOOL:[\s\S]*", "", text).strip()


def _validate_done_claims_uw(done_text: str, root: Path) -> list[str]:
    """Check if files claimed in DONE actually exist in workspace."""
    done_section = re.search(r"DONE\s*:[\s\S]*", done_text, re.IGNORECASE)
    if not done_section:
        return []
    section = done_section.group(0)

    mentioned = set()
    for m in re.finditer(
        r"(?:^|[\s,`'\"])([a-zA-Z0-9_\-\./]+\.\w{1,10})(?:[\s,`'\"]|$)",
        section,
    ):
        fname = m.group(1).strip(".,`'\"")
        if fname and not fname.startswith("."):
            if "/" in fname or "." in fname:
                if not any(x in fname for x in ["http", "localhost", "0.0.0", "127."]):
                    mentioned.add(fname)
    missing = []
    for f in mentioned:
        if not (root / f).exists() and re.match(r"^[\w\-/]+\.\w{1,10}$", f):
            missing.append(f)
    return missing[:8]


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
        self._no_tool_nudges = 0

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

            if state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                yield {
                    "type":    "error",
                    "message": f"Halted: {state.consecutive_errors} consecutive errors.",
                }
                break

            if loop_guard.should_force_stop:
                yield {
                    "type":    "error",
                    "message": "Agent stopped: stuck in a repetitive loop. Try rephrasing or breaking the task into smaller parts.",
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
                if detect_done(response):
                    missing = _validate_done_claims_uw(response, root)
                    if missing and turn < _max_turns - 2:
                        nudge = (
                            f"[System] DONE rejected. These files were claimed but do NOT exist: "
                            f"{', '.join(missing)}\n"
                            "Files are only created via TOOL: FileEditTool | <path> ::: <content>.\n"
                            "Create the missing files now."
                        )
                        ctx._raw.append({"role": "assistant", "content": response.strip()})
                        ctx._raw.append({"role": "user", "content": nudge})
                        yield {"type": "nudge", "text": f"{len(missing)} claimed files missing. Requesting creation..."}
                        full_content.append(response)
                        continue

                if not detect_done(response) and self._no_tool_nudges < 2 and turn < _max_turns - 2:
                    self._no_tool_nudges += 1
                    nudge = (
                        "[System] You wrote text but did NOT call any tools. "
                        "Files are NOT created until you use the tools.\n"
                        "Use: TOOL: FileEditTool | <path> ::: <content>\n"
                        "Proceed with actual tool calls now."
                    )
                    ctx._raw.append({"role": "assistant", "content": response.strip() or "(empty)"})
                    ctx._raw.append({"role": "user", "content": nudge})
                    yield {"type": "nudge", "text": "Reminding agent to use tools..."}
                    full_content.append(response)
                    continue

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

                phash = hashlib.md5(payload[:200].encode()).hexdigest()[:8]
                loop_guard.record(tool, phash)
                if loop_guard.is_looping():
                    hint = loop_guard.hint()
                    yield {"type": "loop_warn", "text": hint}
                    ctx._raw.append({"role": "assistant", "content": response.strip() or "(empty)"})
                    ctx._raw.append({"role": "user", "content": f"[System Warning] {hint}"})
                    full_content.append(response)
                    if loop_guard.should_force_stop:
                        yield {"type": "error", "message": "Agent stopped: stuck in a repetitive loop."}
                        break
                    continue

                self._snapshot_if_write(tool, payload, registry)
                self._pretrack(tool, payload, tracker)

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
                    if t == "BashTool":
                        commands_run.append(p[:120])
                    h = hashlib.md5(p[:200].encode()).hexdigest()[:8]
                    loop_guard.record(t, h)

                if loop_guard.is_looping():
                    hint = loop_guard.hint()
                    yield {"type": "loop_warn", "text": hint}
                    ctx._raw.append({"role": "assistant", "content": response.strip() or "(empty)"})
                    ctx._raw.append({"role": "user", "content": f"[System Warning] {hint}"})
                    full_content.append(response)
                    if loop_guard.should_force_stop:
                        yield {"type": "error", "message": "Agent stopped: stuck in a repetitive loop."}
                        break
                    continue

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

            # ── update context + progress hint ─────────────────────────────────
            progress_hint = ""
            if plan_steps and step_index < len(plan_steps):
                progress_hint = f"\n[Progress: step {step_index + 1}/{len(plan_steps)} — next: {plan_steps[step_index]}. Proceed immediately.]"
            elif plan_steps and step_index >= len(plan_steps):
                progress_hint = "\n[All plan steps complete. Verify your work, then output DONE:]"

            turn_results = [_compress(r.output) for r in results]
            if progress_hint and turn_results:
                turn_results[-1] += progress_hint

            ctx.add_turn(TurnRecord(
                turn_num  = turn + 1,
                phase     = state.current_phase.value,
                assistant = response.strip() or "(empty)",
                tools     = [t for t, _ in calls],
                results   = turn_results,
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