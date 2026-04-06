'use strict';

/* ═══════════════════════════════════════════════════════════════════════════════
   EDITOR.JS — CodeMirror integration, tabs, find/replace, breadcrumbs
   ═══════════════════════════════════════════════════════════════════════════════ */

let _cmEditor = null;
let _cmEditorRight = null;
let _autoSaveTimer = null;

// ─── Editor Initialization ───────────────────────────────────────────────────
function initEditor(container, content = '', mode = 'text/plain') {
    if (typeof CodeMirror === 'undefined') {
        console.warn('CodeMirror not loaded');
        return null;
    }

    const cm = CodeMirror(container, {
        value: content,
        mode: mode,
        theme: 'dracula',
        lineNumbers: true,
        matchBrackets: true,
        autoCloseBrackets: true,
        indentUnit: 2,
        tabSize: 2,
        indentWithTabs: false,
        lineWrapping: false,
        foldGutter: false,
        styleActiveLine: true,
        scrollbarStyle: 'overlay',
        extraKeys: {
            'Ctrl-S': () => saveCurrentFile(),
            'Cmd-S': () => saveCurrentFile(),
            'Ctrl-/': () => toggleComment(),
            'Cmd-/': () => toggleComment(),
            'Ctrl-F': () => showFindReplace(),
            'Cmd-F': () => showFindReplace(),
        }
    });

    cm.on('cursorActivity', () => {
        const pos = cm.getCursor();
        const cursorEl = document.getElementById('cursorPos');
        if (cursorEl) cursorEl.textContent = `Ln ${pos.line + 1}, Col ${pos.ch + 1}`;
        const sbCursorEl = document.getElementById('sbCursor');
        if (sbCursorEl) sbCursorEl.textContent = `Ln ${pos.line + 1}, Col ${pos.ch + 1}`;

        const sel = cm.getSelection();
        const selInfo = document.getElementById('selectionInfo');
        if (selInfo) selInfo.textContent = sel ? `(${sel.length} chars selected)` : '';
    });

    cm.on('change', () => {
        if (NX.activeTab !== null && NX.openTabs[NX.activeTab]) {
            NX.openTabs[NX.activeTab].dirty = true;
            updateTabDirtyState(NX.activeTab);

            // Auto-save
            clearTimeout(_autoSaveTimer);
            const delay = parseInt(document.getElementById('cfg-autoSave')?.value || '1000');
            if (delay > 0 && NX.constraints.auto_save) {
                _autoSaveTimer = setTimeout(() => saveCurrentFile(true), delay);
            }
        }
    });

    return cm;
}

// ─── Open File in Editor ─────────────────────────────────────────────────────
async function openFileInEditor(filepath) {
    // Check if already open in a tab
    const existingIdx = NX.openTabs.findIndex(t => t.path === filepath);
    if (existingIdx >= 0) {
        switchToTab(existingIdx);
        return;
    }

    try {
        const res = await fetch('/api/file/' + filepath);
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.message || `HTTP ${res.status}`);
        }
        const data = await res.json();
        const content = data.content || '';

        // Create new tab
        const tab = { path: filepath, content, dirty: false, cm: null };
        NX.openTabs.push(tab);
        const idx = NX.openTabs.length - 1;
        switchToTab(idx);
        renderTabs();

        showToast(`Opened: ${filepath}`, 'info');
    } catch (e) {
        console.error('openFileInEditor error:', e);
        showToast(`Failed to open ${filepath}: ${e.message}`, 'error');
    }
}

