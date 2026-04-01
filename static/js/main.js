'use strict';

// ===================== MARKED CONFIG =====================
marked.setOptions({ breaks: true, gfm: true });

// ===================== STATE =====================
const S = {
    sessionId: null,
    historyLen: 0,
    turnCount: 0,
    currentFile: null,
    currentFileContent: '',
    editorDirty: false,
    isAgentRunning: false,
    ctxFile: null,
    models: {},
    termHistory: [],
    termIdx: -1,
    allFiles: [],
    previewActive: false,
    currentTheme: 'dark',
};

// ===================== INIT =====================
document.addEventListener('DOMContentLoaded', () => {
    // Restore session from localStorage
    const saved = localStorage.getItem('claw_session_id');
    S.sessionId = saved || generateId();
    localStorage.setItem('claw_session_id', S.sessionId);

    // Restore theme
    const savedTheme = localStorage.getItem('claw_theme') || 'dark';
    applyTheme(savedTheme);

    loadModels();
    loadFiles();
    initResizer();
    initTerminal();
    initEditor();
    checkKeyStatus();
    document.getElementById('prompt').focus();
    updateStats();

    // Global event listeners
    document.addEventListener('click', hideCtxMenu);
    document.getElementById('newFilePath').addEventListener('keydown', e => {
        if (e.key === 'Enter') createNewFile();
        if (e.key === 'Escape') hideNewFileDialog();
    });
    document.getElementById('renameInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') doRename();
        if (e.key === 'Escape') hideRenameDialog();
    });
    const promptEl = document.getElementById('prompt');
    promptEl.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 200) + 'px';
        document.getElementById('charCount').textContent = this.value.length;
    });
    promptEl.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendPrompt(); }
        // Ctrl+L — clear chat
        if (e.ctrlKey && e.key === 'l') { e.preventDefault(); clearChat(); }
    });

    // Global keyboard shortcuts
    document.addEventListener('keydown', e => {
        // Esc closes modals
        if (e.key === 'Escape') {
            hideSettings();
            hideNewFileDialog();
            hideRenameDialog();
        }
        // Ctrl+/ — focus prompt
        if ((e.ctrlKey || e.metaKey) && e.key === '/') {
            e.preventDefault();
            document.getElementById('prompt').focus();
        }
        // Ctrl+Shift+N — new session
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'N') {
            e.preventDefault();
            newSession();
        }
    });
});

function generateId() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// ===================== THEME =====================
function toggleTheme() {
    const newTheme = S.currentTheme === 'dark' ? 'light' : 'dark';
    applyTheme(newTheme);
    localStorage.setItem('claw_theme', newTheme);
}

function applyTheme(theme) {
    S.currentTheme = theme;
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('themeBtn');
    if (btn) btn.innerHTML = theme === 'dark' ? '<i class="fa-solid fa-moon"></i>' : '<i class="fa-solid fa-sun"></i>';
    // Swap highlight.js theme
    const hlLink = document.getElementById('hljs-theme');
    if (hlLink) {
        hlLink.href = theme === 'dark'
            ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css'
            : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css';
    }
}

// ===================== KEY STATUS CHECK =====================
async function checkKeyStatus() {
    try {
        const res = await fetch('/api/settings/key-status');
        const data = await res.json();
        const hasKey = data.groq?.configured || data.openrouter?.configured;
        const hint = document.getElementById('welcomeKeyHint');
        if (hint) hint.style.display = hasKey ? 'none' : 'flex';
    } catch {}
}

// ===================== SIDEBAR =====================
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('collapsed');
}

// ===================== TABS =====================
function switchTab(tab) {
    ['preview','terminal','editor'].forEach(t => {
        const panel = document.getElementById(t + '-panel');
        if (panel) panel.classList.remove('active');
        const btn = document.getElementById('tab-' + t);
        if (btn) btn.classList.remove('active');
    });
    const panel = document.getElementById(tab + '-panel');
    if (panel) panel.classList.add('active');
    const btn = document.getElementById('tab-' + tab);
    if (btn) btn.classList.add('active');
    if (tab === 'terminal') document.getElementById('terminalInput').focus();
}

// ===================== FILE EXPLORER =====================
async function loadFiles() {
    const list = document.getElementById('fileList');
    try {
        const res = await fetch('/api/files');
        const data = await res.json();
        S.allFiles = data.files || [];
        document.getElementById('fileCount').textContent = S.allFiles.length;
        renderFileList(S.allFiles);
    } catch {
        list.innerHTML = '<div class="fe-empty" style="color:var(--red)">Error loading files</div>';
    }
}

function renderFileList(files) {
    const list = document.getElementById('fileList');
    if (!files.length) {
        list.innerHTML = '<div class="fe-empty">Workspace is empty</div>';
        return;
    }
    list.innerHTML = '';
    files.forEach(file => {
        const div = document.createElement('div');
        div.className = 'fe-item' + (file === S.currentFile ? ' active' : '');
        div.dataset.file = file;
        div.title = file;
        div.innerHTML = `<i class="${fileIcon(file)}"></i><span>${escHtml(file)}</span>`;
        div.onclick = () => openFileInEditor(file);
        div.oncontextmenu = (e) => showCtxMenu(e, file);
        list.appendChild(div);
    });
}

