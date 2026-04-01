import os
import subprocess
import re
from pathlib import Path

def get_workspace_root():
    workspace = Path(__file__).resolve().parent.parent / "agent_workspace"
    workspace.mkdir(exist_ok=True)
    return workspace

def enforce_safe_path(path: str) -> Path:
    root = get_workspace_root()
    full_path = (root / path).resolve()
    if not str(full_path).lower().startswith(str(root).lower()):
        raise ValueError(f"Security error: Access denied to path outside of workspace ({path})")
    return full_path

def tool_file_read(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: File '{path}' not found in workspace."
        return full_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"

def tool_view_file_lines(path: str, start_line: int, end_line: int) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: File '{path}' not found in workspace."
        lines = full_path.read_text(encoding="utf-8").splitlines()
        extracted = lines[start_line-1:end_line]
        numbered = [f"{start_line + i}: {line}" for i, line in enumerate(extracted)]
        return "\n".join(numbered)
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"

def tool_file_edit(path: str, new_content: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.parent.exists():
            full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(new_content, encoding="utf-8")
        size = full_path.stat().st_size
        return f"Success: '{path}' written ({size} bytes)."
    except Exception as e:
        return f"Error writing to file '{path}': {str(e)}"

def tool_file_delete(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: '{path}' does not exist."
        if full_path.is_dir():
            import shutil
            shutil.rmtree(full_path)
            return f"Success: Directory '{path}' deleted."
        else:
            full_path.unlink()
            return f"Success: File '{path}' deleted."
    except Exception as e:
        return f"Error deleting '{path}': {str(e)}"

def tool_list_dir(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists() or not full_path.is_dir():
            return f"Error: Directory '{path}' not found in workspace."
        results = []
        for item in sorted(full_path.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if item.is_dir():
                count = sum(1 for _ in item.rglob("*") if _.is_file())
                results.append(f"📁 {item.name}/ ({count} files)")
            else:
                size = item.stat().st_size
                results.append(f"📄 {item.name} ({size} bytes)")
        return "\n".join(results) if results else "Directory is empty."
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"

def tool_search(path: str, query: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
             return f"Error: Path '{path}' not found in workspace."
        results = []
        pattern = re.compile(query, re.IGNORECASE)

        if full_path.is_file():
            files_to_search = [full_path]
        else:
            files_to_search = full_path.rglob("*")

        for f in files_to_search:
            if f.is_file() and f.stat().st_size < 500000:  # Skip large files
                try:
                    lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for i, line in enumerate(lines):
                        if pattern.search(line):
                            rel_path = f.relative_to(get_workspace_root())
                            results.append(f"{rel_path}:{i+1}: {line.strip()}")
                except:
                    pass
        return "\n".join(results[:50]) or "No matches found."
    except Exception as e:
        return f"Error searching in '{path}': {str(e)}"

def tool_bash_run(command: str) -> str:
    try:
        workspace_cwd = get_workspace_root()
        # Extended timeout for npm/pip installs
        result = subprocess.run(
            command, shell=True, cwd=str(workspace_cwd),
            capture_output=True, text=True, timeout=120
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if not output.strip():
            output = f"Command executed successfully (exit code {result.returncode})."
        return output[:5000]  # Cap output to avoid overwhelming the LLM
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"
