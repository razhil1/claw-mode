"""
ClawAgent — Real multi-turn agentic loop with tool execution.
Each call to run_streaming() yields SSE events describing every step.
"""
from __future__ import annotations

import re
import time
from typing import Generator, Iterator

from .llm import LLMClient, DEFAULT_MODEL, set_runtime_key
from .toolbox import (
    tool_bash_run,
    tool_file_read,
    tool_file_edit,
    tool_file_delete,
    tool_list_dir,
    tool_search,
    tool_view_file_lines,
)

MAX_AGENT_TURNS = 14  # max LLM calls per user message
TOOL_PATTERN = re.compile(
    r"TOOL:\s*(\w+)\s*\|+\s*([\s\S]*?)(?=\nTOOL:|\Z)", re.MULTILINE
)

SYSTEM_PROMPT = """You are Claw, an elite autonomous AI coding agent inside a secure isolated workspace.
You are equivalent to a Staff Full-Stack Engineer with mastery over HTML/CSS/JS, React, Vue, Tailwind, Python, Node.js, Flask, Express, SQL, and systems design.

## HOW YOU WORK
You work in a loop: think → use a tool → see the result → think → use the next tool → ... → give the final answer.
You ALWAYS use tools before answering. You NEVER guess file contents — you read them first.

## THINKING FORMAT
Wrap your reasoning in <thought>...</thought> before each tool call. Keep thoughts concise (2-4 sentences max).

## TOOL FORMAT (CRITICAL — follow exactly)
After your thought, emit ONE tool call per turn:
TOOL: ToolName | payload

The payload for FileEditTool uses triple-pipe: TOOL: FileEditTool | path ||| content

## AVAILABLE TOOLS
- **ListDirTool** — List workspace directory. Payload: path (e.g. `.` or `src/`)
- **FileReadTool** — Read full file. Payload: filepath
- **ViewFileLinesTool** — Read line range. Payload: `filepath ||| start,end`
- **SearchTool** — Grep across files. Payload: `path ||| regex`
- **FileEditTool** — Write/overwrite file (FULL content). Payload: `filepath ||| <full file content>`
- **FileDeleteTool** — Delete file/dir. Payload: filepath
- **BashTool** — Run shell command in workspace (npm, pip, node, python, etc). Payload: command

## WORKFLOW RULES
1. ALWAYS scan with ListDirTool or FileReadTool BEFORE editing anything.
2. Write COMPLETE files with FileEditTool — never partial snippets.
3. After creating/editing files, confirm what was done and what the user should see.
4. Use BashTool for: npm install, pip install, running scripts, checking outputs.
5. Build beautiful, modern, production-quality UIs with dark themes, gradients, and smooth animations by default.
6. NEVER ask the user for clarification — figure it out and act.

## STOPPING
When you have NO more tool calls needed, write your final answer WITHOUT any TOOL: line. This ends the loop.

## QUALITY STANDARDS
- HTML: Semantic, accessible, modern (CSS Grid/Flexbox, CSS variables, transitions)
- CSS: Mobile-first, dark themes, glassmorphism or neumorphism where fitting
- JS: Vanilla ES6+ preferred; React/Vue if user asks
- Python: PEP8, type hints, docstrings
- Always include error handling, loading states, and empty states"""


def _execute_tool(tool_name: str, payload: str) -> str:
    """Dispatch tool call and return result string."""
    payload = payload.strip()
    try:
        if tool_name == "ListDirTool":
            return tool_list_dir(payload or ".")
        elif tool_name == "FileReadTool":
            return tool_file_read(payload)
        elif tool_name == "ViewFileLinesTool":
            parts = payload.split("|||", 1)
            if len(parts) == 2:
                path = parts[0].strip()
                range_str = parts[1].strip()
                nums = range_str.split(",")
                start = int(nums[0].strip())
                end = int(nums[1].strip()) if len(nums) > 1 else start + 50
                return tool_view_file_lines(path, start, end)
            return tool_file_read(payload)
        elif tool_name == "SearchTool":
            parts = payload.split("|||", 1)
            if len(parts) == 2:
                return tool_search(parts[0].strip(), parts[1].strip())
            return tool_search(".", payload)
        elif tool_name == "FileEditTool":
            parts = payload.split("|||", 1)
            if len(parts) == 2:
                return tool_file_edit(parts[0].strip(), parts[1])
            return f"Error: FileEditTool requires 'path ||| content' format."
        elif tool_name == "FileDeleteTool":
            return tool_file_delete(payload)
        elif tool_name == "BashTool":
            return tool_bash_run(payload)
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        return f"Tool error ({tool_name}): {str(e)}"