function filterFiles(query) {
    const q = query.trim().toLowerCase();
    if (!q) {
        renderFileList(S.allFiles);
        return;
    }
    const filtered = S.allFiles.filter(f => f.toLowerCase().includes(q));
    renderFileList(filtered);
}

function fileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const map = {
        html: 'fa-brands fa-html5 ic-html',
        htm: 'fa-brands fa-html5 ic-html',
        css: 'fa-brands fa-css3-alt ic-css',
        js: 'fa-brands fa-js ic-js',
        mjs: 'fa-brands fa-js ic-js',
        ts: 'fa-solid fa-code ic-ts',
        tsx: 'fa-solid fa-code ic-ts',
        jsx: 'fa-brands fa-react ic-ts',
        py: 'fa-brands fa-python ic-py',
        json: 'fa-solid fa-brackets-curly ic-json',
        md: 'fa-brands fa-markdown ic-md',
        rs: 'fa-solid fa-gear ic-rs',
        txt: 'fa-solid fa-file-lines ic-txt',
        svg: 'fa-regular fa-image',
        png: 'fa-regular fa-image',
        jpg: 'fa-regular fa-image',
        jpeg: 'fa-regular fa-image',
        gif: 'fa-regular fa-image',
        sh: 'fa-solid fa-terminal',
        bash: 'fa-solid fa-terminal',
        toml: 'fa-solid fa-file-code',
        yaml: 'fa-solid fa-file-code',
        yml: 'fa-solid fa-file-code',
        env: 'fa-solid fa-shield',
    };
    return (map[ext] || 'fa-regular fa-file') + ' ';
}

// ===================== FILE DRAG & DROP =====================
function onFileDragOver(e) {
    e.preventDefault();
    document.getElementById('fileList').classList.add('drag-over');
    document.getElementById('feDropHint').classList.add('show');
}
function onFileDragLeave(e) {
    document.getElementById('fileList').classList.remove('drag-over');
    document.getElementById('feDropHint').classList.remove('show');
}
async function onFileDrop(e) {
    e.preventDefault();
    document.getElementById('fileList').classList.remove('drag-over');
    document.getElementById('feDropHint').classList.remove('show');
    const files = e.dataTransfer.files;
    if (files.length) await uploadFileList(files);
}
function triggerUpload() {
    document.getElementById('uploadInput').click();
}
async function handleFileUpload(files) {
    if (!files.length) return;
    await uploadFileList(files);
}
async function uploadFileList(files) {
    const fd = new FormData();
    for (const f of files) fd.append(f.name, f, f.name);
    try {
        showToast(`Uploading ${files.length} file(s)...`, 'info');
        const res = await fetch('/api/upload', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.uploaded?.length) {
            await loadFiles();
            showToast(`Uploaded: ${data.uploaded.join(', ')}`, 'success');
        }
        if (data.errors?.length) {
            data.errors.forEach(e => showToast(`Upload error: ${e.file}`, 'error'));
        }
    } catch { showToast('Upload failed', 'error'); }
}

// ===================== EDITOR =====================
function initEditor() {
    const ta = document.getElementById('editorTextarea');
    ta.addEventListener('scroll', syncLineNumbers);
    ta.addEventListener('input', syncLineNumbers);
    ta.addEventListener('keydown', editorKeyHandler);
}

function editorKeyHandler(e) {
    if (e.key === 'Tab') {
        e.preventDefault();
        const ta = e.target;
        const start = ta.selectionStart, end = ta.selectionEnd;
        ta.value = ta.value.slice(0, start) + '  ' + ta.value.slice(end);
        ta.selectionStart = ta.selectionEnd = start + 2;
        syncLineNumbers();
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveCurrentFile();
    }
}

function syncLineNumbers() {
    const ta = document.getElementById('editorTextarea');
    const ln = document.getElementById('lineNumbers');
    const lines = ta.value.split('\n').length;
    let html = '';
    for (let i = 1; i <= lines; i++) html += `<div>${i}</div>`;
    ln.innerHTML = html;
    ln.scrollTop = ta.scrollTop;
}

function onEditorChange() {
    S.editorDirty = true;
    const saveBtn = document.getElementById('saveBtn');
    if (saveBtn) {
        saveBtn.classList.add('unsaved');
        saveBtn.style.display = 'flex';
    }
    syncLineNumbers();
}

