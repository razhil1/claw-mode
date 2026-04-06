import os
import subprocess
import re
import time
import threading
from pathlib import Path

# ── Per-path file locks to prevent concurrent write races ──────────────────────
_file_locks: dict[str, threading.Lock] = {}
_file_locks_meta = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """Return a per-path threading.Lock keyed on the resolved canonical path."""
    # Normalize to resolved absolute path so 'foo.txt' and './foo.txt' share the same lock
    try:
        canonical = str(Path(path).resolve())
    except Exception:
        canonical = path
    with _file_locks_meta:
        if canonical not in _file_locks:
            _file_locks[canonical] = threading.Lock()
        return _file_locks[canonical]


def get_workspace_root():
    env_path = os.environ.get("CLAW_WORKSPACE")
    if env_path:
        workspace = Path(os.path.abspath(env_path))
    else:
        workspace = Path(__file__).resolve().parent.parent / "agent_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def enforce_safe_path(path: str) -> Path:
    root = get_workspace_root()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
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
    lock = _get_file_lock(path)
    with lock:
        try:
            full_path = enforce_safe_path(path)

            # Loop prevention: skip identical writes
            if full_path.exists():
                try:
                    old = full_path.read_text(encoding="utf-8")
                    if old.strip() == new_content.strip():
                        return (
                            f"Warning: No changes detected for '{path}'. "
                            "The provided content is identical to the existing file. "
                            "Use FilePatchTool for surgical edits or SearchTool to find the correct insertion point."
                        )
                except Exception:
                    pass

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
            stat = item.stat()
            mod_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
            if item.is_dir():
                count = sum(1 for _ in item.rglob("*") if _.is_file())
                results.append(f"📁 {item.name}/ [{mod_time}] · ({count} files)")
            else:
                size_kb = round(stat.st_size / 1024, 1)
                results.append(f"📄 {item.name} [{mod_time}] · {size_kb}KB")
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
            if f.is_file() and f.stat().st_size < 500000:
                try:
                    lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for i, line in enumerate(lines):
                        if pattern.search(line):
                            rel_path = f.relative_to(get_workspace_root())
                            results.append(f"{rel_path}:{i+1}: {line.strip()}")
                except Exception:
                    pass
        return "\n".join(results[:50]) or "No matches found."
    except Exception as e:
        return f"Error searching in '{path}': {str(e)}"