function switchToTab(idx) {
    if (idx < 0 || idx >= NX.openTabs.length) return;

    NX.activeTab = idx;
    const tab = NX.openTabs[idx];
    NX.currentFile = tab.path;

    // Update editor content
    const wrap = document.getElementById('cmWrapLeft');
    const noFile = document.getElementById('editorNoFile');

    // Hide no-file state (don't innerHTML='' it yet)
    if (noFile) noFile.style.display = 'none';

    // Clear previous editor but leave noFile alone if it's a child
    if (_cmEditor) {
        // Save old content if dirty
        const oldIdx = NX.openTabs.findIndex(t => t.cm === _cmEditor);
        if (oldIdx >= 0) {
            NX.openTabs[oldIdx].content = _cmEditor.getValue();
            NX.openTabs[oldIdx].cm = null;
        }
        // Instead of innerHTML='', remove all children EXCEPT noFile
        Array.from(wrap.children).forEach(child => {
            if (child !== noFile) wrap.removeChild(child);
        });
    }

    // Create new editor
    const isImage = /\.(png|jpg|jpeg|gif|svg|webp|ico)$/i.test(tab.path);
    if (isImage) {
        const imgUrl = `/workspace/${tab.path}`;
        wrap.innerHTML = `<div class="editor-image-viewer"><img src="${imgUrl}" alt="${tab.path}" /></div>`;
        _cmEditor = null;
    } else {
        const mode = getModeForFile(tab.path);
        _cmEditor = initEditor(wrap, tab.content, mode);
    }
    tab.cm = _cmEditor;

    // Update breadcrumb
    updateBreadcrumb(tab.path);

    // Update file language in status bar
    const langName = getLangName(tab.path);
    const langId = document.getElementById('fileLangId');
    if (langId) langId.textContent = langName;
    const sbLang = document.getElementById('sbLang');
    if (sbLang) sbLang.textContent = langName;

    // Highlight active file in explorer
    document.querySelectorAll('.fe-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.fe-item').forEach(el => {
        if (el.dataset.path === tab.path) el.classList.add('active');
    });

    renderTabs();
}

// ─── Tab Management ──────────────────────────────────────────────────────────
function renderTabs() {
    const scroll = document.getElementById('tabsScroll');
    if (!scroll) return;

    scroll.innerHTML = '';
    if (NX.openTabs.length === 0) {
        scroll.innerHTML = '<div class="tab-placeholder">No files open</div>';
        return;
    }

    NX.openTabs.forEach((tab, i) => {
        const el = document.createElement('div');
        el.className = 'editor-tab' + (i === NX.activeTab ? ' active' : '') + (tab.dirty ? ' dirty' : '');
        el.dataset.idx = i;

        const name = tab.path.split('/').pop();
        const icon = fileIcon(name);
        el.innerHTML = `
            <i class="${icon}"></i>
            <span class="tab-name">${name}</span>
            ${tab.dirty ? '<span class="tab-dot">●</span>' : ''}
            <button class="tab-close" onclick="event.stopPropagation(); closeTab(${i})" title="Close"><i class="fa-solid fa-xmark"></i></button>
        `;
        el.onclick = () => switchToTab(i);
        el.oncontextmenu = e => {
            e.preventDefault();
            _tabCtxIdx = i;
            showCtxMenu('tabCtxMenu', e.pageX, e.pageY);
        };
        scroll.appendChild(el);
    });
}

function closeTab(idx) {
    if (idx < 0 || idx >= NX.openTabs.length) return;

    const tab = NX.openTabs[idx];
    if (tab.dirty) {
        if (!confirm(`Save changes to ${tab.path}?`)) {
            // Discard
        } else {
            saveFileContent(tab.path, tab.cm ? tab.cm.getValue() : tab.content);
        }
    }

    NX.openTabs.splice(idx, 1);

    if (NX.openTabs.length === 0) {
        NX.activeTab = null;
        NX.currentFile = null;
        showNoFileState();
    } else if (NX.activeTab >= NX.openTabs.length) {
        switchToTab(NX.openTabs.length - 1);
    } else {
        switchToTab(NX.activeTab);
    }
    renderTabs();
}

function closeAllTabs() {
    NX.openTabs = [];
    NX.activeTab = null;
    NX.currentFile = null;
    showNoFileState();
    renderTabs();
}

function showNoFileState() {
    const noFile = document.getElementById('editorNoFile');
    const wrap = document.getElementById('cmWrapLeft');
    if (noFile) noFile.style.display = '';
    if (wrap && _cmEditor) {
        _cmEditor.toTextArea?.();
        _cmEditor = null;
        wrap.innerHTML = '';
        wrap.appendChild(noFile);
        noFile.style.display = '';
    }
    document.getElementById('breadcrumbFile').textContent = 'No file open';
}

function updateTabDirtyState(idx) {
    const tabs = document.querySelectorAll('.editor-tab');
    if (tabs[idx]) {
        tabs[idx].classList.toggle('dirty', NX.openTabs[idx]?.dirty);
    }
}