async function openFileInEditor(filepath) {
    S.currentFile = filepath;
    document.querySelectorAll('.fe-item').forEach(e => e.classList.remove('active'));
    const activeEl = document.querySelector(`.fe-item[data-file="${filepath}"]`);
    if (activeEl) activeEl.classList.add('active');

    const ext = filepath.split('.').pop().toLowerCase();
    const previewable = ['html', 'htm'];

    if (previewable.includes(ext)) {
        document.getElementById('browserUrl').value = '/workspace/' + filepath;
        refreshBrowser();
        switchTab('preview');
        return;
    }

    try {
        const res = await fetch('/api/file/' + filepath);
        const data = await res.json();
        const content = data.content || '';

        S.currentFileContent = content;
        S.editorDirty = false;

        const ta = document.getElementById('editorTextarea');
        ta.value = content;
        syncLineNumbers();

        document.getElementById('editorFilename').textContent = filepath;

        const saveBtn = document.getElementById('saveBtn');
        saveBtn.style.display = 'flex';
        saveBtn.classList.remove('unsaved');

        const langs = { js:'JavaScript', ts:'TypeScript', jsx:'JSX', tsx:'TSX', py:'Python', html:'HTML', css:'CSS', json:'JSON', md:'Markdown', rs:'Rust', sh:'Shell', toml:'TOML', yaml:'YAML', yml:'YAML' };
        document.getElementById('editorLang').textContent = langs[ext] || ext.toUpperCase();

        switchTab('editor');
    } catch {
        showToast('Could not open file', 'error');
    }
}

async function saveCurrentFile() {
    if (!S.currentFile) return;
    const content = document.getElementById('editorTextarea').value;
    try {
        const res = await fetch('/api/file/' + S.currentFile, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        if (res.ok) {
            S.editorDirty = false;
            S.currentFileContent = content;
            const saveBtn = document.getElementById('saveBtn');
            saveBtn.classList.remove('unsaved');
            showToast(`Saved ${S.currentFile}`, 'success');
            if (S.currentFile.endsWith('.html')) {
                document.getElementById('browserUrl').value = '/workspace/' + S.currentFile;
                setTimeout(refreshBrowser, 300);
            }
        }
    } catch { showToast('Save failed', 'error'); }
}

function closeEditor() {
    S.currentFile = null;
    S.editorDirty = false;
    document.getElementById('editorFilename').textContent = 'No file open';
    document.getElementById('editorTextarea').value = '';
    document.getElementById('lineNumbers').innerHTML = '';
    document.getElementById('saveBtn').style.display = 'none';
    document.getElementById('editorLang').textContent = '';
    switchTab('preview');
}

// ===================== RENAME =====================
function ctxRename() {
    if (!S.ctxFile) return;
    document.getElementById('renameModal').classList.add('open');
    const inp = document.getElementById('renameInput');
    inp.value = S.ctxFile.split('/').pop();
    inp.focus();
    inp.select();
}
function hideRenameDialog(e) {
    if (e && e.target !== document.getElementById('renameModal')) return;
    document.getElementById('renameModal').classList.remove('open');
}
async function doRename() {
    const newName = document.getElementById('renameInput').value.trim();
    if (!newName || !S.ctxFile) return;
    const dir = S.ctxFile.includes('/') ? S.ctxFile.split('/').slice(0,-1).join('/') + '/' : '';
    const newPath = dir + newName;
    try {
        // Read old file content
        const readRes = await fetch('/api/file/' + S.ctxFile);
        const readData = await readRes.json();
        // Write to new path
        await fetch('/api/file/' + newPath, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: readData.content })
        });
        // Delete old file
        await fetch('/api/file/' + S.ctxFile, { method: 'DELETE' });
        document.getElementById('renameModal').classList.remove('open');
        if (S.currentFile === S.ctxFile) {
            S.currentFile = newPath;
            document.getElementById('editorFilename').textContent = newPath;
        }
        await loadFiles();
        showToast(`Renamed to ${newPath}`, 'success');
    } catch { showToast('Rename failed', 'error'); }
}

// ===================== NEW FILE DIALOG =====================
function showNewFileDialog() {
    document.getElementById('newFileModal').classList.add('open');
    document.getElementById('newFilePath').value = '';
    document.getElementById('newFilePath').focus();
}
function hideNewFileDialog(e) {
    if (e && e.target !== document.getElementById('newFileModal')) return;
    document.getElementById('newFileModal').classList.remove('open');
}
async function createNewFile() {
    const path = document.getElementById('newFilePath').value.trim();
    if (!path) return;
    try {
        await fetch('/api/file/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, content: '' })
        });
        document.getElementById('newFileModal').classList.remove('open');
        await loadFiles();
        await openFileInEditor(path);
        showToast(`Created ${path}`, 'success');
    } catch { showToast('Error creating file', 'error'); }
}