def tool_bash_run(command: str) -> str:
    _blocked = re.compile(
        r'(?:^|\s|&&|\|\||;)'
        r'\s*(?:cat|rm|mv|cp|nano|vi|vim|code|python|node)\s+'
        r'(?:\.\.[\\/])*(?:app\.py|main\.py|models\.py|src/|templates/|static/)',
    )
    if _blocked.search(command):
        ws = str(get_workspace_root())
        if not any(ws in tok for tok in command.split()):
            return "Security error: Access denied to IDE system files. Please operate inside agent_workspace."

    try:
        workspace_cwd = get_workspace_root()

        env = os.environ.copy()
        env["CI"] = "1"
        env["NPM_CONFIG_PROGRESS"] = "false"
        env["NPM_CONFIG_SPIN"] = "false"
        env["NO_COLOR"] = "1"

        import platform
        cmd_args = command
        is_shell = True

        if platform.system() == "Windows":
            git_bash = Path("C:/Program Files/Git/bin/bash.exe")
            if git_bash.exists():
                cmd_args = [str(git_bash), "-c", command]
                is_shell = False
            else:
                cmd_args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
                is_shell = False

        result = subprocess.run(
            cmd_args, shell=is_shell, cwd=str(workspace_cwd),
            capture_output=True, text=True, timeout=300, env=env
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n" + result.stderr

        output = output.strip()

        if not output:
            output = f"Command executed successfully (exit code {result.returncode})."
        elif result.returncode != 0:
            output = f"[Command exited with code {result.returncode}]\n{output}"

        if len(output) > 5000:
            output = output[:1500] + f"\n\n...[TRUNCATED {len(output)-4000} CHARS]...\n\n" + output[-2500:]

        return output
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 300 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"


def _normalize_ws(text: str) -> str:
    import re as _re
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text


def _fuzzy_find(content: str, search: str) -> tuple[int, int] | None:
    """Find search block in content using progressively looser matching."""
    idx = content.find(search)
    if idx >= 0:
        return (idx, idx + len(search))

    norm_content = _normalize_ws(content)
    norm_search = _normalize_ws(search)
    idx = norm_content.find(norm_search)
    if idx >= 0:
        return (idx, idx + len(norm_search))

    def strip_lines(t):
        return '\n'.join(line.strip() for line in t.split('\n'))

    stripped_content = strip_lines(norm_content)
    stripped_search = strip_lines(norm_search)
    idx = stripped_content.find(stripped_search)
    if idx >= 0:
        search_lines = stripped_search.split('\n')
        content_lines = norm_content.split('\n')
        for i in range(len(content_lines) - len(search_lines) + 1):
            window = content_lines[i:i + len(search_lines)]
            if all(cl.strip() == sl.strip() for cl, sl in zip(window, search_lines)):
                start = sum(len(l) + 1 for l in content_lines[:i])
                end = sum(len(l) + 1 for l in content_lines[:i + len(search_lines)])
                return (start, min(end, len(norm_content)))

    search_lines = [l for l in norm_search.split('\n') if l.strip()]
    if len(search_lines) >= 2:
        content_lines = norm_content.split('\n')
        first_stripped = search_lines[0].strip()
        last_stripped = search_lines[-1].strip()

        for i, cl in enumerate(content_lines):
            if cl.strip() == first_stripped:
                for j in range(i + len(search_lines) - 1, min(i + len(search_lines) + 5, len(content_lines))):
                    if j < len(content_lines) and content_lines[j].strip() == last_stripped:
                        start = sum(len(l) + 1 for l in content_lines[:i])
                        end = sum(len(l) + 1 for l in content_lines[:j + 1])
                        return (start, min(end, len(norm_content)))

    return None


def _get_context_snippet(content: str, search: str, max_lines: int = 5) -> str:
    import difflib
    search_lines = search.strip().split('\n')
    content_lines = content.split('\n')

    if not search_lines:
        return ""

    first_search = search_lines[0].strip()
    best_ratio = 0
    best_idx = 0

    for i, cl in enumerate(content_lines):
        ratio = difflib.SequenceMatcher(None, cl.strip(), first_search).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i

    if best_ratio < 0.4:
        snippet = content_lines[:max_lines]
        return "File starts with:\n" + "\n".join(f"  {i+1}: {l}" for i, l in enumerate(snippet))

    start = max(0, best_idx - 1)
    end = min(len(content_lines), best_idx + max_lines)
    snippet = content_lines[start:end]
    return (
        f"Closest match near line {best_idx + 1} ({int(best_ratio * 100)}% similar):\n"
        + "\n".join(f"  {start + i + 1}: {l}" for i, l in enumerate(snippet))
    )


def tool_file_patch(path: str, search_block: str, replace_block: str) -> str:
    """Perform a surgical SEARCH/REPLACE on a file with locking + exact/fuzzy matching."""
    lock = _get_file_lock(path)
    with lock:
        try:
            full_path = enforce_safe_path(path)
            if not full_path.exists():
                return f"Error: File '{path}' not found."

            content = full_path.read_text(encoding="utf-8")
            if search_block == replace_block:
                return f"Success: No changes needed in '{path}'."

            # Strategy 1: Exact string match
            if search_block in content:
                new_content = content.replace(search_block, replace_block, 1)
                full_path.write_text(new_content, encoding="utf-8")
            else:
                content_lines = content.splitlines(keepends=True)
                search_lines = search_block.splitlines(keepends=True)
                replace_lines = replace_block.splitlines(keepends=True)

                # Strip empty lines from edges of search block
                while search_lines and not search_lines[0].strip():
                    search_lines.pop(0)
                while search_lines and not search_lines[-1].strip():
                    search_lines.pop(-1)

                if not search_lines:
                    return "Error: Search block is empty."

                def fuzzy_match_lines():
                    c_norm = [l.strip() for l in content_lines]
                    s_norm = [l.strip() for l in search_lines]

                    for i in range(len(c_norm) - len(s_norm) + 1):
                        if c_norm[i:i+len(s_norm)] == s_norm:
                            return i, i + len(s_norm)

                    if len(s_norm) >= 2:
                        first, last = s_norm[0], s_norm[-1]
                        for i in range(len(c_norm)):
                            if c_norm[i] == first:
                                for j in range(i + len(s_norm) - 1, min(i + len(s_norm) + 5, len(c_norm))):
                                    if c_norm[j] == last:
                                        return i, j + 1
                    return None

                match = fuzzy_match_lines()
                if not match:
                    snippet = _get_context_snippet(content, search_block)
                    hint = f"Error: SEARCH block not found in '{path}'.\n"
                    hint += "TIP: Read the file first with FileReadTool, then copy the EXACT text.\n"
                    hint += "After 2 failed patches, use FileEditTool to rewrite the whole file instead.\n"
                    if snippet:
                        hint += f"\n{snippet}"
                    return hint

                start_i, end_i = match
                new_content = (
                    "".join(content_lines[:start_i])
                    + "".join(replace_lines)
                    + "".join(content_lines[end_i:])
                )
                full_path.write_text(new_content, encoding="utf-8")

            import difflib
            diff = list(difflib.unified_diff(
                content.splitlines(),
                new_content.splitlines(),
                fromfile='a/' + path,
                tofile='b/' + path,
                lineterm='',
                n=3
            ))
            diff_text = "\n".join(diff)
            return f"Success: Surgical patch applied to '{path}'.\n\n```diff\n{diff_text}\n```"

        except Exception as e:
            return f"Error patching '{path}': {str(e)}"


def tool_workspace_zip(zip_name: str = "workspace_backup.zip") -> bytes:
    """Create a ZIP of the entire workspace and return it as bytes."""
    import shutil
    import tempfile
    import io
    root = get_workspace_root()
    with tempfile.TemporaryDirectory() as tmp:
        zip_base = os.path.join(tmp, "workspace")
        shutil.make_archive(zip_base, 'zip', str(root))
        with open(zip_base + ".zip", "rb") as f:
            return f.read()


def tool_workspace_unzip(zip_data: bytes, zip_name: str = "workspace_backup.zip") -> str:
    """Extract a ZIP (provided as bytes) back into the workspace."""
    import zipfile
    import io
    root = get_workspace_root()
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            z.extractall(str(root))
        return f"Success: Workspace restored from uploaded ZIP."
    except Exception as e:
        return f"Error extracting ZIP: {str(e)}"
