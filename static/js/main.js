'use strict';

// ===================== MARKED CONFIG =====================
marked.setOptions({ breaks: true, gfm: true });

// ===================== STATE =====================
const S = {
    sessionId: generateId(),
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
};

// ===================== INIT =====================
document.addEventListener('DOMContentLoaded', () => {
    loadModels();
    loadFiles();
    initResizer();
    initTerminal();
    initEditor();
    switchTab('preview');
    document.getElementById('prompt').focus();
    updateStats();
    document.addEventListener('click', hideCtxMenu);
    document.getElementById('newFilePath').addEventListener('keydown', e => {
        if (e.key === 'Enter') createNewFile();
        if (e.key === 'Escape') hideNewFileDialog();
    });
    document.getElementById('prompt').addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 200) + 'px';
        document.getElementById('charCount').textContent = this.value.length;
    });
    document.getElementById('prompt').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendPrompt(); }
    });
});

function generateId() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// ===================== SIDEBAR =====================
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('collapsed');
}

// ===================== TABS =====================
function switchTab(tab) {
    ['preview','terminal','editor'].forEach(t => {
        document.getElementById(t + '-panel').classList.remove('active');
        const btn = document.getElementById('tab-' + t);
        if (btn) btn.classList.remove('active');
    });
    document.getElementById(tab + '-panel').classList.add('active');
    const activeBtn = document.getElementById('tab-' + tab);
    if (activeBtn) activeBtn.classList.add('active');
    if (tab === 'terminal') document.getElementById('terminalInput').focus();
}