// ===================== CONTEXT MENU =====================
function showCtxMenu(e, file) {
    e.preventDefault();
    S.ctxFile = file;
    const m = document.getElementById('ctxMenu');
    const vw = window.innerWidth, vh = window.innerHeight;
    let x = e.clientX, y = e.clientY;
    m.style.display = 'block';
    const mw = m.offsetWidth, mh = m.offsetHeight;
    m.style.display = '';
    if (x + mw > vw) x = vw - mw - 4;
    if (y + mh > vh) y = vh - mh - 4;
    m.style.top = y + 'px';
    m.style.left = x + 'px';
    m.classList.add('open');
}
function hideCtxMenu() { document.getElementById('ctxMenu').classList.remove('open'); }
function ctxOpen() { if (S.ctxFile) openFileInEditor(S.ctxFile); }
function ctxPreview() {
    if (!S.ctxFile) return;
    document.getElementById('browserUrl').value = '/workspace/' + S.ctxFile;
    refreshBrowser();
    switchTab('preview');
}
async function ctxDelete() {
    if (!S.ctxFile) return;
    if (!confirm(`Delete "${S.ctxFile}"?`)) return;
    try {
        await fetch('/api/file/' + S.ctxFile, { method: 'DELETE' });
        if (S.currentFile === S.ctxFile) closeEditor();
        await loadFiles();
        showToast(`Deleted ${S.ctxFile}`, 'info');
    } catch { showToast('Error deleting file', 'error'); }
}

// ===================== PREVIEW =====================
function refreshBrowser() {
    const frame = document.getElementById('previewFrame');
    const url = document.getElementById('browserUrl').value;
    frame.src = '';
    setTimeout(() => { frame.src = url; }, 30);
    document.getElementById('previewEmpty').classList.add('hidden');
    S.previewActive = true;
}
function openInNewTab() { window.open(document.getElementById('browserUrl').value, '_blank'); }

// ===================== TERMINAL =====================
function initTerminal() {
    const inp = document.getElementById('terminalInput');
    inp.addEventListener('keydown', async e => {
        if (e.key === 'Enter') {
            const cmd = inp.value.trim();
            if (!cmd) return;
            S.termHistory.unshift(cmd);
            if (S.termHistory.length > 100) S.termHistory.pop();
            S.termIdx = -1;
            inp.value = '';
            await runTerminalCommand(cmd);
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (S.termIdx < S.termHistory.length - 1) S.termIdx++;
            inp.value = S.termHistory[S.termIdx] || '';
        }
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (S.termIdx > 0) S.termIdx--;
            else S.termIdx = -1;
            inp.value = S.termHistory[S.termIdx] || '';
        }
    });
}

function runTerminalFromBtn() {
    const inp = document.getElementById('terminalInput');
    const cmd = inp.value.trim();
    if (!cmd) return;
    S.termHistory.unshift(cmd);
    S.termIdx = -1;
    inp.value = '';
    runTerminalCommand(cmd);
}

