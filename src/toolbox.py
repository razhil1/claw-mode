import os
import subprocess
import re
import time
import threading
import shutil
import fnmatch
from pathlib import Path

_file_locks: dict[str, threading.Lock] = {}
_file_locks_meta = threading.Lock()

_IDE_ROOT = Path(__file__).resolve().parent.parent

_IDE_PROTECTED_DIRS = {"src", "static", "templates", ".git", "__pycache__", "dist", "build", ".local"}
_IDE_PROTECTED_FILES = {
    "app.py", "main.py", "models.py", "requirements.txt", "pyproject.toml",
    "replit.md", ".replit", "replit.nix", "shell.nix", "poetry.lock",
    "start.sh", "start.bat", "build_exe.bat", "build_exe.sh",
    "Procfile", "gunicorn_config.py",
}

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".class", ".o",
    ".sqlite", ".db", ".sqlite3",
}


def _get_file_lock(path: str) -> threading.Lock:
    try:
        canonical = str(Path(path).resolve())
    except Exception:
        canonical = path
    with _file_locks_meta:
        if canonical not in _file_locks:
            _file_locks[canonical] = threading.Lock()
        return _file_locks[canonical]


def get_workspace_root() -> Path:
    env_path = os.environ.get("CLAW_WORKSPACE")
    if env_path:
        workspace = Path(os.path.abspath(env_path))
    else:
        workspace = _IDE_ROOT / "agent_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def enforce_safe_path(path: str) -> Path:
    root = get_workspace_root()
    cleaned = path.replace("\x00", "").strip()
    if not cleaned or cleaned == "/":
        return root
    full_path = (root / cleaned).resolve()
    root_resolved = root.resolve()
    root_str = str(root_resolved)
    full_str = str(full_path)
    if not (full_str == root_str or full_str.startswith(root_str + os.sep)):
        raise ValueError(
            f"Security error: Path '{path}' resolves outside workspace. "
            f"All operations must stay inside '{root.name}/'. "
            f"Use relative paths like 'src/app.py', not absolute or '../' paths."
        )
    ide_root_str = str(_IDE_ROOT.resolve())
    if full_str.startswith(ide_root_str) and not full_str.startswith(root_str):
        raise ValueError(
            f"Security error: Path '{path}' targets IDE system files. "
            "The agent must never access app.py, src/, static/, templates/ etc. "
            "Only files inside agent_workspace/ are allowed."
        )
    return full_path


def _is_within_workspace(resolved_path: str) -> bool:
    ws = str(get_workspace_root().resolve())
    return resolved_path == ws or resolved_path.startswith(ws + os.sep)


def _is_ide_system_path(path_str: str) -> bool:
    ide_str = str(_IDE_ROOT.resolve())
    ws_str = str(get_workspace_root().resolve())
    resolved = os.path.realpath(os.path.join(ws_str, path_str))
    if resolved.startswith(ws_str + os.sep) or resolved == ws_str:
        return False
    if resolved.startswith(ide_str):
        return True
    rel = os.path.relpath(resolved, ide_str)
    parts = Path(rel).parts
    if parts and parts[0] in _IDE_PROTECTED_DIRS:
        return True
    if parts and parts[0] in _IDE_PROTECTED_FILES:
        return True
    return False


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024*1024):.1f}MB"


