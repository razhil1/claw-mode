// ===================== MARKED + HIGHLIGHT INIT =====================
marked.setOptions({
    highlight: (code, lang) => {
        const l = hljs.getLanguage(lang) ? lang : 'plaintext';
        return hljs.highlight(code, { language: l }).value;
    },
    breaks: true, gfm: true
});

// ===================== GLOBALS =====================
const chat = document.getElementById("chat");
const promptInput = document.getElementById("prompt");
const loading = document.getElementById("loading");
const sendBtn = document.getElementById("sendBtn");
let currentFile = null;
let terminalHistory = [];
let terminalHistoryIdx = -1;

// ===================== TEXTAREA RESIZE =====================
promptInput.addEventListener('input', function () {
    this.style.height = '22px';
    this.style.height = Math.min(this.scrollHeight, 180) + 'px';
});
promptInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendPrompt(); }
});

// ===================== PANEL TABS =====================
function switchTab(tab) {
    document.querySelectorAll('.panel-tab').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + tab).classList.add('active');

    document.getElementById('preview-panel').classList.remove('active');
    document.getElementById('terminal-panel').classList.remove('active');
    document.getElementById('editor-panel').classList.remove('active');

    document.getElementById(tab + '-panel').classList.add('active');

    if (tab === 'terminal') {
        document.getElementById('terminalInput').focus();
    }
}

// ===================== FILE EXPLORER =====================
async function loadFiles() {
    const fileList = document.getElementById("fileList");
    try {
        const res = await fetch("/api/files");
        const data = await res.json();

        if (!data.files || !data.files.length) {
            fileList.innerHTML = '<div style="padding:10px;font-size:12px;color:var(--text-dimmer)">Workspace is empty</div>';
            return;
        }

        fileList.innerHTML = "";
        data.files.forEach(file => {
            const div = document.createElement("div");
            div.className = "fe-item" + (file === currentFile ? " active" : "");
            div.title = file;

            const ext = file.split('.').pop().toLowerCase();
            const icons = { html:'fa-brands fa-html5 ic-html', css:'fa-brands fa-css3-alt ic-css', js:'fa-brands fa-js ic-js', py:'fa-brands fa-python ic-py', json:'fa-solid fa-brackets-curly ic-json', md:'fa-brands fa-markdown ic-md' };
            const icon = icons[ext] || 'fa-solid fa-file';

            div.innerHTML = `<i class="${icon}"></i>${file}`;
            div.onclick = () => openFile(file);
            fileList.appendChild(div);
        });
    } catch (e) {
        fileList.innerHTML = '<div style="padding:10px;font-size:12px;color:var(--red)">Error loading files</div>';
    }
}

async function openFile(filepath) {
    currentFile = filepath;
    document.querySelectorAll('.fe-item').forEach(e => e.classList.remove('active'));
    event && event.currentTarget && event.currentTarget.classList.add('active');

    const ext = filepath.split('.').pop().toLowerCase();
    const previewable = ['html', 'htm', 'md'];

    if (previewable.includes(ext)) {
        document.getElementById("browserUrl").value = "/workspace/" + filepath;
        refreshBrowser();
        switchTab('preview');
    } else {
        // Show in editor tab
        try {
            const res = await fetch("/api/file/" + filepath);
            const data = await res.json();
            document.getElementById('editorHeader').textContent = filepath;
            const editorContent = document.getElementById('editorContent');
            editorContent.innerHTML = hljs.highlightAuto(data.content).value;
            switchTab('editor');
        } catch (e) {
            switchTab('preview');
        }
    }
}

// ===================== PREVIEW =====================
function refreshBrowser() {
    const frame = document.getElementById("previewFrame");
    const url = document.getElementById("browserUrl").value;
    frame.src = url;
}
function openInNewTab() {
    window.open(document.getElementById("browserUrl").value, "_blank");
}