async function runTerminalCommand(cmd) {
    const out = document.getElementById('terminalOutput');
    const cmdDiv = document.createElement('div');
    cmdDiv.className = 'cmd-line';
    cmdDiv.textContent = '$ ' + cmd;
    out.appendChild(cmdDiv);
    out.scrollTop = out.scrollHeight;
    switchTab('terminal');

    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'cmd-output';
    loadingDiv.textContent = '...';
    out.appendChild(loadingDiv);

    try {
        const res = await fetch('/api/terminal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        loadingDiv.textContent = data.output || '(no output)';
    } catch {
        loadingDiv.className = 'cmd-error';
        loadingDiv.textContent = 'Error: server unreachable';
    }
    out.scrollTop = out.scrollHeight;
    await loadFiles();
}

function clearTerminal() {
    document.getElementById('terminalOutput').innerHTML =
        '<div class="term-welcome">Terminal cleared — commands run inside <strong>agent_workspace/</strong></div>';
}

// ===================== MODELS =====================
const MODEL_GROUPS = [
    {
        key: 'smart',
        label: '🤖 Smart Combo — Auto-Routing',
        match: m => m.provider === 'smart',
    },
    {
        key: 'groq_free',
        label: '⚡ Groq — Ultra-Fast & Free',
        match: m => m.provider === 'groq' && m.tier === 'free',
    },
    {
        key: 'or_free_coding',
        label: '💻 Coding Specialists — Free',
        match: m => m.provider === 'openrouter' && m.tier === 'free' && m.role === 'coding',
    },
    {
        key: 'or_free_thinking',
        label: '🧠 Thinking / Reasoning — Free',
        match: m => m.provider === 'openrouter' && m.tier === 'free' && m.role === 'thinking',
    },
    {
        key: 'or_free_powerful',
        label: '🚀 Powerful Large Models — Free',
        match: m => m.provider === 'openrouter' && m.tier === 'free' && ['powerful','balanced'].includes(m.role),
    },
    {
        key: 'paid_fast',
        label: '★ Premium — Fast & Affordable',
        match: m => m.tier === 'paid' && ['fast','balanced'].includes(m.role),
    },
    {
        key: 'paid_coding',
        label: '★ Premium — Coding Specialists',
        match: m => m.tier === 'paid' && m.role === 'coding',
    },
    {
        key: 'paid_thinking',
        label: '★ Premium — Deep Reasoning',
        match: m => m.tier === 'paid' && m.role === 'thinking',
    },
    {
        key: 'paid_powerful',
        label: '★ Premium — Most Powerful',
        match: m => m.tier === 'paid' && m.role === 'powerful',
    },
];

async function loadModels() {
    const sel = document.getElementById('modelSelect');
    try {
        const res = await fetch('/api/models');
        const data = await res.json();
        sel.innerHTML = '';

        data.models.forEach(m => { S.models[m.id] = m; });

        const placed = new Set();

        for (const group of MODEL_GROUPS) {
            const members = data.models.filter(m => group.match(m) && !placed.has(m.id));
            if (!members.length) continue;
            const g = document.createElement('optgroup');
            g.label = group.label;
            members.forEach(m => {
                placed.add(m.id);
                const o = document.createElement('option');
                o.value = m.id;
                o.textContent = (m.emoji ? m.emoji + ' ' : '') + m.label + (m.short ? '  — ' + m.short : '');
                if (m.active) o.selected = true;
                g.appendChild(o);
            });
            sel.appendChild(g);
        }

        // Any stragglers
        const leftover = data.models.filter(m => !placed.has(m.id));
        if (leftover.length) {
            const g = document.createElement('optgroup');
            g.label = 'Other';
            leftover.forEach(m => {
                const o = document.createElement('option');
                o.value = m.id;
                o.textContent = m.label;
                if (m.active) o.selected = true;
                g.appendChild(o);
            });
            sel.appendChild(g);
        }

        const active = S.models[data.active];
        if (active) updateModelInfo(active);
        updateProviderLabel(active?.provider || 'groq');
    } catch {
        sel.innerHTML = '<option>Error loading models</option>';
    }
}

const ROLE_META = {
    fast:     { label: '⚡ Fast',        cls: 'role-fast' },
    thinking: { label: '🧠 Reasoning',   cls: 'role-thinking' },
    coding:   { label: '💻 Coding',      cls: 'role-coding' },
    powerful: { label: '🚀 Powerful',    cls: 'role-powerful' },
    balanced: { label: '⚖ Balanced',    cls: 'role-balanced' },
};

function updateModelInfo(m) {
    const role = ROLE_META[m.role] || ROLE_META.balanced;
    const badge = document.getElementById('modelRoleBadge');
    badge.textContent = role.label;
    badge.className = 'model-role-badge ' + (m.provider === 'smart' ? 'role-smart' : role.cls);

    const price = document.getElementById('modelPrice');
    price.textContent = m.price_note || (m.tier === 'free' ? 'Free' : '');
    price.style.color = m.tier === 'free' ? 'var(--green)' : 'var(--yellow)';

    document.getElementById('modelDesc').textContent = m.description;
    document.getElementById('modelCtx').textContent =
        'Context: ' + fmtCtx(m.context) + (m.provider === 'smart' ? '  · Multi-model routing' : '  · ' + m.provider);
}

function updateProviderLabel(provider) {
    const el = document.getElementById('activeProvider');
    if (!el) return;
    const labels = { groq: 'Groq', openrouter: 'OpenRouter', smart: 'Smart' };
    el.textContent = labels[provider] || provider;
}

function fmtCtx(n) {
    if (n >= 1000000) return (n/1000000).toFixed(0) + 'M tokens';
    if (n >= 1000) return Math.round(n/1000) + 'K tokens';
    return n + ' tokens';
}

async function switchModel(modelId) {
    if (!modelId) return;
    const m = S.models[modelId];
    if (m) {
        updateModelInfo(m);
        updateProviderLabel(m.provider);
    }
    try {
        await fetch('/api/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: modelId })
        });
        showToast(`Switched to ${m?.label || modelId}`, 'info');
    } catch { showToast('Error switching model', 'error'); }
}

// ===================== QUICK PROMPT =====================
function quickPrompt(text) {
    const inp = document.getElementById('prompt');
    inp.value = text;
    inp.style.height = 'auto';
    inp.style.height = Math.min(inp.scrollHeight, 200) + 'px';
    document.getElementById('charCount').textContent = text.length;
    inp.focus();
}

// ===================== SESSION =====================
async function newSession() {
    if (!confirm('Start a new session? The current conversation will be cleared.')) return;
    try {
        await fetch(`/api/session/${S.sessionId}/clear`, { method: 'POST' });
    } catch {}
    S.sessionId = generateId();
    localStorage.setItem('claw_session_id', S.sessionId);
    S.historyLen = 0;
    S.turnCount = 0;
    clearChat();
    updateStats();
    showToast('New session started', 'info');
}

function updateStats() {
    document.getElementById('historyCount').textContent = S.historyLen;
    document.getElementById('turnCounter').textContent = S.turnCount + ' turn' + (S.turnCount !== 1 ? 's' : '');
}

// ===================== STOP AGENT =====================
async function stopAgent() {
    try {
        await fetch('/api/chat/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: S.sessionId })
        });
        showToast('Stop signal sent', 'warning');
    } catch { showToast('Could not send stop signal', 'error'); }
}

