from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .models import PortingBacklog, PortingModule
from .permissions import ToolPermissionContext
from .toolbox import (
    tool_file_read, tool_file_edit, tool_bash_run, tool_list_dir,
    tool_search, tool_view_file_lines, tool_file_delete,
    tool_file_move, tool_file_copy, tool_file_info, tool_tree,
    tool_grep, tool_glob,
)

SNAPSHOT_PATH = Path(__file__).resolve().parent / 'reference_data' / 'tools_snapshot.json'


@dataclass(frozen=True)
class ToolExecution:
    name: str
    source_hint: str
    payload: str
    handled: bool
    message: str


@lru_cache(maxsize=1)
def load_tool_snapshot() -> tuple[PortingModule, ...]:
    raw_entries = json.loads(SNAPSHOT_PATH.read_text())
    return tuple(
        PortingModule(
            name=entry['name'],
            responsibility=entry['responsibility'],
            source_hint=entry['source_hint'],
            status='mirrored',
        )
        for entry in raw_entries
    )


PORTED_TOOLS = load_tool_snapshot()


def build_tool_backlog() -> PortingBacklog:
    return PortingBacklog(title='Tool surface', modules=list(PORTED_TOOLS))


def tool_names() -> list[str]:
    return [module.name for module in PORTED_TOOLS]


def get_tool(name: str) -> PortingModule | None:
    needle = name.lower()
    for module in PORTED_TOOLS:
        if module.name.lower() == needle:
            return module
    return None


def filter_tools_by_permission_context(tools: tuple[PortingModule, ...], permission_context: ToolPermissionContext | None = None) -> tuple[PortingModule, ...]:
    if permission_context is None:
        return tools
    return tuple(module for module in tools if not permission_context.blocks(module.name))


def get_tools(
    simple_mode: bool = False,
    include_mcp: bool = True,
    permission_context: ToolPermissionContext | None = None,
) -> tuple[PortingModule, ...]:
    tools = list(PORTED_TOOLS)
    if simple_mode:
        tools = [module for module in tools if module.name in {'BashTool', 'FileReadTool', 'FileEditTool'}]
    if not include_mcp:
        tools = [module for module in tools if 'mcp' not in module.name.lower() and 'mcp' not in module.source_hint.lower()]
    return filter_tools_by_permission_context(tuple(tools), permission_context)


def find_tools(query: str, limit: int = 20) -> list[PortingModule]:
    needle = query.lower()
    matches = [module for module in PORTED_TOOLS if needle in module.name.lower() or needle in module.source_hint.lower()]
    return matches[:limit]


def execute_tool(name: str, payload: str = '') -> ToolExecution:
    source_hint = 'core'
    if name == 'FileReadTool':
        message = tool_file_read(payload)
    elif name == 'ListDirTool':
        message = tool_list_dir(payload)
    elif name == 'TreeTool':
        parts = payload.split(' ||| ', 1)
        path = parts[0].strip() if parts[0].strip() else "."
        depth = 4
        if len(parts) > 1:
            try:
                depth = int(parts[1].strip())
            except ValueError:
                pass
        message = tool_tree(path, depth)
    elif name == 'SearchTool':
        try:
            path, query = payload.split(' ||| ', 1)
            message = tool_search(path.strip(), query.strip())
        except Exception:
            message = "Error: SearchTool requires 'path ||| query'"
    elif name == 'GrepTool':
        try:
            parts = payload.split(' ||| ')
            if len(parts) >= 2:
                path = parts[0].strip()
                query = parts[1].strip()
                ctx = int(parts[2].strip()) if len(parts) > 2 else 2
                message = tool_grep(path, query, ctx)
            else:
                message = "Error: GrepTool requires 'path ||| query' or 'path ||| query ||| context_lines'"
        except Exception:
            message = "Error: GrepTool requires 'path ||| query'"
    elif name == 'GlobTool':
        message = tool_glob(payload.strip())
    elif name == 'ViewFileLinesTool':
        try:
            path, limits = payload.split(' ||| ', 1)
            start, end = map(int, limits.split(','))
            message = tool_view_file_lines(path.strip(), start, end)
        except Exception:
            message = "Error: ViewFileLinesTool requires 'path ||| start,end'"
    elif name == 'FileEditTool':
        try:
            path, content = payload.split(' ||| ', 1)
            message = tool_file_edit(path.strip(), content)
        except ValueError:
            message = "Error: FileEditTool requires payload format 'path ||| content'."
    elif name == 'FileDeleteTool':
        message = tool_file_delete(payload)
    elif name == 'FileMoveTool':
        try:
            source, dest = payload.split(' ||| ', 1)
            message = tool_file_move(source.strip(), dest.strip())
        except ValueError:
            message = "Error: FileMoveTool requires 'source ||| destination'"
    elif name == 'FileCopyTool':
        try:
            source, dest = payload.split(' ||| ', 1)
            message = tool_file_copy(source.strip(), dest.strip())
        except ValueError:
            message = "Error: FileCopyTool requires 'source ||| destination'"
    elif name == 'FileInfoTool':
        message = tool_file_info(payload.strip())
    elif name == 'BashTool':
        message = tool_bash_run(payload)
    else:
        module = get_tool(name)
        if module is None:
            return ToolExecution(name=name, source_hint='', payload=payload, handled=False, message=f'Unknown mirrored tool: {name}')
        action = f"Mirrored tool '{module.name}' from {module.source_hint} would handle payload {payload!r}."
        message = action
        source_hint = module.source_hint

    return ToolExecution(name=name, source_hint=source_hint, payload=payload, handled=True, message=message)



def render_tool_index(limit: int = 20, query: str | None = None) -> str:
    modules = find_tools(query, limit) if query else list(PORTED_TOOLS[:limit])
    lines = [f'Tool entries: {len(PORTED_TOOLS)}', '']
    if query:
        lines.append(f'Filtered by: {query}')
        lines.append('')
    lines.extend(f'- {module.name} — {module.source_hint}' for module in modules)
    return '\n'.join(lines)
