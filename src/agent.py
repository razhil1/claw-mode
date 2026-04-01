"""
ClawAgent — Multi-turn agentic loop with tool execution and smart model routing.
Each call to run_streaming() yields SSE event dicts describing every step.
"""
from __future__ import annotations

import re
import time
import threading
from typing import Generator

from .llm import LLMClient, DEFAULT_MODEL, get_all_models, set_runtime_key
from .toolbox import (
    tool_bash_run,
    tool_file_read,
    tool_file_edit,
    tool_file_delete,
    tool_list_dir,
    tool_search,
    tool_view_file_lines,
)

MAX_AGENT_TURNS = 18

TOOL_PATTERN = re.compile(
    r"TOOL:\s*(\w+)\s*\|+\s*([\s\S]*?)(?=\nTOOL:|\Z)", re.MULTILINE
)

SYSTEM_PROMPT = """You are Claw, an elite autonomous AI coding agent inside a secure isolated workspace.
You are a Staff Full-Stack Engineer with deep mastery of HTML/CSS/JS, React, Vue, Tailwind, Python, Node.js, Flask, Express, SQL, and systems design.

## HOW YOU WORK
You work in an agentic loop: think → use a tool → observe the result → think → next tool → ... → final answer.
You ALWAYS use tools to gather information before acting. You NEVER guess file contents — you read them first.

## THINKING FORMAT
Before each tool call, briefly wrap your reasoning in <thought>...</thought> (2-4 sentences max).

## TOOL CALL FORMAT  (follow EXACTLY — one tool per turn)
After your thought:
  TOOL: ToolName | payload

FileEditTool uses triple-pipe separator:
  TOOL: FileEditTool | path ||| <full file content>

## AVAILABLE TOOLS
| Tool             | Payload format                     | Use when                              |
|------------------|------------------------------------|---------------------------------------|
| ListDirTool      | path (e.g. `.` or `src/`)          | Explore directory structure           |
| FileReadTool     | filepath                           | Read a complete file                  |
| ViewFileLinesTool| filepath ||| start,end             | Read a range of lines from large file |
| SearchTool       | path ||| regex                     | Search for a pattern across files     |
| FileEditTool     | filepath ||| <full file content>    | Create or overwrite a file            |
| FileDeleteTool   | filepath                           | Delete a file or directory            |
| BashTool         | shell command                      | Run npm, pip, node, python, etc.      |

## WORKFLOW RULES
1. ALWAYS scan with ListDirTool or FileReadTool BEFORE editing any existing file.
2. Write COMPLETE files with FileEditTool — never emit partial snippets or diffs.
3. After creating files, confirm what was built and what the user should see in the preview.
4. Use BashTool for: installing packages, running tests, checking outputs.
5. Build beautiful, modern, production-quality UIs by default: CSS variables, smooth transitions, dark theme.
6. Self-contained HTML/CSS/JS in a single file unless the user specifies otherwise.
7. NEVER ask the user for clarification — make smart decisions and act.
8. Include error handling, loading states, and empty states in every interactive UI.

## STOPPING
When you have no more tool calls to make, write your final answer WITHOUT any TOOL: line. This ends the loop.

## QUALITY STANDARDS
- HTML: Semantic, accessible, responsive (CSS Grid/Flexbox, CSS variables)
- CSS: Mobile-first, modern aesthetics, consistent color palette
- JavaScript: Vanilla ES6+; use React/Vue only when asked
- Python: PEP8, type hints, clear function names
- Always test edge cases and add defensive checks"""


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
                nums = parts[1].strip().split(",")
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
            return "Error: FileEditTool requires 'path ||| content' format."
        elif tool_name == "FileDeleteTool":
            return tool_file_delete(payload)
        elif tool_name == "BashTool":
            return tool_bash_run(payload)
        else:
            known = "ListDirTool, FileReadTool, ViewFileLinesTool, SearchTool, FileEditTool, FileDeleteTool, BashTool"
            return f"Unknown tool: {tool_name}. Available: {known}"
    except Exception as e:
        return f"Tool error ({tool_name}): {str(e)}"


def _parse_tool_call(text: str) -> tuple[str, str] | None:
    """Find first TOOL: ... in LLM response. Returns (tool_name, payload) or None."""
    match = re.search(r"TOOL:\s*(\w+)\s*\|+([\s\S]*)", text)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _strip_tool_lines(text: str) -> str:
    """Remove TOOL: ... lines from display text."""
    return re.sub(r"\nTOOL:[\s\S]*", "", text).strip()


def _clean_response(text: str) -> str:
    """Strip thought tags and tool lines from text for display."""
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text, flags=re.IGNORECASE)
    return _strip_tool_lines(text).strip()