// ===================== FILE EXPLORER =====================
async function loadFiles() {
    const list = document.getElementById('fileList');
    try {
        const res = await fetch('/api/files');
        const data = await res.json();
        const files = data.files || [];
        document.getElementById('fileCount').textContent = files.length;

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
            div.innerHTML = `<i class="${fileIcon(file)}"></i><span>${file}</span>`;
            div.onclick = () => openFileInEditor(file);
            div.oncontextmenu = (e) => showCtxMenu(e, file);
            list.appendChild(div);
        });
    } catch {
        list.innerHTML = '<div class="fe-empty" style="color:var(--red)">Error loading files</div>';
    }
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
        py: 'fa-brands fa-python ic-py',
        json: 'fa-solid fa-brackets-curly ic-json',
        md: 'fa-brands fa-markdown ic-md',
        rs: 'fa-solid fa-gear ic-rs',
        txt: 'fa-solid fa-file-lines ic-txt',
        svg: 'fa-regular fa-image',
        sh: 'fa-solid fa-terminal',
    };
    return (map[ext] || 'fa-regular fa-file') + ' ';
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
    const dot = document.querySelector('.unsaved-dot');
    if (dot) dot.classList.add('show');
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

        const fn = document.getElementById('editorFilename');
        fn.textContent = filepath;

        const saveBtn = document.getElementById('saveBtn');
        saveBtn.style.display = 'flex';
        saveBtn.classList.remove('unsaved');

        const langEl = document.getElementById('editorLang');
        const langs = { js:'JavaScript', ts:'TypeScript', py:'Python', html:'HTML', css:'CSS', json:'JSON', md:'Markdown', rs:'Rust', sh:'Shell' };
        langEl.textContent = langs[ext] || ext.toUpperCase();

        switchTab('editor');
    } catch (e) {
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
    m.style.top = e.clientY + 'px';
    m.style.left = e.clientX + 'px';
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
    frame.src = frame.src === url ? (frame.src = '', url) : url;
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

    try {
        const res = await fetch('/api/terminal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        const outDiv = document.createElement('div');
        outDiv.className = 'cmd-output';
        outDiv.textContent = data.output || '';
        out.appendChild(outDiv);
    } catch {
        const errDiv = document.createElement('div');
        errDiv.className = 'cmd-error';
        errDiv.textContent = 'Error: server unreachable';
        out.appendChild(errDiv);
    }
    out.scrollTop = out.scrollHeight;
    await loadFiles();
}

function clearTerminal() {
    document.getElementById('terminalOutput').innerHTML =
        '<div class="term-welcome">Terminal cleared</div>';
}

// ===================== MODELS =====================
async function loadModels() {
    const sel = document.getElementById('modelSelect');
    const desc = document.getElementById('modelDesc');
    const tier = document.getElementById('modelTier');
    try {
        const res = await fetch('/api/models');
        const data = await res.json();
        sel.innerHTML = '';
        const groups = { free: [], paid: [] };
        data.models.forEach(m => {
            S.models[m.id] = m;
            groups[m.tier] = groups[m.tier] || [];
            groups[m.tier].push(m);
        });
        const makeGroup = (label, models) => {
            const g = document.createElement('optgroup');
            g.label = label;
            models.forEach(m => {
                const o = document.createElement('option');
                o.value = m.id;
                o.textContent = m.label;
                if (m.active) o.selected = true;
                g.appendChild(o);
            });
            return g;
        };
        if (groups.free?.length) sel.appendChild(makeGroup('✦ Free Models', groups.free));
        if (groups.paid?.length) sel.appendChild(makeGroup('★ Premium Models', groups.paid));

        const active = S.models[data.active];
        if (active) {
            desc.textContent = active.description;
            tier.textContent = active.tier === 'free' ? '✦ Free · ' + fmtCtx(active.context) : '★ Premium · ' + fmtCtx(active.context);
            tier.style.color = active.tier === 'free' ? 'var(--green)' : 'var(--yellow)';
        }
    } catch {
        sel.innerHTML = '<option>Error loading models</option>';
    }
}

function fmtCtx(n) {
    if (n >= 1000000) return (n/1000000).toFixed(0) + 'M ctx';
    if (n >= 1000) return Math.round(n/1000) + 'K ctx';
    return n + ' ctx';
}

async function switchModel(modelId) {
    if (!modelId) return;
    const m = S.models[modelId];
    if (m) {
        document.getElementById('modelDesc').textContent = m.description;
        const tier = document.getElementById('modelTier');
        tier.textContent = m.tier === 'free' ? '✦ Free · ' + fmtCtx(m.context) : '★ Premium · ' + fmtCtx(m.context);
        tier.style.color = m.tier === 'free' ? 'var(--green)' : 'var(--yellow)';
    }
    try {
        await fetch('/api/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: modelId })
        });
        showToast('Model switched', 'info');
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

    // Create agent message bubble
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
                    handleStreamEvent(evt, contentDiv, () => accumulatedText, (t) => { accumulatedText = t; }, (n) => { toolCalls = n; return toolCalls; }, toolCalls);
                    if (evt.type === 'thinking') {
                        showStepBar(evt.text.length > 60 ? evt.text.slice(0, 60) + '...' : evt.text);
                    }
                    if (evt.type === 'tool_call') {
                        toolCalls++;
                        showStepBar(`Running ${evt.tool}...`);
                        S.turnCount++;
                        updateStats();
                    }
                    if (evt.type === 'done') {
                        S.historyLen = evt.history_len || S.historyLen + 1;
                        if (evt.files_changed?.length) {
                            await loadFiles();
                            highlightChangedFiles(evt.files_changed);
                            setTimeout(refreshBrowser, 500);
                        }
                        updateStats();
                    }
                } catch {}
            }
        }

        // Finalize bubble
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