// ===================== SEND PROMPT =====================
async function sendPrompt() {
    const text = document.getElementById('prompt').value.trim();
    if (!text || S.isAgentRunning) return;

    appendMessage(text, 'user');
    document.getElementById('prompt').value = '';
    document.getElementById('prompt').style.height = 'auto';
    document.getElementById('charCount').textContent = '0';

    setAgentRunning(true);
    setAgentStatus('yellow', 'Working...');
    showStepBar('Initializing agent...');

    const agentBubble = createAgentBubble();
    const contentDiv = agentBubble.querySelector('.agent-content');

    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text, session_id: S.sessionId })
        });

        if (!res.ok) throw new Error('HTTP ' + res.status);
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let accumulatedText = '';
        let toolCalls = 0;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6).trim();
                if (raw === '[DONE]') break;
                try {
                    const evt = JSON.parse(raw);

                    if (evt.type === 'thinking' && evt.text) {
                        if (evt.text.length > 20 && !evt.text.startsWith('Thinking (turn')) {
                            showStepBar(evt.text.length > 70 ? evt.text.slice(0, 70) + '...' : evt.text);
                            let tb = contentDiv.querySelector('.thought-block');
                            if (!tb) {
                                tb = document.createElement('div');
                                tb.className = 'thought-block';
                                tb.innerHTML = `<div class="thought-header" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'"><i class="fa-solid fa-brain"></i> Reasoning <i class="fa-solid fa-chevron-down" style="margin-left:auto"></i></div><div class="thought-content"></div>`;
                                contentDiv.appendChild(tb);
                            }
                            tb.querySelector('.thought-content').textContent = evt.text;
                        } else if (evt.text.startsWith('Thinking (turn')) {
                            showStepBar(evt.text);
                        }
                    }

                    else if (evt.type === 'tool_call') {
                        toolCalls++;
                        const step = document.createElement('div');
                        step.className = 'tool-step';
                        const toolIcon = toolIcons[evt.tool] || 'fa-solid fa-wrench';
                        const shortPayload = (evt.payload || '').replace(/\n/g, ' ').slice(0, 90);
                        step.innerHTML = `
                            <div class="tool-step-icon"><i class="${toolIcon}"></i></div>
                            <div class="tool-step-body">
                                <div class="tool-step-title"><span class="tool-badge-name">${escHtml(evt.tool)}</span></div>
                                <div class="tool-step-detail">${escHtml(shortPayload)}</div>
                            </div>`;
                        contentDiv.appendChild(step);
                        showStepBar(`Running ${evt.tool}...`);
                        S.turnCount++;
                        updateStats();
                        scrollBottom();
                    }

                    else if (evt.type === 'tool_result') {
                        const steps = contentDiv.querySelectorAll('.tool-step:not(.result)');
                        const lastStep = steps[steps.length - 1];
                        if (lastStep) {
                            lastStep.classList.add('result');
                            lastStep.querySelector('.tool-step-icon i').className = 'fa-solid fa-check';
                            const detail = lastStep.querySelector('.tool-step-detail');
                            if (detail) {
                                const first = (evt.result || '').split('\n')[0].slice(0, 80);
                                detail.textContent = (evt.elapsed ? `(${evt.elapsed}s) ` : '') + first;
                            }
                        }
                    }

                    else if (evt.type === 'token') {
                        accumulatedText = (accumulatedText + '\n\n' + evt.text).trim();
                        updateStreamingText(contentDiv, accumulatedText);
                        scrollBottom();
                    }

                    else if (evt.type === 'error') {
                        const errDiv = document.createElement('div');
                        errDiv.className = 'tool-step error-step';
                        errDiv.innerHTML = `<div class="tool-step-icon"><i class="fa-solid fa-circle-exclamation"></i></div><div class="tool-step-body"><div class="tool-step-title">Error</div><div class="tool-step-detail">${escHtml(evt.message)}</div></div>`;
                        contentDiv.appendChild(errDiv);
                        scrollBottom();
                    }

                    else if (evt.type === 'key_error') {
                        const banner = document.createElement('div');
                        banner.className = 'key-error-banner';
                        const providerName = evt.error_type?.includes('groq') ? 'Groq' : 'OpenRouter';
                        let linkHtml = '';
                        if (evt.error_type?.includes('NO_KEY') || evt.error_type?.includes('BAD_KEY')) {
                            const url = evt.error_type?.includes('groq') ? 'https://console.groq.com/keys' : 'https://openrouter.ai/keys';
                            linkHtml = ` <a href="${url}" target="_blank">Get a key →</a> or <button onclick="showSettings()">Open Settings</button>`;
                        }
                        banner.innerHTML = `<i class="fa-solid fa-key"></i><div><strong>API Key Issue (${providerName})</strong><br>${escHtml(evt.message)}${linkHtml}</div>`;
                        contentDiv.appendChild(banner);
                        // Auto-open settings for missing/bad key
                        if (evt.error_type?.includes('NO_KEY') || evt.error_type?.includes('BAD_KEY')) {
                            setTimeout(() => showSettings(), 600);
                        }
                        scrollBottom();
                    }

                    else if (evt.type === 'stopped') {
                        const banner = document.createElement('div');
                        banner.className = 'stopped-banner';
                        banner.innerHTML = `<i class="fa-solid fa-stop"></i> Agent stopped after ${evt.turns || 0} turn(s).`;
                        contentDiv.appendChild(banner);
                        scrollBottom();
                    }

                    else if (evt.type === 'done') {
                        S.historyLen = evt.history_len || S.historyLen + 1;
                        if (evt.files_changed?.length) {
                            await loadFiles();
                            highlightChangedFiles(evt.files_changed);
                            // Auto-navigate to first HTML file
                            const htmlFile = evt.files_changed.find(f => f.endsWith('.html'));
                            if (htmlFile) {
                                document.getElementById('browserUrl').value = '/workspace/' + htmlFile;
                                setTimeout(refreshBrowser, 500);
                            }
                        }
                        updateStats();
                    }
                } catch {}
            }
        }

        finalizeAgentBubble(contentDiv, accumulatedText);

    } catch (err) {
        contentDiv.innerHTML = `<p style="color:var(--red)"><i class="fa-solid fa-circle-exclamation"></i> Error: ${escHtml(err.message)}</p>`;
    } finally {
        setAgentRunning(false);
        setAgentStatus('green', 'Ready');
        hideStepBar();
        scrollBottom();
    }
}