def tool_file_read(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: File '{path}' not found in workspace."
        if full_path.is_dir():
            return f"Error: '{path}' is a directory. Use ListDirTool instead."
        size = full_path.stat().st_size
        if size > 2 * 1024 * 1024:
            return f"Error: File '{path}' is too large ({_human_size(size)}). Use ViewFileLinesTool to read a range."
        ext = full_path.suffix.lower()
        if ext in _BINARY_EXTENSIONS:
            return f"Info: '{path}' is a binary file ({ext}, {_human_size(size)}). Cannot display as text."
        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.count('\n') + 1
        return f"[{path} · {lines} lines · {_human_size(size)}]\n{content}"
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"


def tool_view_file_lines(path: str, start_line: int, end_line: int) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: File '{path}' not found in workspace."
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        start_line = max(1, min(start_line, total))
        end_line = max(start_line, min(end_line, total))
        extracted = lines[start_line-1:end_line]
        numbered = [f"{start_line + i:4d} | {line}" for i, line in enumerate(extracted)]
        header = f"[{path} · lines {start_line}-{end_line} of {total}]"
        return header + "\n" + "\n".join(numbered)
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"


def tool_file_edit(path: str, new_content: str) -> str:
    lock = _get_file_lock(path)
    with lock:
        try:
            full_path = enforce_safe_path(path)
            is_new = not full_path.exists()
            if not is_new:
                try:
                    old = full_path.read_text(encoding="utf-8")
                    if old.strip() == new_content.strip():
                        return (
                            f"Warning: No changes detected for '{path}'. "
                            "The provided content is identical to the existing file. "
                            "Use FilePatchTool for surgical edits."
                        )
                except Exception:
                    pass

            if not full_path.parent.exists():
                full_path.parent.mkdir(parents=True, exist_ok=True)

            full_path.write_text(new_content, encoding="utf-8")
            size = full_path.stat().st_size
            lines = new_content.count('\n') + 1
            action = "Created" if is_new else "Updated"
            return f"Success: {action} '{path}' ({lines} lines, {_human_size(size)})."
        except Exception as e:
            return f"Error writing to file '{path}': {str(e)}"


def tool_file_delete(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: '{path}' does not exist."
        if full_path == get_workspace_root().resolve():
            return "Error: Cannot delete the workspace root directory."
        if full_path.is_dir():
            count = sum(1 for _ in full_path.rglob("*") if _.is_file())
            shutil.rmtree(full_path)
            return f"Success: Deleted directory '{path}' ({count} files removed)."
        else:
            size = full_path.stat().st_size
            full_path.unlink()
            return f"Success: Deleted file '{path}' ({_human_size(size)})."
    except Exception as e:
        return f"Error deleting '{path}': {str(e)}"


def tool_list_dir(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: Directory '{path}' not found in workspace."
        if not full_path.is_dir():
            return f"Error: '{path}' is a file, not a directory. Use FileReadTool to read it."

        dirs = []
        files = []
        total_size = 0

        for item in sorted(full_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            try:
                stat = item.stat()
            except OSError:
                continue
            mod_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
            if item.is_dir():
                count = sum(1 for _ in item.rglob("*") if _.is_file())
                dirs.append(f"  📁 {item.name}/  ({count} files) [{mod_time}]")
            else:
                size = stat.st_size
                total_size += size
                ext = item.suffix.lower()
                icon = "📄"
                if ext in {".py"}: icon = "🐍"
                elif ext in {".js", ".jsx", ".ts", ".tsx"}: icon = "📜"
                elif ext in {".html", ".htm"}: icon = "🌐"
                elif ext in {".css", ".scss", ".sass"}: icon = "🎨"
                elif ext in {".json", ".yaml", ".yml", ".toml"}: icon = "⚙️"
                elif ext in {".md", ".txt", ".rst"}: icon = "📝"
                elif ext in {".sh", ".bash", ".bat", ".ps1"}: icon = "💻"
                elif ext in _BINARY_EXTENSIONS: icon = "📦"
                files.append(f"  {icon} {item.name}  {_human_size(size)} [{mod_time}]")

        rel = full_path.relative_to(get_workspace_root()) if full_path != get_workspace_root() else Path(".")
        header = f"📂 {rel}/ — {len(dirs)} dirs, {len(files)} files ({_human_size(total_size)} total)"
        parts = [header, "─" * min(60, len(header))]
        if dirs:
            parts.extend(dirs)
        if files:
            parts.extend(files)
        if not dirs and not files:
            parts.append("  (empty directory)")
        return "\n".join(parts)
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"


def tool_tree(path: str = ".", max_depth: int = 4) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists() or not full_path.is_dir():
            return f"Error: Directory '{path}' not found in workspace."

        root = get_workspace_root()
        lines = []
        file_count = 0
        dir_count = 0

        def _walk(dir_path: Path, prefix: str, depth: int):
            nonlocal file_count, dir_count
            if depth > max_depth:
                lines.append(f"{prefix}... (depth limit)")
                return

            try:
                entries = sorted(dir_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                return

            for i, entry in enumerate(entries):
                is_last = (i == len(entries) - 1)
                connector = "└── " if is_last else "├── "
                extension = "    " if is_last else "│   "

                if entry.is_dir():
                    dir_count += 1
                    sub_count = sum(1 for _ in entry.rglob("*") if _.is_file())
                    lines.append(f"{prefix}{connector}📁 {entry.name}/ ({sub_count})")
                    _walk(entry, prefix + extension, depth + 1)
                else:
                    file_count += 1
                    lines.append(f"{prefix}{connector}{entry.name} ({_human_size(entry.stat().st_size)})")

        rel = full_path.relative_to(root) if full_path != root else Path(".")
        lines.append(f"📂 {rel}/")
        _walk(full_path, "", 1)
        lines.append(f"\n{dir_count} directories, {file_count} files")
        return "\n".join(lines)
    except Exception as e:
        return f"Error building tree for '{path}': {str(e)}"


def tool_file_move(source: str, destination: str) -> str:
    try:
        src_path = enforce_safe_path(source)
        dst_path = enforce_safe_path(destination)
        if not src_path.exists():
            return f"Error: Source '{source}' not found."
        if dst_path.exists() and dst_path.is_file():
            return f"Error: Destination '{destination}' already exists. Delete it first or use a different name."
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
        return f"Success: Moved '{source}' → '{destination}'."
    except Exception as e:
        return f"Error moving '{source}': {str(e)}"


def tool_file_copy(source: str, destination: str) -> str:
    try:
        src_path = enforce_safe_path(source)
        dst_path = enforce_safe_path(destination)
        if not src_path.exists():
            return f"Error: Source '{source}' not found."
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            if dst_path.exists():
                return f"Error: Destination '{destination}' already exists."
            shutil.copytree(str(src_path), str(dst_path))
            count = sum(1 for _ in dst_path.rglob("*") if _.is_file())
            return f"Success: Copied directory '{source}' → '{destination}' ({count} files)."
        else:
            shutil.copy2(str(src_path), str(dst_path))
            return f"Success: Copied '{source}' → '{destination}' ({_human_size(src_path.stat().st_size)})."
    except Exception as e:
        return f"Error copying '{source}': {str(e)}"


def tool_file_info(path: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: '{path}' not found in workspace."
        stat = full_path.stat()
        info = [f"Path: {path}"]
        if full_path.is_dir():
            info.append("Type: Directory")
            file_count = sum(1 for _ in full_path.rglob("*") if _.is_file())
            dir_count = sum(1 for _ in full_path.rglob("*") if _.is_dir())
            total_size = sum(f.stat().st_size for f in full_path.rglob("*") if f.is_file())
            info.append(f"Contents: {file_count} files, {dir_count} subdirectories")
            info.append(f"Total size: {_human_size(total_size)}")
        else:
            info.append(f"Type: File ({full_path.suffix or 'no extension'})")
            info.append(f"Size: {_human_size(stat.st_size)}")
            try:
                lines = full_path.read_text(encoding="utf-8", errors="replace").count('\n') + 1
                info.append(f"Lines: {lines}")
            except Exception:
                info.append("Lines: (binary file)")
        info.append(f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))}")
        info.append(f"Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_ctime))}")
        import stat as stat_mod
        perms = stat_mod.filemode(stat.st_mode)
        info.append(f"Permissions: {perms}")
        return "\n".join(info)
    except Exception as e:
        return f"Error getting info for '{path}': {str(e)}"


def tool_glob(pattern: str) -> str:
    try:
        root = get_workspace_root()
        matches = []
        for p in root.rglob(pattern):
            rel = p.relative_to(root)
            if p.is_dir():
                matches.append(f"📁 {rel}/")
            else:
                matches.append(f"📄 {rel} ({_human_size(p.stat().st_size)})")
        if not matches:
            return f"No files matching pattern '{pattern}' found."
        header = f"Found {len(matches)} match(es) for '{pattern}':"
        return header + "\n" + "\n".join(matches[:100])
    except Exception as e:
        return f"Error searching for pattern '{pattern}': {str(e)}"


def tool_search(path: str, query: str) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: Path '{path}' not found in workspace."
        results = []
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return f"Error: Invalid regex pattern '{query}': {e}"

        if full_path.is_file():
            files_to_search = [full_path]
        else:
            files_to_search = sorted(full_path.rglob("*"))

        files_searched = 0
        for f in files_to_search:
            if not f.is_file():
                continue
            if f.suffix.lower() in _BINARY_EXTENSIONS:
                continue
            if f.stat().st_size > 1_000_000:
                continue
            files_searched += 1
            try:
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        rel_path = f.relative_to(get_workspace_root())
                        results.append(f"  {rel_path}:{i+1}: {line.strip()[:200]}")
                        if len(results) >= 100:
                            break
            except Exception:
                pass
            if len(results) >= 100:
                break

        if not results:
            return f"No matches found for '{query}' in '{path}' ({files_searched} files searched)."
        header = f"Found {len(results)} match(es) for '{query}' ({files_searched} files searched):"
        return header + "\n" + "\n".join(results)
    except Exception as e:
        return f"Error searching in '{path}': {str(e)}"


def tool_grep(path: str, query: str, context_lines: int = 2) -> str:
    try:
        full_path = enforce_safe_path(path)
        if not full_path.exists():
            return f"Error: Path '{path}' not found in workspace."
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return f"Error: Invalid regex pattern '{query}': {e}"

        results = []
        files_to_search = [full_path] if full_path.is_file() else sorted(full_path.rglob("*"))

        for f in files_to_search:
            if not f.is_file() or f.suffix.lower() in _BINARY_EXTENSIONS or f.stat().st_size > 1_000_000:
                continue
            try:
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                rel_path = f.relative_to(get_workspace_root())
                file_matches = []
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        ctx = []
                        for j in range(start, end):
                            marker = ">>>" if j == i else "   "
                            ctx.append(f"  {marker} {j+1:4d} | {lines[j]}")
                        file_matches.append("\n".join(ctx))
                if file_matches:
                    results.append(f"── {rel_path} ──")
                    results.extend(file_matches)
                    if len(results) > 80:
                        break
            except Exception:
                pass

        if not results:
            return f"No matches found for '{query}' in '{path}'."
        return "\n".join(results)
    except Exception as e:
        return f"Error grep in '{path}': {str(e)}"


def tool_bash_run(command: str) -> str:
    ws = get_workspace_root()
    ws_str = str(ws.resolve())

    command = command.strip()
    if not command:
        return "Error: Empty command."

    _BLOCKED_PATTERNS = [
        r'(?:^|[\s;&|])cd\s+/',
        r'(?:^|[\s;&|])cd\s+\.\.',
        r'(?:^|[\s;&|])(?:rm|mv|cp|cat|nano|vi|vim|code|sed|awk|perl|dd|truncate|shred)\s+(?:/|\.\.)',
        r'(?:^|[\s;&|])(?:rm|mv|cp)\s+-[rf]*\s+/',
    ]

    for pat in _BLOCKED_PATTERNS:
        if re.search(pat, command):
            return "Security error: Command attempts to access paths outside the workspace. All operations must stay inside agent_workspace/."

    import shlex
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for tok in tokens:
        if tok.startswith('-'):
            continue
        if tok.startswith('/') and not tok.startswith(ws_str):
            return f"Security error: Absolute path '{tok}' is outside the workspace. Use relative paths."
        resolved = os.path.realpath(os.path.join(ws_str, tok))
        if _is_ide_system_path(tok) and not _is_within_workspace(resolved):
            return (
                "Security error: Command references IDE system files "
                f"(detected: '{tok}'). The agent must only operate "
                "inside agent_workspace/."
            )

    try:
        env = os.environ.copy()
        env["CI"] = "1"
        env["NPM_CONFIG_PROGRESS"] = "false"
        env["NPM_CONFIG_SPIN"] = "false"
        env["NO_COLOR"] = "1"
        env["HOME"] = ws_str
        env["PYTHONDONTWRITEBYTECODE"] = "1"

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
            cmd_args, shell=is_shell, cwd=ws_str,
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
            output = f"[Exit code {result.returncode}]\n{output}"

        if len(output) > 8000:
            output = output[:3000] + f"\n\n...[TRUNCATED {len(output)-6000} chars]...\n\n" + output[-3000:]

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
    lock = _get_file_lock(path)
    with lock:
        try:
            full_path = enforce_safe_path(path)
            if not full_path.exists():
                return f"Error: File '{path}' not found."

            content = full_path.read_text(encoding="utf-8")
            if search_block == replace_block:
                return f"Success: No changes needed in '{path}'."

            if search_block in content:
                new_content = content.replace(search_block, replace_block, 1)
                full_path.write_text(new_content, encoding="utf-8")
            else:
                content_lines = content.splitlines(keepends=True)
                search_lines = search_block.splitlines(keepends=True)
                replace_lines = replace_block.splitlines(keepends=True)

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
                    hint += "After 2 failed patches, use FileEditTool to rewrite the whole file.\n"
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
            return f"Success: Patch applied to '{path}'.\n\n```diff\n{diff_text}\n```"

        except Exception as e:
            return f"Error patching '{path}': {str(e)}"


def tool_workspace_zip(zip_name: str = "workspace_backup.zip") -> bytes:
    import tempfile
    import io
    root = get_workspace_root()
    with tempfile.TemporaryDirectory() as tmp:
        zip_base = os.path.join(tmp, "workspace")
        shutil.make_archive(zip_base, 'zip', str(root))
        with open(zip_base + ".zip", "rb") as f:
            return f.read()


def tool_workspace_unzip(zip_data: bytes, zip_name: str = "workspace_backup.zip") -> str:
    import zipfile
    import io
    root = get_workspace_root()
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            for member in z.namelist():
                resolved = (root / member).resolve()
                if not str(resolved).startswith(str(root.resolve())):
                    return f"Security error: ZIP contains path traversal attack in '{member}'."
            z.extractall(str(root))
        return f"Success: Workspace restored from uploaded ZIP."
    except Exception as e:
        return f"Error extracting ZIP: {str(e)}"