def _parse_tool_call(text: str) -> tuple[str, str] | None:
    """Find first TOOL: ... in LLM response. Returns (tool_name, payload) or None."""
    match = re.search(r"TOOL:\s*(\w+)\s*\|+([\s\S]*)", text)
    if not match:
        return None
    tool_name = match.group(1).strip()
    payload = match.group(2).strip()
    return tool_name, payload


def _strip_tool_lines(text: str) -> str:
    """Remove TOOL: ... lines from display text."""
    return re.sub(r"\nTOOL:[\s\S]*", "", text).strip()


class ClawAgent:
    """Stateful agent — holds conversation history across user messages."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.history: list[dict[str, str]] = []
        self.last_error: str = ""

    def clear_history(self):
        self.history = []

    def _build_messages(self, user_prompt: str) -> list[dict[str, str]]:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Keep last 16 history items to stay within context limits
        for item in self.history[-16:]:
            msgs.append(item)
        msgs.append({"role": "user", "content": user_prompt})
        return msgs

    def run_streaming(self, user_prompt: str) -> Generator[dict, None, None]:
        """
        Generator that yields SSE event dicts.
        Event types:
          - thinking: agent reasoning text
          - tool_call: {tool, payload}
          - tool_result: {tool, result}
          - token: streamed text chunk
          - done: final summary stats
          - error: error message
        """
        llm = LLMClient(model=self.model)
        messages = self._build_messages(user_prompt)

        full_assistant_content = []  # accumulate all turns for history
        total_turns = 0
        files_changed: list[str] = []

        for turn in range(MAX_AGENT_TURNS):
            total_turns = turn + 1

            # Call the LLM
            yield {"type": "thinking", "text": f"Thinking (turn {turn + 1})..."}

            response_text = llm.chat(messages)

            if not response_text:
                yield {"type": "error", "message": "Empty response from LLM. Please try again."}
                break

            # Structured error from LLM client (key issues, rate limits, etc.)
            if response_text.startswith("CLAW_ERROR:"):
                parts = response_text.split("|", 1)
                meta = parts[0].replace("CLAW_ERROR:", "")
                msg = parts[1] if len(parts) > 1 else response_text
                error_type = meta.split(":")[0] if ":" in meta else "error"
                yield {"type": "key_error", "error_type": error_type, "message": msg}
                break

            # Parse thinking block
            thought_match = re.search(r"<thought>([\s\S]*?)</thought>", response_text, re.IGNORECASE)
            display_text = _strip_tool_lines(response_text)

            if thought_match:
                thought_text = thought_match.group(1).strip()
                yield {"type": "thinking", "text": thought_text}

            # Check for tool call
            tool_call = _parse_tool_call(response_text)

            if tool_call:
                tool_name, payload = tool_call
                # Emit clean display text (minus the TOOL line)
                clean = _strip_tool_lines(
                    re.sub(r"<thought>[\s\S]*?</thought>", "", display_text, flags=re.IGNORECASE)
                ).strip()
                if clean:
                    yield {"type": "token", "text": clean}

                yield {"type": "tool_call", "tool": tool_name, "payload": payload[:200]}

                # Execute tool
                t_start = time.time()
                result = _execute_tool(tool_name, payload)
                elapsed = round(time.time() - t_start, 2)

                # Track changed files
                if tool_name == "FileEditTool":
                    parts = payload.split("|||", 1)
                    if parts:
                        files_changed.append(parts[0].strip())

                yield {"type": "tool_result", "tool": tool_name, "result": result[:1500], "elapsed": elapsed}

                # Feed result back into conversation
                messages.append({"role": "assistant", "content": response_text})
                messages.append({
                    "role": "user",
                    "content": f"[Tool Result — {tool_name}]\n{result}"
                })
                full_assistant_content.append(response_text)
                full_assistant_content.append(f"[Tool: {tool_name}] → {result[:300]}")

            else:
                # No tool call — this is the final response
                clean_final = re.sub(r"<thought>[\s\S]*?</thought>", "", display_text, flags=re.IGNORECASE).strip()
                if clean_final:
                    yield {"type": "token", "text": clean_final}

                full_assistant_content.append(response_text)
                break

        # Update conversation history with a summary of this exchange
        combined_assistant = "\n\n".join(full_assistant_content)
        self.history.append({"role": "user", "content": user_prompt})
        self.history.append({"role": "assistant", "content": combined_assistant[:4000]})

        # Keep history bounded
        if len(self.history) > 24:
            self.history = self.history[-24:]

        yield {
            "type": "done",
            "turns": total_turns,
            "files_changed": files_changed,
            "history_len": len(self.history) // 2
        }
