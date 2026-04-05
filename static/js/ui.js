'use strict';
/* UI.JS — Command palette, menu bar dropdowns, and miscellaneous UI wiring */

// ─── Command Palette ─────────────────────────────────────────────────────────
const _commands = [
    { label: 'New Session', icon: 'fa-solid fa-plus', action: () => newSession(), category: 'General' },
    { label: 'New File', icon: 'fa-solid fa-file-plus', action: () => showNewFileDialog(), category: 'File' },
    { label: 'New Folder', icon: 'fa-solid fa-folder-plus', action: () => showNewFolderDialog(), category: 'File' },
    { label: 'Save File', icon: 'fa-solid fa-floppy-disk', action: () => saveCurrentFile(), category: 'File' },
    { label: 'Save All', icon: 'fa-solid fa-floppy-disks', action: () => saveAllFiles(), category: 'File' },
    { label: 'Export ZIP', icon: 'fa-solid fa-file-zipper', action: () => downloadWorkspace(), category: 'File' },
    { label: 'Import ZIP', icon: 'fa-solid fa-file-import', action: () => triggerImport(), category: 'File' },
    { label: 'Upload Files', icon: 'fa-solid fa-cloud-arrow-up', action: () => triggerUpload(), category: 'File' },
    { label: 'Find & Replace', icon: 'fa-solid fa-magnifying-glass', action: () => showFindReplace(), category: 'Edit' },
    { label: 'Global Search', icon: 'fa-solid fa-search', action: () => showGlobalSearch(), category: 'Edit' },
    { label: 'Format Document', icon: 'fa-solid fa-align-left', action: () => formatDocument(), category: 'Edit' },
    { label: 'Toggle Comment', icon: 'fa-solid fa-comment-slash', action: () => toggleComment(), category: 'Edit' },
    { label: 'Explorer', icon: 'fa-solid fa-folder-tree', action: () => togglePanel('explorer'), category: 'View' },
    { label: 'Search Panel', icon: 'fa-solid fa-magnifying-glass', action: () => togglePanel('search'), category: 'View' },
    { label: 'Source Control', icon: 'fa-brands fa-git-alt', action: () => { togglePanel('git'); gitRefresh(); }, category: 'View' },
    { label: 'Agent Panel', icon: 'fa-solid fa-robot', action: () => togglePanel('agents'), category: 'View' },
    { label: 'Terminal', icon: 'fa-solid fa-terminal', action: () => switchRightTab('terminal'), category: 'View' },
    { label: 'Preview', icon: 'fa-solid fa-globe', action: () => { switchRightTab('preview'); refreshPreview(); }, category: 'View' },
    { label: 'Zen Mode', icon: 'fa-solid fa-expand', action: () => toggleLayout('zen'), category: 'View' },
    { label: 'Split Editor', icon: 'fa-solid fa-table-columns', action: () => splitEditorHorizontal(), category: 'View' },
    { label: 'Switch Model', icon: 'fa-solid fa-microchip', action: () => showModelSelector(), category: 'Agent' },
    { label: 'Toggle Ultra Mode', icon: 'fa-solid fa-bolt', action: () => toggleUltraMode(), category: 'Agent' },
    { label: 'View Memory', icon: 'fa-solid fa-brain', action: () => showMemory(), category: 'Agent' },
    { label: 'Token Stats', icon: 'fa-solid fa-coins', action: () => showTokenStats(), category: 'Agent' },
    { label: 'Stop Agent', icon: 'fa-solid fa-stop', action: () => stopAgent(), category: 'Agent' },
    { label: 'Reset Agent', icon: 'fa-solid fa-rotate-left', action: () => resetAgent(), category: 'Agent' },
    { label: 'Run Project', icon: 'fa-solid fa-play', action: () => runProject(), category: 'Run' },
    { label: 'Run Current File', icon: 'fa-solid fa-play-circle', action: () => runCurrentFile(), category: 'Run' },
    { label: 'Deploy', icon: 'fa-solid fa-rocket', action: () => showDeployPanel(), category: 'Deploy' },
    { label: 'Settings', icon: 'fa-solid fa-gear', action: () => showSettings(), category: 'General' },
    { label: 'Keyboard Shortcuts', icon: 'fa-solid fa-keyboard', action: () => showKeyboardShortcuts(), category: 'Help' },
    { label: 'About NEXUS IDE', icon: 'fa-solid fa-circle-info', action: () => showAbout(), category: 'Help' },
    { label: 'Toggle Theme', icon: 'fa-solid fa-moon', action: () => toggleTheme(), category: 'View' },
    { label: 'Clear Chat', icon: 'fa-solid fa-trash', action: () => clearChat(), category: 'Agent' },
    { label: 'Export Chat', icon: 'fa-solid fa-download', action: () => exportChat(), category: 'Agent' },
    { label: 'Refresh Preview', icon: 'fa-solid fa-rotate-right', action: () => refreshPreview(), category: 'View' },
    { label: 'Tool Log', icon: 'fa-solid fa-list-check', action: () => showToolLog(), category: 'View' },
];