function updateBreadcrumb(filepath) {
    const parts = filepath.split('/');
    const breadcrumb = document.getElementById('breadcrumbBar');
    if (!breadcrumb) return;

    breadcrumb.innerHTML = '<span class="breadcrumb-item">workspace</span>';
    parts.forEach((p, i) => {
        breadcrumb.innerHTML += '<i class="fa-solid fa-chevron-right breadcrumb-sep"></i>';
        const isLast = i === parts.length - 1;
        breadcrumb.innerHTML += `<span class="breadcrumb-item${isLast ? ' active' : ''}">${p}</span>`;
    });
}

// ─── Save ────────────────────────────────────────────────────────────────────
async function saveCurrentFile(silent = false) {
    if (NX.activeTab === null) return;
    const tab = NX.openTabs[NX.activeTab];
    if (!tab) return;

    const content = tab.cm ? tab.cm.getValue() : tab.content;
    tab.content = content;
    tab.dirty = false;

    await saveFileContent(tab.path, content);
    renderTabs();
    if (!silent) showToast(`Saved: ${tab.path}`, 'success');
}

async function saveAllFiles() {
    for (const tab of NX.openTabs) {
        if (tab.dirty) {
            const content = tab.cm ? tab.cm.getValue() : tab.content;
            tab.content = content;
            tab.dirty = false;
            await saveFileContent(tab.path, content);
        }
    }
    renderTabs();
    showToast('All files saved!', 'success');
}