// ===================== TERMINAL =====================
document.addEventListener('DOMContentLoaded', () => {
    const termInput = document.getElementById('terminalInput');
    if (termInput) {
        termInput.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                const cmd = termInput.value.trim();
                if (!cmd) return;
                terminalHistory.unshift(cmd);
                terminalHistoryIdx = -1;
                termInput.value = '';
                await runTerminalCommand(cmd);
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (terminalHistoryIdx < terminalHistory.length - 1) terminalHistoryIdx++;
                termInput.value = terminalHistory[terminalHistoryIdx] || '';
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (terminalHistoryIdx > 0) terminalHistoryIdx--;
                else terminalHistoryIdx = -1;
                termInput.value = terminalHistory[terminalHistoryIdx] || '';
            }
        });
    }
});

async function runTerminalCommand(cmd) {
    const output = document.getElementById('terminalOutput');
    const cmdLine = document.createElement('div');
    cmdLine.className = 'cmd-line';
    cmdLine.textContent = '$ ' + cmd;
    output.appendChild(cmdLine);
    output.scrollTop = output.scrollHeight;

    try {
        const res = await fetch('/api/terminal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        const outDiv = document.createElement('div');
        outDiv.className = 'cmd-output';
        outDiv.textContent = data.output;
        output.appendChild(outDiv);
    } catch (err) {
        const errDiv = document.createElement('div');
        errDiv.className = 'cmd-error';
        errDiv.textContent = 'Error: could not reach server.';
        output.appendChild(errDiv);
    }
    output.scrollTop = output.scrollHeight;
    loadFiles();
}

// ===================== FORMAT RESPONSE =====================
function formatAgentResponse(text) {
    if (!text) return "";
    // Hide raw TOOL: lines
    text = text.replace(/^TOOL:\s*.+$/gm, '').trim();
    const parts = text.split(/(<thought>[\s\S]*?<\/thought>)/gi);
    let html = "";
    parts.forEach(part => {
        if (part.toLowerCase().startsWith('<thought>')) {
            const m = part.match(/<thought>([\s\S]*?)<\/thought>/i);
            const inner = m ? m[1].trim() : "";
            html += `<div class="thought-block">
                <div class="thought-header" onclick="toggleThought(this)">
                    <i class="fa-solid fa-brain"></i> Reasoning Process
                </div>
                <div class="thought-content">${escapeHtml(inner)}</div>
            </div>`;
        } else if (part.trim()) {
            html += marked.parse(part);
        }
    });
    return html;
}

// ===================== SEND PROMPT =====================
async function sendPrompt() {
    const text = promptInput.value.trim();
    if (!text) return;
    appendMessage(text, "user");
    promptInput.value = ""; promptInput.style.height = '22px';
    loading.classList.add("active");
    sendBtn.disabled = true; promptInput.disabled = true;
    scrollBottom();

    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: text })
        });
        const data = await res.json();
        let html = formatAgentResponse(data.response || "");

        if (data.log && data.log.trim()) {
            html += `<div class="log-container">
                <div class="log-header" onclick="toggleLog(this)">
                    <span><i class="fa-solid fa-terminal"></i>&nbsp; Execution Log</span>
                    <i class="fa-solid fa-chevron-down"></i>
                </div>
                <div class="log-content">${escapeHtml(data.log)}</div>
            </div>`;
        }

        appendMessage(html, "agent", true);

        // Apply copy buttons + syntax highlighting
        document.querySelectorAll('.message.agent pre').forEach(block => {
            if (!block.querySelector('.copy-btn')) {
                const btn = document.createElement('button');
                btn.className = 'copy-btn';
                btn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy';
                btn.onclick = function() { copyCode(this); };
                block.appendChild(btn);
            }
            const codeEl = block.querySelector('code');
            if (codeEl && !codeEl.dataset.highlighted) {
                hljs.highlightElement(codeEl);
                codeEl.dataset.highlighted = '1';
            }
        });

        // Refresh file list and preview after agent acts
        await loadFiles();
        setTimeout(refreshBrowser, 400);

    } catch (err) {
        appendMessage("⚠️ Could not reach the agent server.", "agent");
    } finally {
        loading.classList.remove("active");
        sendBtn.disabled = false; promptInput.disabled = false;
        promptInput.focus(); scrollBottom();
    }
}