const toolIcons = {
    ListDirTool: 'fa-solid fa-folder-open',
    FileReadTool: 'fa-solid fa-file-lines',
    ViewFileLinesTool: 'fa-solid fa-list-ol',
    SearchTool: 'fa-solid fa-magnifying-glass',
    FileEditTool: 'fa-solid fa-pen-to-square',
    FileDeleteTool: 'fa-solid fa-trash',
    BashTool: 'fa-solid fa-terminal',
};

function updateStreamingText(contentDiv, text) {
    let textDiv = contentDiv.querySelector('.streamed-text');
    if (!textDiv) {
        textDiv = document.createElement('div');
        textDiv.className = 'streamed-text streaming-cursor';
        contentDiv.appendChild(textDiv);
    }
    textDiv.innerHTML = marked.parse(text || '');
}

function finalizeAgentBubble(contentDiv, text) {
    const textDiv = contentDiv.querySelector('.streamed-text');
    if (textDiv) {
        textDiv.classList.remove('streaming-cursor');
        if (text) textDiv.innerHTML = marked.parse(text);
    } else if (text) {
        const d = document.createElement('div');
        d.className = 'streamed-text';
        d.innerHTML = marked.parse(text);
        contentDiv.appendChild(d);
    }
    // Add copy buttons and syntax highlight
    contentDiv.querySelectorAll('pre').forEach(pre => {
        if (!pre.querySelector('.copy-btn')) {
            const btn = document.createElement('button');
            btn.className = 'copy-btn';
            btn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy';
            btn.onclick = () => copyCode(btn);
            pre.appendChild(btn);
        }
        const code = pre.querySelector('code');
        if (code && !code.dataset.highlighted) {
            try { hljs.highlightElement(code); code.dataset.highlighted = '1'; } catch {}
        }
    });
}

function createAgentBubble() {
    const d = document.createElement('div');
    d.className = 'message agent';
    const inner = document.createElement('div');
    inner.className = 'agent-content';
    const typingEl = document.createElement('div');
    typingEl.className = 'typing-indicator';
    typingEl.innerHTML = '<span></span><span></span><span></span>';
    inner.appendChild(typingEl);
    d.appendChild(inner);
    document.getElementById('chat').appendChild(d);
    scrollBottom();
    setTimeout(() => { typingEl.remove(); }, 800);
    return d;
}

// ===================== UI HELPERS =====================
function setAgentRunning(running) {
    S.isAgentRunning = running;
    const btn = document.getElementById('sendBtn');
    const inp = document.getElementById('prompt');
    const stopBtn = document.getElementById('stopBtn');
    btn.disabled = running;
    inp.disabled = running;
    if (stopBtn) stopBtn.style.display = running ? 'flex' : 'none';
}

function setAgentStatus(color, text) {
    const st = document.getElementById('agentStatus');
    const dot = st.querySelector('.status-dot');
    dot.className = 'status-dot ' + color;
    st.lastChild.textContent = ' ' + text;
}

function showStepBar(text) {
    document.getElementById('stepLabel').textContent = text;
    document.getElementById('stepBar').classList.add('active');
}
function hideStepBar() {
    document.getElementById('stepBar').classList.remove('active');
}

function appendMessage(text, role) {
    const d = document.createElement('div');
    d.className = 'message ' + role;
    d.textContent = text;
    document.getElementById('chat').appendChild(d);
    scrollBottom();
}