class ClawAgent:
    """
    Stateful agent — holds conversation history across user messages.
    Supports single-model and smart multi-model routing modes.
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.history: list[dict[str, str]] = []
        self.last_error: str = ""
        self._stop_event: threading.Event = threading.Event()

    def clear_history(self):
        self.history = []

    def request_stop(self):
        self._stop_event.set()

    def clear_stop(self):
        self._stop_event.clear()

    def _build_messages(self, user_prompt: str) -> list[dict[str, str]]:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in self.history[-20:]:
            msgs.append(item)
        msgs.append({"role": "user", "content": user_prompt})
        return msgs

    def _is_smart(self) -> bool:
        return self.model.startswith("smart:")

    def run_streaming(self, user_prompt: str) -> Generator[dict, None, None]:
        """
        Generator that yields SSE event dicts.
        Types: thinking | tool_call | tool_result | token | done | error | key_error | stopped
        """
        self.clear_stop()
        llm = LLMClient(model=self.model)
        messages = self._build_messages(user_prompt)

        full_assistant_content: list[str] = []
        total_turns = 0
        files_changed: list[str] = []

        # Emit smart routing info as a hint
        if self._is_smart():
            info = SMART_MODELS.get(self.model, {})
            yield {"type": "thinking", "text": f"Smart routing: {info.get('description', self.model)}"}

        for turn in range(MAX_AGENT_TURNS):
            if self._stop_event.is_set():
                yield {"type": "stopped", "message": "Agent stopped by user.", "turns": total_turns}
                break

            total_turns = turn + 1

            # Determine turn type for smart routing
            # First turn is always planning (thinking), subsequent depend on context
            if turn == 0:
                turn_type = "thinking"
            else:
                # Peek at last tool used to predict next turn type
                last_tool = None
                for m in reversed(messages):
                    if m["role"] == "user" and m["content"].startswith("[Tool Result"):
                        last_tool = re.search(r"\[Tool Result — (\w+)\]", m["content"])
                        if last_tool:
                            last_tool = last_tool.group(1)
                        break
                if last_tool in {"ListDirTool", "FileReadTool", "ViewFileLinesTool", "SearchTool"}:
                    turn_type = "coding"
                elif last_tool in {"FileEditTool", "BashTool"}:
                    turn_type = "default"
                else:
                    turn_type = "default"

            active_label = self.model
            if self._is_smart():
                concrete = llm.route(turn_type)
                info = get_all_models().get(concrete, {})
                active_label = info.get("label", concrete)
                yield {
                    "type": "thinking",
                    "text": f"Using {info.get('emoji','')}{active_label} ({info.get('short','')}) for this step"
                }
            else:
                yield {"type": "thinking", "text": f"Thinking (turn {turn + 1})..."}

            response_text = llm.chat(messages, turn_type=turn_type)

            if not response_text:
                yield {"type": "error", "message": "Empty response from LLM. Please try again."}
                break

            # Handle structured error codes from LLM client
            if response_text.startswith("CLAW_ERROR:"):
                parts = response_text.split("|", 1)
                meta = parts[0].replace("CLAW_ERROR:", "")
                msg = parts[1] if len(parts) > 1 else response_text
                yield {"type": "key_error", "error_type": meta.strip(), "message": msg}
                break

            # Parse thinking block
            thought_match = re.search(r"<thought>([\s\S]*?)</thought>", response_text, re.IGNORECASE)
            if thought_match:
                thought_text = thought_match.group(1).strip()
                if thought_text:
                    yield {"type": "thinking", "text": thought_text}

            # Check for tool call
            tool_call = _parse_tool_call(response_text)

            if tool_call:
                tool_name, payload = tool_call

                # Emit clean prose before the tool call
                clean = _clean_response(response_text).strip()
                if clean:
                    yield {"type": "token", "text": clean}

                yield {"type": "tool_call", "tool": tool_name, "payload": payload[:300]}

                if self._stop_event.is_set():
                    yield {"type": "stopped", "message": "Agent stopped by user.", "turns": total_turns}
                    break

                # Execute the tool
                t_start = time.time()
                result = _execute_tool(tool_name, payload)
                elapsed = round(time.time() - t_start, 2)

                # Track files changed by the agent
                if tool_name == "FileEditTool":
                    fe_parts = payload.split("|||", 1)
                    if fe_parts:
                        fname = fe_parts[0].strip()
                        if fname and fname not in files_changed:
                            files_changed.append(fname)

                yield {
                    "type": "tool_result",
                    "tool": tool_name,
                    "result": result[:2000],
                    "elapsed": elapsed,
                }

                messages.append({"role": "assistant", "content": response_text})
                messages.append({
                    "role": "user",
                    "content": f"[Tool Result — {tool_name}]\n{result}"
                })
                full_assistant_content.append(response_text)
                full_assistant_content.append(f"[Tool: {tool_name}] → {result[:400]}")

            else:
                # No tool call → this is the final response
                clean_final = _clean_response(response_text)
                if clean_final:
                    yield {"type": "token", "text": clean_final}
                full_assistant_content.append(response_text)
                break

        # Persist conversation history
        combined_assistant = "\n\n".join(full_assistant_content)
        self.history.append({"role": "user", "content": user_prompt})
        self.history.append({"role": "assistant", "content": combined_assistant[:6000]})
        if len(self.history) > 30:
            self.history = self.history[-30:]

        if not self._stop_event.is_set():
            yield {
                "type": "done",
                "turns": total_turns,
                "files_changed": files_changed,
                "history_len": len(self.history) // 2,
            }