// ===================== UI UTILITIES =====================
function toggleLog(h) {
    const c = h.nextElementSibling;
    const i = h.querySelector('i.fa-chevron-down, i.fa-chevron-up');
    c.style.display = c.style.display === 'block' ? 'none' : 'block';
    if (i) i.className = c.style.display === 'block' ? 'fa-solid fa-chevron-up' : 'fa-solid fa-chevron-down';
}
function toggleThought(h) {
    const c = h.nextElementSibling;
    c.style.display = c.style.display === 'none' ? 'block' : 'none';
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
function clearChat() {
    const first = chat.firstElementChild;
    chat.innerHTML = '';
    if (first) chat.appendChild(first);
}
function scrollBottom() { chat.scrollTop = chat.scrollHeight; }
function appendMessage(content, role, isHtml = false) {
    const d = document.createElement("div");
    d.className = "message " + role;
    if (isHtml) d.innerHTML = content; else d.textContent = content;
    chat.appendChild(d);
    scrollBottom();
}
function escapeHtml(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

// ===================== RESIZABLE PANES =====================
function initResizer() {
    const handle = document.getElementById("resizeHandle");
    if (!handle) return;
    const chatSec = document.getElementById("chatSection");
    let dragging = false;
    handle.addEventListener("mousedown", () => { dragging = true; handle.classList.add('active'); document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'; });
    document.addEventListener("mousemove", e => {
        if (!dragging) return;
        const rect = handle.parentElement.getBoundingClientRect();
        const newW = e.clientX - rect.left;
        if (newW >= 320 && newW <= rect.width - 280) {
            chatSec.style.flex = 'none';
            chatSec.style.width = newW + 'px';
        }
    });
    document.addEventListener("mouseup", () => { if (dragging) { dragging = false; handle.classList.remove('active'); document.body.style.cursor = ''; document.body.style.userSelect = ''; } });
}

// ===================== MODELS =====================
let loadedModels = {};
async function loadModels() {
    const select = document.getElementById("modelSelect");
    const desc = document.getElementById("modelDesc");
    if(!select) return;
    try {
        const res = await fetch("/api/models");
        const data = await res.json();
        select.innerHTML = '';
        data.models.forEach(m => {
            loadedModels[m.id] = m;
            const opt = document.createElement("option");
            opt.value = m.id;
            opt.textContent = m.label;
            if (m.active) opt.selected = true;
            select.appendChild(opt);
        });
        if (data.active && loadedModels[data.active]) {
            desc.textContent = loadedModels[data.active].description;
        }
    } catch (e) {
        select.innerHTML = '<option value="">Error loading models</option>';
    }
}

async function switchModel(modelId) {
    if (!modelId) return;
    const desc = document.getElementById("modelDesc");
    if (loadedModels[modelId]) {
        desc.textContent = loadedModels[modelId].description;
    } else {
        desc.textContent = "Switching...";
    }
    
    try {
        await fetch("/api/model", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: modelId })
        });
    } catch (e) {
        desc.textContent = "Error saving model!";
    }
}

// ===================== BOOT =====================
document.addEventListener("DOMContentLoaded", () => {
    loadModels();
    loadFiles();
    initResizer();
    switchTab('preview');
    promptInput.focus();
    // Welcome terminal
    const out = document.getElementById('terminalOutput');
    if (out) {
        out.innerHTML = '<div class="cmd-line">Claw IDE Terminal — type commands to run in agent_workspace/</div>';
    }
});