function handleStreamEvent(evt, contentDiv, getText, setText, incTools, toolCount) {
    if (evt.type === 'thinking') {
        // Show as a thought block if substantial
        if (evt.text && evt.text.length > 20 && !evt.text.startsWith('Thinking (turn')) {
            let tb = contentDiv.querySelector('.thought-block');
            if (!tb) {
                tb = document.createElement('div');
                tb.className = 'thought-block';
                tb.innerHTML = `<div class="thought-header" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'"><i class="fa-solid fa-brain"></i> Reasoning</div><div class="thought-content"></div>`;
                contentDiv.appendChild(tb);
            }
            tb.querySelector('.thought-content').textContent = evt.text;
        }
    }
    else if (evt.type === 'tool_call') {
        const step = document.createElement('div');
        step.className = 'tool-step';
        const toolIcon = toolIcons[evt.tool] || 'fa-solid fa-wrench';
        step.innerHTML = `
            <div class="tool-step-icon"><i class="${toolIcon}"></i></div>
            <div class="tool-step-body">
                <div class="tool-step-title"><span class="tool-badge-name">${escHtml(evt.tool)}</span></div>
                <div class="tool-step-detail">${escHtml((evt.payload || '').slice(0, 80))}</div>
            </div>`;
        contentDiv.appendChild(step);
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
                const first = (evt.result || '').split('\n')[0].slice(0, 70);
                detail.textContent = (evt.elapsed ? `(${evt.elapsed}s) ` : '') + first;
            }
        }
    }
    else if (evt.type === 'token') {
        const newText = getText() + '\n\n' + evt.text;
        setText(newText.trim());
        updateStreamingText(contentDiv, newText.trim());
    }
    else if (evt.type === 'error') {
        const errDiv = document.createElement('div');
        errDiv.className = 'tool-step error-step';
        errDiv.innerHTML = `<div class="tool-step-icon"><i class="fa-solid fa-circle-exclamation"></i></div><div class="tool-step-body"><div class="tool-step-title">Error</div><div class="tool-step-detail">${escHtml(evt.message)}</div></div>`;
        contentDiv.appendChild(errDiv);
    }
    scrollBottom();
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
    // Remove streaming cursor, apply full markdown
    const textDiv = contentDiv.querySelector('.streamed-text');
    if (textDiv) {
        textDiv.classList.remove('streaming-cursor');
        textDiv.innerHTML = marked.parse(text || '');
    } else if (text) {
        const d = document.createElement('div');
        d.className = 'streamed-text';
        d.innerHTML = marked.parse(text);
        contentDiv.appendChild(d);
    }
    // Add copy buttons to code blocks
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

    setTimeout(() => { typingEl.remove(); }, 600);
    return d;
}

// ===================== UI HELPERS =====================
function setAgentRunning(running) {
    S.isAgentRunning = running;
    const btn = document.getElementById('sendBtn');
    const inp = document.getElementById('prompt');
    btn.disabled = running;
    inp.disabled = running;
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
    wc.innerHTML = `<div class="welcome-icon">⚡</div><h2>New Session</h2><p>Ready for your next project. What should I build?</p>`;
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
    const code = btn.closest('pre').querySelector('code');
    navigator.clipboard.writeText(code.innerText).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
        btn.style.color = 'var(--green)';
        setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 2000);
    });
}

function highlightChangedFiles(files) {
    files.forEach(f => {
        const el = document.querySelector(`.fe-item[data-file="${f}"]`);
        if (el) {
            el.classList.add('changed');
            setTimeout(() => el.classList.remove('changed'), 4000);
        }
    });
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
    setTimeout(() => t.remove(), 3000);
}

// ===================== EXPORT CHAT =====================
function exportChat() {
    const msgs = document.querySelectorAll('#chat .message');
    let text = '# Claw IDE Chat Export\n\n';
    msgs.forEach(m => {
        const role = m.classList.contains('user') ? 'User' : 'Agent';
        text += `## ${role}\n${m.innerText}\n\n`;
    });
    const blob = new Blob([text], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'claw-chat-' + Date.now() + '.md';
    a.click();
    showToast('Chat exported', 'success');
}

// ===================== RESIZABLE PANES =====================
function initResizer() {
    const handle = document.getElementById('resizeHandle');
    const chatSec = document.getElementById('chatSection');
    let dragging = false;

    handle.addEventListener('mousedown', () => {
        dragging = true;
        handle.classList.add('active');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', e => {
        if (!dragging) return;
        const rect = handle.parentElement.getBoundingClientRect();
        const newW = e.clientX - rect.left;
        if (newW >= 280 && newW <= rect.width - 240) {
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