let _cmdIdx = 0;
let _filteredCmds = [..._commands];

function showCommandPalette() {
    document.getElementById('cmdPaletteOverlay').style.display = 'flex';
    const input = document.getElementById('cmdInput');
    if (input) { input.value = ''; input.focus(); }
    _filteredCmds = [..._commands];
    _cmdIdx = 0;

    // Also add open files as searchable items
    const fileCommands = NX.allFiles.map(f => {
        const path = typeof f === 'object' ? f.path : f;
        return { label: path, icon: fileIcon(path.split('/').pop()), action: () => openFileInEditor(path), category: 'Files' };
    });
    _filteredCmds = [..._commands, ...fileCommands];
    renderCommandResults(_filteredCmds);
}

function hideCommandPalette() {
    document.getElementById('cmdPaletteOverlay').style.display = 'none';
}

function filterCommands(val) {
    const q = val.toLowerCase().trim();
    if (!q) { _filteredCmds = [..._commands]; } else {
        const allCmds = [..._commands, ...NX.allFiles.map(f => {
            const path = typeof f === 'object' ? f.path : f;
            return { label: path, icon: fileIcon(path.split('/').pop()), action: () => openFileInEditor(path), category: 'Files' };
        })];
        _filteredCmds = allCmds.filter(c => c.label.toLowerCase().includes(q) || (c.category || '').toLowerCase().includes(q));
    }
    _cmdIdx = 0;
    renderCommandResults(_filteredCmds);
}

function renderCommandResults(cmds) {
    const container = document.getElementById('cmdResults');
    if (!container) return;

    if (!cmds.length) { container.innerHTML = '<div class="cmd-empty">No matching commands</div>'; return; }

    let lastCat = '';
    container.innerHTML = cmds.slice(0, 20).map((c, i) => {
        let catHeader = '';
        if (c.category !== lastCat) { catHeader = `<div class="cmd-category">${c.category}</div>`; lastCat = c.category; }
        return `${catHeader}<div class="cmd-result ${i === _cmdIdx ? 'active' : ''}" data-idx="${i}" onclick="executeCommand(${i})" onmouseenter="_cmdIdx=${i};highlightCmd()">
            <i class="${c.icon}"></i><span>${c.label}</span>
        </div>`;
    }).join('');
}

function cmdKeyNav(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); _cmdIdx = Math.min(_cmdIdx + 1, _filteredCmds.length - 1); highlightCmd(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); _cmdIdx = Math.max(_cmdIdx - 1, 0); highlightCmd(); }
    else if (e.key === 'Enter') { e.preventDefault(); executeCommand(_cmdIdx); }
}

function highlightCmd() {
    document.querySelectorAll('.cmd-result').forEach((el, i) => el.classList.toggle('active', parseInt(el.dataset.idx) === _cmdIdx));
    const active = document.querySelector('.cmd-result.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
}

function executeCommand(idx) {
    if (_filteredCmds[idx]?.action) {
        hideCommandPalette();
        _filteredCmds[idx].action();
    }
}

// ─── Menu Bar Dropdowns ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Close dropdowns when clicking outside
    document.addEventListener('click', e => {
        if (!e.target.closest('.tb-menu-item')) {
            document.querySelectorAll('.tb-menu-item').forEach(m => m.classList.remove('open'));
        }
    });

    // Toggle dropdowns on click
    document.querySelectorAll('.tb-menu-item').forEach(item => {
        item.addEventListener('click', e => {
            const wasOpen = item.classList.contains('open');
            document.querySelectorAll('.tb-menu-item').forEach(m => m.classList.remove('open'));
            if (!wasOpen) item.classList.add('open');
        });
    });

    // Close dropdown when clicking an action
    document.querySelectorAll('.tb-dropdown > div:not(.tb-sep)').forEach(action => {
        action.addEventListener('click', () => {
            document.querySelectorAll('.tb-menu-item').forEach(m => m.classList.remove('open'));
        });
    });
});