function clearChat() {
    const chat = document.getElementById('chat');
    chat.innerHTML = '';
    const wc = document.createElement('div');
    wc.className = 'welcome-card';
    wc.innerHTML = `<div class="welcome-icon">⚡</div><h2>New Session</h2><p>Ready for your next project. What should I build?</p><div class="welcome-tips"><span>💡 Try a Quick Start template</span></div>`;
    chat.appendChild(wc);
}

function scrollBottom() {
    const c = document.getElementById('chat');
    c.scrollTop = c.scrollHeight;
}

function escHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function copyCode(btn) {
    const pre = btn.closest('pre');
    const code = pre.querySelector('code') || pre;
    const text = code.innerText || code.textContent;
    navigator.clipboard.writeText(text).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
        btn.style.color = 'var(--green)';
        setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 2000);
    }).catch(() => {
        showToast('Copy failed', 'error');
    });
}

function highlightChangedFiles(files) {
    files.forEach(f => {
        const el = document.querySelector(`.fe-item[data-file="${f}"]`);
        if (el) {
            el.classList.add('changed');
            setTimeout(() => el.classList.remove('changed'), 5000);
        }
    });
}

// ===================== SETTINGS =====================
function showSettings() {
    document.getElementById('settingsModal').classList.add('open');
    // Pre-fill key status
    fetch('/api/settings/key-status').then(r => r.json()).then(data => {
        if (data.groq?.configured) {
            const s = document.getElementById('groqKeyStatus');
            s.className = 'key-status ok';
            s.textContent = `✓ Configured (${data.groq.prefix})`;
        }
        if (data.openrouter?.configured) {
            const s = document.getElementById('orKeyStatus');
            s.className = 'key-status ok';
            s.textContent = `✓ Configured (${data.openrouter.prefix})`;
        }
    }).catch(() => {});
}
function hideSettings(e) {
    if (e && e.target !== document.getElementById('settingsModal')) return;
    document.getElementById('settingsModal').classList.remove('open');
}

async function saveAndValidateKey(provider) {
    const input = document.getElementById(provider === 'groq' ? 'groqKeyInput' : 'orKeyInput');
    const statusEl = document.getElementById(provider === 'groq' ? 'groqKeyStatus' : 'orKeyStatus');
    const key = input.value.trim();
    if (!key) { statusEl.className = 'key-status err'; statusEl.textContent = 'Please enter a key'; return; }

    statusEl.className = 'key-status loading';
    statusEl.textContent = 'Saving & testing...';

    try {
        // Save the key
        await fetch('/api/settings/set-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, key })
        });
        // Validate it
        const res = await fetch('/api/settings/validate-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, key })
        });
        const data = await res.json();
        if (data.ok) {
            statusEl.className = 'key-status ok';
            statusEl.textContent = '✓ ' + data.message;
            input.value = '';
            showToast(`${provider} key saved & verified`, 'success');
            checkKeyStatus();
        } else {
            statusEl.className = 'key-status err';
            statusEl.textContent = '✗ ' + data.message;
        }
    } catch {
        statusEl.className = 'key-status err';
        statusEl.textContent = 'Connection error';
    }
}

// ===================== EXPORT CHAT =====================
function exportChat() {
    const msgs = document.querySelectorAll('#chat .message');
    let text = '# Claw IDE — Chat Export\n\n';
    text += `Date: ${new Date().toLocaleString()}\n\n---\n\n`;
    msgs.forEach(m => {
        const role = m.classList.contains('user') ? '**User**' : '**Agent**';
        text += `## ${role}\n${m.innerText}\n\n---\n\n`;
    });
    const blob = new Blob([text], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'claw-chat-' + new Date().toISOString().slice(0,10) + '.md';
    a.click();
    showToast('Chat exported', 'success');
}

// ===================== TOAST =====================
const toastContainer = (() => {
    const d = document.createElement('div');
    d.className = 'toast-container';
    document.body.appendChild(d);
    return d;
})();

function showToast(msg, type = 'info') {
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.textContent = msg;
    toastContainer.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3000);
}

// ===================== RESIZABLE PANES =====================
function initResizer() {
    const handle = document.getElementById('resizeHandle');
    const chatSec = document.getElementById('chatSection');
    let dragging = false;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        dragging = true;
        handle.classList.add('active');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', e => {
        if (!dragging) return;
        const sidebar = document.getElementById('sidebar');
        const sidebarW = sidebar.classList.contains('collapsed') ? 0 : sidebar.offsetWidth;
        const newW = e.clientX - sidebarW;
        const wrapper = document.querySelector('.main-wrapper');
        const totalW = wrapper.offsetWidth;
        if (newW >= 280 && newW <= totalW - 220) {
            chatSec.style.flex = 'none';
            chatSec.style.width = newW + 'px';
        }
    });
    document.addEventListener('mouseup', () => {
        if (dragging) {
            dragging = false;
            handle.classList.remove('active');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}
