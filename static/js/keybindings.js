'use strict';
/* KEYBINDINGS.JS — Global keyboard shortcuts */

function initKeybindings() {
    document.addEventListener('keydown', e => {
        const isMac = navigator.platform.includes('Mac');
        const mod = isMac ? e.metaKey : e.ctrlKey;

        // Escape — close all modals/overlays
        if (e.key === 'Escape') {
            hideCommandPalette();
            ['settingsModal','newFileModal','newFolderModal','modelSelectorModal',
             'shortcutsModal','memoryModal','tokenStatsModal','deployModal',
             'confirmModal','aboutModal','renameModal'].forEach(hideModal);
            hideFindReplace();
            hideAllCtxMenus();
            return;
        }

        // ⌘K — Command Palette
        if (e.key === 'k' && mod) { e.preventDefault(); showCommandPalette(); return; }

        // ⌘N — New Session
        if (e.key === 'n' && mod && !e.shiftKey) { e.preventDefault(); newSession(); return; }

        // ⌘T — New File
        if (e.key === 't' && mod) { e.preventDefault(); showNewFileDialog(); return; }

        // ⌘S — Save
        if (e.key === 's' && mod && !e.shiftKey) { e.preventDefault(); saveCurrentFile(); return; }

        // ⌘⇧S — Save All
        if (e.key === 's' && mod && e.shiftKey) { e.preventDefault(); saveAllFiles(); return; }

        // ⌘F — Find
        if (e.key === 'f' && mod && !e.shiftKey) { e.preventDefault(); showFindReplace(); return; }

        // ⌘⇧F — Global Search
        if (e.key === 'f' && mod && e.shiftKey) { e.preventDefault(); showGlobalSearch(); return; }

        // ⌘⇧E — Explorer
        if (e.key === 'e' && mod && e.shiftKey) { e.preventDefault(); togglePanel('explorer'); return; }

        // ⌘B — Toggle Sidebar
        if (e.key === 'b' && mod) { e.preventDefault(); toggleActivity(); return; }

        // ⌘` — Toggle Terminal
        if (e.key === '`' && mod) { e.preventDefault(); switchRightTab('terminal'); return; }

        // ⌘, — Settings
        if (e.key === ',' && mod) { e.preventDefault(); showSettings(); return; }

        // ⌘. — Stop Agent
        if (e.key === '.' && mod) { e.preventDefault(); stopAgent(); return; }

        // ⌘⇧K — Clear Chat
        if (e.key === 'k' && mod && e.shiftKey) { e.preventDefault(); clearChat(); return; }

        // ⌘U — Toggle Ultra
        if (e.key === 'u' && mod) { e.preventDefault(); toggleUltraMode(); return; }

        // ⌘M — Memory
        if (e.key === 'm' && mod) { e.preventDefault(); showMemory(); return; }

        // ⌘\ — Split Editor
        if (e.key === '\\' && mod) { e.preventDefault(); splitEditorHorizontal(); return; }

        // ⌘/ — Toggle Comment
        if (e.key === '/' && mod) { e.preventDefault(); toggleComment(); return; }

        // F5 — Run Project
        if (e.key === 'F5' && !mod && !e.shiftKey) { e.preventDefault(); runProject(); return; }

        // ⌘F5 — Run Current File
        if (e.key === 'F5' && mod) { e.preventDefault(); runCurrentFile(); return; }

        // ⇧F5 — Stop Debug
        if (e.key === 'F5' && e.shiftKey) { e.preventDefault(); stopDebug(); return; }

        // F9 — Debug
        if (e.key === 'F9') { e.preventDefault(); debugProject(); return; }

        // F10 — Step Over
        if (e.key === 'F10') { e.preventDefault(); debugStepOver(); return; }

        // F11 — Step Into
        if (e.key === 'F11') { e.preventDefault(); debugStepInto(); return; }

        // ⇧F11 — Step Out
        if (e.key === 'F11' && e.shiftKey) { e.preventDefault(); debugStepOut(); return; }

        // ⌘W — Close Tab
        if (e.key === 'w' && mod) {
            e.preventDefault();
            if (NX.activeTab !== null) closeTab(NX.activeTab);
            return;
        }

        // ⌘Tab / ⌘⇧Tab — Switch Tabs
        if (e.key === 'Tab' && mod && NX.openTabs.length > 1) {
            e.preventDefault();
            const dir = e.shiftKey ? -1 : 1;
            const newIdx = (NX.activeTab + dir + NX.openTabs.length) % NX.openTabs.length;
            switchToTab(newIdx);
            return;
        }

        // Alt+⇧+F — Format Document
        if (e.key === 'f' && e.altKey && e.shiftKey) { e.preventDefault(); formatDocument(); return; }
    });
}

// ─── Keybinding Table (for settings) ─────────────────────────────────────────
function renderKeybindingTable() {
    const table = document.getElementById('keybindingTable');
    if (!table) return;

    const bindings = [
        ['Command Palette', '⌘K / Ctrl+K'],
        ['New Session', '⌘N / Ctrl+N'],
        ['New File', '⌘T / Ctrl+T'],
        ['Save', '⌘S / Ctrl+S'],
        ['Save All', '⌘⇧S / Ctrl+Shift+S'],
        ['Find', '⌘F / Ctrl+F'],
        ['Global Search', '⌘⇧F / Ctrl+Shift+F'],
        ['Toggle Sidebar', '⌘B / Ctrl+B'],
        ['Terminal', '⌘` / Ctrl+`'],
        ['Settings', '⌘, / Ctrl+,'],
        ['Stop Agent', '⌘. / Ctrl+.'],
        ['Clear Chat', '⌘⇧K / Ctrl+Shift+K'],
        ['Ultra Mode', '⌘U / Ctrl+U'],
        ['Memory', '⌘M / Ctrl+M'],
        ['Split Editor', '⌘\\ / Ctrl+\\'],
        ['Toggle Comment', '⌘/ / Ctrl+/'],
        ['Close Tab', '⌘W / Ctrl+W'],
        ['Switch Tabs', '⌘Tab / Ctrl+Tab'],
        ['Format Document', 'Alt+Shift+F'],
        ['Run Project', 'F5'],
        ['Run File', '⌘F5 / Ctrl+F5'],
        ['Stop Debug', 'Shift+F5'],
        ['Step Over', 'F10'],
        ['Step Into', 'F11'],
    ];

    table.innerHTML = bindings.map(([action, key]) =>
        `<div class="kb-row"><span class="kb-action">${action}</span><kbd class="kb-key">${key}</kbd></div>`
    ).join('');
}

document.addEventListener('DOMContentLoaded', () => setTimeout(renderKeybindingTable, 300));