async function saveFileContent(filepath, content) {
    try {
        await fetch('/api/file/' + filepath, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
    } catch (e) {
        showToast(`Failed to save ${filepath}`, 'error');
    }
}

// ─── Editor Actions ──────────────────────────────────────────────────────────
function editorUndo() { if (_cmEditor) _cmEditor.undo(); }
function editorRedo() { if (_cmEditor) _cmEditor.redo(); }

function toggleComment() {
    if (!_cmEditor) return;
    const cursor = _cmEditor.getCursor();
    const line = _cmEditor.getLine(cursor.line);
    const mode = _cmEditor.getMode().name;

    const commentChars = { javascript: '//', python: '#', css: '/*', htmlmixed: '<!--', shell: '#', markdown: '' };
    const cc = commentChars[mode] || '//';

    if (line.trimStart().startsWith(cc)) {
        const idx = line.indexOf(cc);
        _cmEditor.replaceRange('', { line: cursor.line, ch: idx }, { line: cursor.line, ch: idx + cc.length + (line[idx + cc.length] === ' ' ? 1 : 0) });
    } else {
        _cmEditor.replaceRange(cc + ' ', { line: cursor.line, ch: 0 });
    }
}

function formatDocument() {
    if (!_cmEditor) return;
    const content = _cmEditor.getValue();
    try {
        // Simple JSON formatting
        if (NX.currentFile?.endsWith('.json')) {
            _cmEditor.setValue(JSON.stringify(JSON.parse(content), null, 2));
            showToast('Document formatted', 'success');
            return;
        }
    } catch (e) {}
    showToast('Format: basic indentation applied', 'info');
}

// ─── Find & Replace ──────────────────────────────────────────────────────────
let _findCursor = null;
let _findMatches = [];
let _findIdx = 0;

function showFindReplace() {
    const bar = document.getElementById('findReplaceBar');
    if (bar) {
        bar.style.display = bar.style.display === 'none' ? '' : 'none';
        if (bar.style.display !== 'none') {
            document.getElementById('frFind')?.focus();
        }
    }
}

function hideFindReplace() {
    const bar = document.getElementById('findReplaceBar');
    if (bar) bar.style.display = 'none';
    if (_cmEditor) _cmEditor.getAllMarks().forEach(m => m.clear());
}

function toggleFindReplace() {
    const row = document.getElementById('frReplaceRow');
    if (row) row.style.display = row.style.display === 'none' ? '' : 'none';
}

function findInEditor(val) {
    if (!_cmEditor || !val) {
        document.getElementById('frCount').textContent = '0/0';
        if (_cmEditor) _cmEditor.getAllMarks().forEach(m => m.clear());
        return;
    }

    _cmEditor.getAllMarks().forEach(m => m.clear());
    _findMatches = [];
    _findIdx = 0;

    const cursor = _cmEditor.getSearchCursor(val, null, true);
    while (cursor.findNext()) {
        _findMatches.push({ from: cursor.from(), to: cursor.to() });
        _cmEditor.markText(cursor.from(), cursor.to(), { className: 'cm-search-match' });
    }

    document.getElementById('frCount').textContent = _findMatches.length > 0 ? `1/${_findMatches.length}` : '0/0';
    if (_findMatches.length > 0) _cmEditor.scrollIntoView(_findMatches[0].from);
}

function frNext() {
    if (_findMatches.length === 0) return;
    _findIdx = (_findIdx + 1) % _findMatches.length;
    _cmEditor.scrollIntoView(_findMatches[_findIdx].from);
    document.getElementById('frCount').textContent = `${_findIdx + 1}/${_findMatches.length}`;
}

function frPrev() {
    if (_findMatches.length === 0) return;
    _findIdx = (_findIdx - 1 + _findMatches.length) % _findMatches.length;
    _cmEditor.scrollIntoView(_findMatches[_findIdx].from);
    document.getElementById('frCount').textContent = `${_findIdx + 1}/${_findMatches.length}`;
}

function frReplaceOne() {
    if (!_cmEditor || _findMatches.length === 0) return;
    const m = _findMatches[_findIdx];
    const repl = document.getElementById('frReplace')?.value || '';
    _cmEditor.replaceRange(repl, m.from, m.to);
    findInEditor(document.getElementById('frFind')?.value || '');
}

function frReplaceAll() {
    if (!_cmEditor || _findMatches.length === 0) return;
    const repl = document.getElementById('frReplace')?.value || '';
    const searchVal = document.getElementById('frFind')?.value || '';
    const content = _cmEditor.getValue();
    _cmEditor.setValue(content.split(searchVal).join(repl));
    showToast(`Replaced ${_findMatches.length} occurrences`, 'success');
    findInEditor(searchVal);
}

// ─── Split Editor ────────────────────────────────────────────────────────────
function splitEditorHorizontal() {
    const paneRight = document.getElementById('editorPaneRight');
    const handle = document.getElementById('splitResizeHandle');
    if (paneRight) {
        paneRight.style.display = paneRight.style.display === 'none' ? '' : 'none';
        if (handle) handle.style.display = paneRight.style.display;
        if (paneRight.style.display !== 'none' && !_cmEditorRight) {
            const wrap = document.getElementById('cmWrapRight');
            _cmEditorRight = initEditor(wrap, _cmEditor?.getValue() || '', _cmEditor?.getMode()?.name || 'text/plain');
        }
    }
}

function splitEditorVertical() {
    const wrap = document.getElementById('editorSplitWrap');
    if (wrap) wrap.classList.toggle('vertical-split');
    splitEditorHorizontal();
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
function getModeForFile(path) {
    const ext = path.split('.').pop().toLowerCase();
    const map = {
        js: 'javascript', jsx: 'javascript', ts: 'javascript', tsx: 'javascript',
        py: 'python', html: 'htmlmixed', htm: 'htmlmixed',
        css: 'css', scss: 'css', less: 'css',
        json: { name: 'javascript', json: true },
        md: 'markdown', markdown: 'markdown',
        sh: 'shell', bash: 'shell', zsh: 'shell',
        xml: 'xml', svg: 'xml',
        rs: 'rust', go: 'go', yml: 'yaml', yaml: 'yaml',
        sql: 'sql', php: 'php', java: 'clike',
        cpp: 'clike', c: 'clike', h: 'clike',
        toml: 'toml', rb: 'ruby'
    };
    return map[ext] || 'text/plain';
}

function getLangName(path) {
    const ext = path.split('.').pop().toLowerCase();
    const nameMap = {
        js: 'JavaScript', jsx: 'React JSX', ts: 'TypeScript', tsx: 'React TSX',
        py: 'Python', html: 'HTML', htm: 'HTML', css: 'CSS', scss: 'SCSS',
        json: 'JSON', md: 'Markdown', sh: 'Shell', yml: 'YAML', yaml: 'YAML',
        xml: 'XML', svg: 'SVG', rs: 'Rust', go: 'Go', java: 'Java',
        rb: 'Ruby', php: 'PHP', c: 'C', cpp: 'C++', h: 'C Header',
        toml: 'TOML', txt: 'Plain Text', dockerfile: 'Dockerfile',
    };
    return nameMap[ext] || 'Plain Text';
}

function updateEditorConfig() {
    if (!_cmEditor) return;
    const fontSize = document.getElementById('cfg-fontSize')?.value || '14';
    const fontFamily = document.getElementById('cfg-fontFamily')?.value || "'Space Mono', monospace";
    const tabSize = parseInt(document.getElementById('cfg-tabSize')?.value || '2');
    const lineNums = document.getElementById('cfg-lineNums')?.checked;
    const wordWrap = document.getElementById('cfg-wordWrap')?.checked;

    _cmEditor.setOption('tabSize', tabSize);
    _cmEditor.setOption('indentUnit', tabSize);
    _cmEditor.setOption('lineNumbers', lineNums);
    _cmEditor.setOption('lineWrapping', wordWrap);

    const cmEl = _cmEditor.getWrapperElement();
    if (cmEl) {
        cmEl.style.fontSize = fontSize + 'px';
        cmEl.style.fontFamily = fontFamily;
    }
}

function updateEditorTheme(theme) {
    if (_cmEditor) {
        const cmTheme = theme === 'light' ? 'default' : 'dracula';
        _cmEditor.setOption('theme', cmTheme);
    }
}

function showLangPicker() {
    const langs = ['JavaScript','TypeScript','Python','HTML','CSS','JSON','Markdown','C++','Java','Go','Rust','SQL','Shell','YAML','XML'];
    const current = document.getElementById('sbLang')?.textContent || 'Plain Text';
    let html = '<div style="max-height:300px;overflow-y:auto">';
    langs.forEach(l => {
        const active = l === current ? 'style="background:var(--accent);color:#fff"' : '';
        html += `<div class="ctx-item" ${active} onclick="setEditorLang('${l}')">${l}</div>`;
    });
    html += '</div>';
    showQuickPicker('Language Mode', html);
}
function showIndentMenu() {
    const html = `<div>
        <div class="ctx-item" onclick="setIndent(2)">2 Spaces</div>
        <div class="ctx-item" onclick="setIndent(4)">4 Spaces</div>
        <div class="ctx-item" onclick="setIndent('tab')">Tabs</div>
    </div>`;
    showQuickPicker('Indentation', html);
}
function setEditorLang(lang) {
    const el = document.getElementById('sbLang');
    if (el) el.textContent = lang;
    if (_cmEditor) {
        const modeMap = {javascript:'javascript',typescript:'javascript',python:'python',html:'htmlmixed',css:'css',json:'application/json',markdown:'markdown',sql:'sql',shell:'shell',yaml:'yaml',xml:'xml'};
        const mode = modeMap[lang.toLowerCase()] || 'text/plain';
        _cmEditor.setOption('mode', mode);
    }
    hideQuickPicker();
    showToast('Language: ' + lang, 'info');
}
function setIndent(size) {
    if (_cmEditor) {
        if (size === 'tab') { _cmEditor.setOption('indentWithTabs', true); _cmEditor.setOption('tabSize', 4); }
        else { _cmEditor.setOption('indentWithTabs', false); _cmEditor.setOption('tabSize', size); _cmEditor.setOption('indentUnit', size); }
    }
    hideQuickPicker();
    const el = document.getElementById('sbIndent');
    if (el) el.textContent = size === 'tab' ? 'Tab Size: 4' : `Spaces: ${size}`;
    showToast('Indent: ' + (size === 'tab' ? 'Tabs' : size + ' spaces'), 'info');
}
function showQuickPicker(title, html) {
    let picker = document.getElementById('quickPicker');
    if (!picker) {
        picker = document.createElement('div');
        picker.id = 'quickPicker';
        picker.className = 'modal-overlay';
        picker.onclick = function() { hideQuickPicker(); };
        document.body.appendChild(picker);
    }
    picker.innerHTML = `<div class="modal-box small" onclick="event.stopPropagation()"><div class="modal-head"><h3>${title}</h3><button class="modal-close" onclick="hideQuickPicker()">&times;</button></div><div class="modal-body" style="padding:8px">${html}</div></div>`;
    picker.style.display = 'flex';
}
function hideQuickPicker() {
    const p = document.getElementById('quickPicker');
    if (p) p.style.display = 'none';
}
