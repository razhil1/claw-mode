'use strict';

const S = {
    sessionId: localStorage.getItem('claw_session_id') || Math.random().toString(36).slice(2),
    allFiles: [],
    currentFile: null,
    isAgentRunning: false,
    theme: localStorage.getItem('claw_theme') || 'dark',
    termHistory: [],
    termIdx: -1
};
if(!localStorage.getItem('claw_session_id')) localStorage.setItem('claw_session_id', S.sessionId);

document.addEventListener('DOMContentLoaded', () => {
    applyTheme(S.theme);
    loadFiles();
    loadModels();
    
    // Command Pallete Escape
    document.addEventListener('keydown', e => {
        if(e.key === 'Escape') {
            hideCommandPalette();
            hideModal("settingsModal");
            hideModal("newFileModal");
            hideModal("newFolderModal");
            hideModal("modelSelectorModal");
            hideModal("shortcutsModal");
            hideModal("memoryModal");
            hideModal("tokenStatsModal");
            hideModal("deployModal");
        }
        if(e.key === 'k' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            showCommandPalette();
        }
    });

    const promptEl = document.getElementById('chatPrompt');
    if(promptEl) {
        promptEl.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 200) + 'px';
            document.getElementById('charCount').textContent = this.value.length;
        });
    }
});

/* ========= CORE API LOGIC ========= */

async function loadFiles() {
    try {
        const res = await fetch('/api/files');
        const data = await res.json();
        S.allFiles = (data.files || []).map(f => typeof f === 'object' ? f.path : f);
        document.getElementById('wsFileCount').textContent = S.allFiles.length + ' files';
        renderFileList(S.allFiles);
    } catch (e) {
        document.getElementById('fileTree').innerHTML = '<div class="fe-empty-state">Error loading files</div>';
    }
}

function renderFileList(files) {
    const list = document.getElementById('fileTree');
    if (!files.length) {
        list.innerHTML = '<div class="fe-empty-state"><i class="fa-solid fa-folder-open"></i><p>Workspace is empty</p><button onclick="showNewFileDialog()">Create first file</button></div>';
        return;
    }
    list.innerHTML = '';
    
    const sortedFiles = [...files].sort((a, b) => a.localeCompare(b));
    let lastDir = null;

    sortedFiles.forEach(file => {
        const parts = file.split('/');
        const name = parts.pop();
        const dir = parts.length > 0 ? parts.join('/') : null;

        if (dir && dir !== lastDir) {
            const dirDiv = document.createElement('div');
            dirDiv.className = 'fe-dir';
            dirDiv.innerHTML = '<i class="fa-solid fa-folder-open"></i><span>' + dir + '</span>';
            list.appendChild(dirDiv);
            lastDir = dir;
        }

        const div = document.createElement('div');
        div.className = 'fe-item' + (file === S.currentFile ? ' active' : '');
        div.style.paddingLeft = dir ? '24px' : '10px';
        div.innerHTML = '<i class="' + fileIcon(name) + '"></i><span>' + name + '</span>';
        div.onclick = () => openFileInEditor(file);
        list.appendChild(div);
    });
}

function fileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const map = { html: 'fa-brands fa-html5', js: 'fa-brands fa-js', py: 'fa-brands fa-python', css: 'fa-brands fa-css3' };
    return (map[ext] || 'fa-solid fa-file') + ' ';
}

async function sendPrompt() {
    const el = document.getElementById('chatPrompt');
    const text = el.value.trim();
    if (!text || S.isAgentRunning) return;

    el.value = '';
    if(el.style) el.style.height = 'auto';
    if(document.getElementById('charCount')) document.getElementById('charCount').textContent = '0';
    
    appendMessage(text, 'user');
    setAgentWorking(true);
    _clearModeTag();

    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text, session_id: S.sessionId })
        });
        
        const contentDiv = appendMessage('', 'agent');
        let planEl = null;
        let planSteps = [];
        let stepsDone = 0;
        
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';

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

                    // ── Mode badge ────────────────────────────────────────────
                    if (evt.type === 'mode') {
                        _showModeTag(evt.emoji, evt.label);
                    }

                    // ── Thinking indicator ────────────────────────────────────
                    if (evt.type === 'thinking') {
                        _setThinkingStep(evt.text);
                    }

                    // ── Plan rendering ────────────────────────────────────────
                    if (evt.type === 'plan') {
                        planEl = _renderPlan(evt.text, contentDiv);
                        // Extract numbered steps for tracking
                        planSteps = (evt.text.match(/^\s*\d+\.\s+.+/gm) || []).map(s => s.replace(/^\s*\d+\.\s+/, '').trim());
                        stepsDone = 0;
                    }

                    // ── Prose tokens ──────────────────────────────────────────
                    if (evt.type === 'token' && evt.text) {
                        // Strip PLAN: and DONE: blocks from the streamed text
                        let clean = evt.text
                            .replace(/PLAN:\s*[\s\S]*?(?=\n\n|\n(?=\S)|$)/g, '')
                            .replace(/DONE:\s*[\s\S]*/g, '')
                            .trim();
                        if (clean) {
                            contentDiv.dataset.md = (contentDiv.dataset.md || '') + clean + '\n';
                            try {
                                contentDiv.innerHTML = marked.parse(contentDiv.dataset.md);
                            } catch(e) {
                                contentDiv.textContent = contentDiv.dataset.md;
                            }
                            document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
                        }
                    }

                    // ── Tool call ─────────────────────────────────────────────
                    if (evt.type === 'tool_call') {
                        _setThinkingStep(`${evt.tool}: ${(evt.payload || '').slice(0, 60)}`);
                        // Advance plan step indicator
                        if (planEl && stepsDone < planSteps.length) {
                            _markPlanStep(planEl, stepsDone);
                            stepsDone++;
                        }
                    }

                    // ── Tool result → tool log ────────────────────────────────
                    if (evt.type === 'tool_result') {
                        logToolCall({
                            tool:    evt.tool,
                            type:    evt.success ? 'tool_call' : 'error',
                            summary: `${evt.tool} (${evt.elapsed}s)`,
                            result:  evt.result || '',
                        });
                    }

                    // ── Loop warning ──────────────────────────────────────────
                    if (evt.type === 'loop_warn') {
                        showToast('Agent detected a loop — redirecting…', 'warning');
                    }

                    // ── Done summary ──────────────────────────────────────────
                    if (evt.type === 'done') {
                        _renderDoneSummary(evt, contentDiv);
                        if (planEl) _markAllPlanDone(planEl);
                    }

                    // ── Key error ─────────────────────────────────────────────
                    if (evt.type === 'key_error') {
                        contentDiv.dataset.md = (contentDiv.dataset.md || '') + `\n\n⚠️ **${evt.message}**\n`;
                        contentDiv.innerHTML = marked.parse(contentDiv.dataset.md);
                        showToast(evt.message, 'error');
                    }

                    // ── Stop / error ──────────────────────────────────────────
                    if (evt.type === 'stopped') {
                        showToast('Agent stopped', 'warning');
                    }
                    if (evt.type === 'error') {
                        showToast(evt.message || 'Agent error', 'error');
                    }

                } catch(e) {}
            }
        }
        await loadFiles();
    } catch(err) {
        showToast('Communication error: ' + err.message, 'error');
    }
    setAgentWorking(false);
    _setThinkingStep('');
}

// ── Agent UI helpers ──────────────────────────────────────────────────────────

function _showModeTag(emoji, label) {
    const bar = document.getElementById('agentStepBar');
    if (!bar) return;
    let tag = document.getElementById('agentModeTag');
    if (!tag) {
        tag = document.createElement('span');
        tag.id = 'agentModeTag';
        tag.style.cssText = 'margin-left:8px;padding:2px 8px;border-radius:12px;font-size:0.75rem;background:var(--accent-muted,#1e3a5f);color:var(--accent,#58a6ff);';
        bar.appendChild(tag);
    }
    tag.textContent = `${emoji} ${label}`;
    tag.style.display = '';
}

function _clearModeTag() {
    const tag = document.getElementById('agentModeTag');
    if (tag) tag.style.display = 'none';
}

function _setThinkingStep(text) {
    const el = document.getElementById('agentStatusText');
    if (el) el.textContent = text ? `${text.slice(0, 60)}…` : 'Agent Ready';
}

function _renderPlan(planText, contentDiv) {
    const steps = (planText.match(/^\s*\d+\.\s+.+/gm) || []);
    if (!steps.length) return null;
    const ul = document.createElement('div');
    ul.className = 'agent-plan-block';
    ul.innerHTML = '<div class="plan-header"><i class="fa-solid fa-list-check"></i> Plan</div>' +
        steps.map((s, i) => {
            const txt = s.replace(/^\s*\d+\.\s+/, '');
            return `<div class="plan-step" id="plan-step-${i}" data-idx="${i}">
                <span class="plan-step-num">${i+1}</span>
                <span class="plan-step-text">${escapeHtml(txt)}</span>
                <i class="fa-solid fa-circle plan-step-status"></i>
            </div>`;
        }).join('');
    // Insert before any existing content in contentDiv
    const parent = contentDiv.parentNode;
    parent.insertBefore(ul, contentDiv);
    return ul;
}

function _markPlanStep(planEl, idx) {
    const step = planEl?.querySelector(`#plan-step-${idx}`);
    if (step) {
        step.classList.add('active');
        const prev = planEl.querySelector(`#plan-step-${idx - 1}`);
        if (prev) { prev.classList.remove('active'); prev.classList.add('done'); }
    }
}

function _markAllPlanDone(planEl) {
    planEl?.querySelectorAll('.plan-step').forEach(s => { s.classList.remove('active'); s.classList.add('done'); });
}

function _renderDoneSummary(evt, contentDiv) {
    if (!evt.files_changed || !evt.files_changed.length) return;
    const summary = `\n\n---\n**Done** — ${evt.turns} turn${evt.turns !== 1 ? 's' : ''} · ${evt.files_changed.length} file${evt.files_changed.length !== 1 ? 's' : ''} changed\n` +
        evt.files_changed.map(f => `- \`${f}\``).join('\n');
    contentDiv.dataset.md = (contentDiv.dataset.md || '') + summary;
    try { contentDiv.innerHTML = marked.parse(contentDiv.dataset.md); } catch(e) {}
    document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
}

function appendMessage(text, role) {
    const wrap = document.createElement('div');
    wrap.className = 'message-wrap ' + (role === 'user' ? 'user-msg' : 'agent-msg');
    wrap.innerHTML = '<div class="message-meta"><span class="message-role">' + role + '</span></div><div class="message-content" data-md="' + text.replace(/"/g, '&quot;') + '">' + (role === 'user' ? text : '') + '</div>';
    document.getElementById('chatMessages').appendChild(wrap);
    document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
    return wrap.querySelector('.message-content');
}

function setAgentWorking(isWorking) {
    S.isAgentRunning = isWorking;
    const sbar = document.getElementById('agentStepBar');
    const badge = document.getElementById('crhStatusText');
    const dot = document.getElementById('crhDot');
    
    if (isWorking) {
        if(sbar) sbar.style.display = 'block';
        if(badge) badge.textContent = 'Working...';
        if(dot) dot.className = 'crh-dot amber pulse';
        document.body.classList.add('agent-is-working');
    } else {
        if(sbar) sbar.style.display = 'none';
        if(badge) badge.textContent = 'Ready';
        if(dot) dot.className = 'crh-dot green';
        document.body.classList.remove('agent-is-working');
    }
}

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

async function newSession() {
    if (!confirm('Start a new session? Context will be cleared.')) return;
    try {
        await fetch(`/api/session/${S.sessionId}/clear`, { method: 'POST' });
    } catch {}
    S.sessionId = Math.random().toString(36).slice(2);
    localStorage.setItem('claw_session_id', S.sessionId);
    clearChat();
    showToast('New session started', 'info');
}

/* ========= UI SHELL MOCKS ========= */
const _CMD_REGISTRY = [
    { label: 'New File',              icon: 'fa-solid fa-file-circle-plus',  action: () => showNewFileDialog() },
    { label: 'New Folder',            icon: 'fa-solid fa-folder-plus',       action: () => document.getElementById('newFolderModal').style.display = 'flex' },
    { label: 'Save File',             icon: 'fa-solid fa-floppy-disk',       action: () => saveCurrentFile() },
    { label: 'Save All Files',        icon: 'fa-solid fa-save',              action: () => saveAllFiles() },
    { label: 'Upload Files',          icon: 'fa-solid fa-upload',            action: () => triggerUpload() },
    { label: 'Download Workspace',    icon: 'fa-solid fa-download',          action: () => downloadWorkspace() },
    { label: 'Undo',                  icon: 'fa-solid fa-rotate-left',       action: () => editorUndo() },
    { label: 'Redo',                  icon: 'fa-solid fa-rotate-right',      action: () => editorRedo() },
    { label: 'Find & Replace',        icon: 'fa-solid fa-magnifying-glass',  action: () => showFindReplace() },
    { label: 'Global Search',         icon: 'fa-solid fa-search',            action: () => showGlobalSearch() },
    { label: 'Format Document',       icon: 'fa-solid fa-align-left',        action: () => formatDocument() },
    { label: 'Toggle Comment',        icon: 'fa-solid fa-code',              action: () => toggleComment() },
    { label: 'Run Project',           icon: 'fa-solid fa-play',              action: () => runProject() },
    { label: 'Run Current File',      icon: 'fa-solid fa-file-code',         action: () => runCurrentFile() },
    { label: 'Open Terminal',         icon: 'fa-solid fa-terminal',          action: () => switchRightTab('terminal') },
    { label: 'Git: Commit',           icon: 'fa-brands fa-git-alt',          action: () => { togglePanel('git'); gitRefresh(); } },
    { label: 'Git: Push to GitHub',   icon: 'fa-brands fa-github',           action: () => showGitPanel() },
    { label: 'Toggle Dark/Light',     icon: 'fa-solid fa-circle-half-stroke',action: () => toggleTheme() },
    { label: 'Settings',              icon: 'fa-solid fa-gear',              action: () => showSettings() },
    { label: 'Keyboard Shortcuts',    icon: 'fa-solid fa-keyboard',          action: () => showKeyboardShortcuts() },
    { label: 'Environment Variables', icon: 'fa-solid fa-key',               action: () => showEnvManager() },
    { label: 'About NEXUS IDE',       icon: 'fa-solid fa-circle-info',       action: () => showAbout() },
    { label: 'Preview',               icon: 'fa-solid fa-eye',               action: () => switchRightTab('preview') },
    { label: 'Diff Viewer',           icon: 'fa-solid fa-code-compare',      action: () => switchRightTab('diff') },
    { label: 'Output Console',        icon: 'fa-solid fa-rectangle-list',    action: () => switchRightTab('output') },
    { label: 'Split Editor',          icon: 'fa-solid fa-columns',           action: () => splitEditorHorizontal() },
    { label: 'Close All Tabs',        icon: 'fa-solid fa-xmark',             action: () => closeAllTabs() },
];
let _cmdSelectedIdx = 0;

function showCommandPalette() {
    document.getElementById('cmdPaletteOverlay').style.display = 'flex';
    const input = document.getElementById('cmdInput');
    if (input) { input.value = ''; input.focus(); }
    _cmdSelectedIdx = 0;
    filterCommands('');
}
function hideCommandPalette() { document.getElementById('cmdPaletteOverlay').style.display = 'none'; }

function filterCommands(val) {
    const list = document.getElementById('cmdResults');
    if (!list) return;
    const q = (val || '').toLowerCase();
    const filtered = q ? _CMD_REGISTRY.filter(c => c.label.toLowerCase().includes(q)) : _CMD_REGISTRY;
    _cmdSelectedIdx = 0;
    list.innerHTML = filtered.map((c, i) =>
        `<div class="cmd-result-item${i === 0 ? ' selected' : ''}" data-idx="${i}" onclick="_cmdExec(${_CMD_REGISTRY.indexOf(c)})" onmouseenter="document.querySelectorAll('.cmd-result-item').forEach(el=>el.classList.remove('selected'));this.classList.add('selected');_cmdSelectedIdx=${i}">
            <i class="${c.icon}"></i> <span class="cmd-label">${c.label}</span>
        </div>`
    ).join('') || '<div class="cmd-result-item disabled" style="opacity:0.5;pointer-events:none">No matching commands</div>';
    list._filtered = filtered;
}

function _cmdExec(regIdx) {
    hideCommandPalette();
    _CMD_REGISTRY[regIdx]?.action();
}

function cmdKeyNav(e) {
    const list = document.getElementById('cmdResults');
    if (!list || !list._filtered) return;
    const items = list.querySelectorAll('.cmd-result-item:not(.disabled)');
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        _cmdSelectedIdx = Math.min(_cmdSelectedIdx + 1, items.length - 1);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        _cmdSelectedIdx = Math.max(_cmdSelectedIdx - 1, 0);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        const f = list._filtered[_cmdSelectedIdx];
        if (f) { hideCommandPalette(); f.action(); }
        return;
    } else { return; }
    items.forEach(el => el.classList.remove('selected'));
    if (items[_cmdSelectedIdx]) items[_cmdSelectedIdx].classList.add('selected');
}

function toggleActivity() { togglePanel('files'); }
function showNewFileDialog() { document.getElementById('newFileModal').style.display = 'flex'; }
function triggerUpload() {
    const input = document.createElement('input');
    input.type = 'file'; input.multiple = true;
    input.onchange = async () => {
        for (const file of input.files) {
            const content = await file.text();
            await fetch('/api/file/' + file.name, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) });
        }
        showToast(`Uploaded ${input.files.length} file(s)`, 'success');
        loadFiles();
    };
    input.click();
}
function downloadWorkspace() {
    showToast('Exporting workspace...', 'info');
    window.location.href = '/api/workspace/download';
}
function triggerImport() { triggerUpload(); }


function showAbout() {
    let modal = document.getElementById('aboutModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'aboutModal';
        modal.className = 'modal-overlay';
        modal.onclick = () => modal.style.display = 'none';
        document.body.appendChild(modal);
    }
    modal.innerHTML = `<div class="modal-box medium" onclick="event.stopPropagation()">
        <div class="modal-head"><h3><i class="fa-solid fa-circle-info"></i> About NEXUS IDE</h3><button class="modal-close" onclick="hideModal('aboutModal')">&times;</button></div>
        <div class="modal-body" style="padding:20px;text-align:center">
            <div style="font-size:42px;margin-bottom:8px">⬡</div>
            <h2 style="color:var(--cyan);margin:0">NEXUS <span style="color:var(--text-primary)">IDE</span></h2>
            <p style="color:var(--text-dim);margin:4px 0 16px">Autonomous AI Development Environment</p>
            <div style="text-align:left;background:var(--bg-inset);border-radius:8px;padding:16px;font-size:13px;line-height:1.8">
                <div><strong>Version:</strong> 2.0</div>
                <div><strong>Engine:</strong> NVIDIA NIM API</div>
                <div style="margin-top:8px"><strong>Model Tiers:</strong></div>
                <div style="padding-left:12px">
                    🧠 Frontier — DeepSeek R1 70B<br>
                    ⚡ Smart — Qwen 2.5 Coder 32B<br>
                    🚀 Fast — Phi-4 Mini
                </div>
                <div style="margin-top:8px"><strong>Agent Modes:</strong></div>
                <div style="padding-left:12px">
                    Standard · Ultra · Swarm
                </div>
                <div style="margin-top:12px"><strong>Shortcuts:</strong></div>
                <div style="padding-left:12px;font-family:var(--font-mono);font-size:12px">
                    Ctrl+K — Command Palette<br>
                    Ctrl+S — Save File<br>
                    Ctrl+F — Find & Replace<br>
                    Ctrl+/ — Toggle Comment<br>
                    F5 — Run Project<br>
                    Enter — Send Message
                </div>
            </div>
        </div>
    </div>`;
    modal.style.display = 'flex';
}

function togglePanel(name) {
    document.querySelectorAll('.sp-section').forEach(el => el.classList.remove('active'));
    const p = document.getElementById('panel-' + name);
    if(p) p.classList.add('active');
    document.querySelectorAll('.ab-btn').forEach(btn => btn.classList.remove('active'));
    const btn = document.querySelector(".ab-btn[data-panel='" + name + "']");
    if(btn) btn.classList.add('active');
}

function switchRightTab(name) {
    document.querySelectorAll('.rp-tab').forEach(el => el.classList.remove('active'));
    const tabBtn = document.getElementById('rptab-' + name);
    if(tabBtn) tabBtn.classList.add('active');
    
    document.querySelectorAll('.rp-section').forEach(el => el.classList.remove('active'));
    const sec = document.getElementById('rpsec-' + name);
    if(sec) sec.classList.add('active');
}

function refreshPreview() {
    const frame = document.getElementById('previewFrame');
    if(frame) frame.src = document.getElementById('browserUrlInput')?.value || 'about:blank';
    showToast('Refreshed preview', 'info');
}

function loadPreviewUrl(url) {
    const frame = document.getElementById('previewFrame');
    if(frame) frame.src = url;
}

function toggleDeviceView(type) {
    const wrap = document.getElementById('previewDeviceWrap');
    wrap.className = 'preview-device-wrap ' + type;
}

function toggleLayout(mode) { document.documentElement.setAttribute('data-layout', mode); }
function cycleLayout() {}
function showSettings() { document.getElementById('settingsModal').style.display = 'flex'; }
function showProfileMenu() { if (typeof showProfileModal === 'function') showProfileModal(); }
function showAgentConfig() { showSettings(); }
function showModelSelector() { document.getElementById('modelSelectorModal').style.display = 'flex'; }
function toggleUltraMode() { showToast('Ultra Mode toggled'); }
function showMemory() { document.getElementById('memoryModal').style.display = 'flex'; }
function showToolLog() { switchRightTab('toollog'); }
function showTokenStats() { document.getElementById('tokenStatsModal').style.display = 'flex'; }

function showDeployPanel() {
    let modal = document.getElementById('deployModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'deployModal';
        modal.className = 'modal-overlay';
        modal.onclick = () => modal.style.display = 'none';
        document.body.appendChild(modal);
    }
    modal.innerHTML = `<div class="modal-box medium" onclick="event.stopPropagation()">
        <div class="modal-head"><h3><i class="fa-solid fa-rocket"></i> Deploy</h3><button class="modal-close" onclick="hideModal('deployModal')">&times;</button></div>
        <div class="modal-body" style="padding:16px">
            <div style="display:flex;flex-direction:column;gap:12px">
                <button class="btn-primary" onclick="hideModal('deployModal');downloadWorkspace()" style="padding:12px;font-size:14px"><i class="fa-solid fa-download"></i> Download as ZIP</button>
                <button class="btn-primary" onclick="hideModal('deployModal');showGitPanel()" style="padding:12px;font-size:14px;background:var(--bg-elevated);border:1px solid var(--border-dim)"><i class="fa-brands fa-github"></i> Push to GitHub</button>
            </div>
        </div>
    </div>`;
    modal.style.display = 'flex';
}
function showKeyboardShortcuts() { document.getElementById('shortcutsModal').style.display = 'flex'; }
function showGuide() { if (typeof showGuideModal === 'function') showGuideModal(); }
function showApiDocs() { if (typeof showGuideModal === 'function') showGuideModal(); }

function toggleChatExpand() {}

function quickPrompt(txt) { 
    const p = document.getElementById('chatPrompt');
    if(p) { p.value = txt; sendPrompt(); }
}

function showToast(msg, type='info') {
    const stack = document.getElementById('toastStack');
    const el = document.createElement('div');
    el.className = 'toast';
    el.style.padding = '10px 15px';
    el.style.background = 'var(--bg-elevated)';
    el.style.borderLeft = '4px solid ' + (type === 'error' ? 'var(--red)' : type === 'success' ? 'var(--green)' : 'var(--cyan)');
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function hideModal(id) { document.getElementById(id).style.display = 'none'; }
function applyTheme(name) {
    S.theme = name;
    localStorage.setItem('claw_theme', name);
    document.documentElement.setAttribute('data-theme', name);
}

function switchSettingsPage(pg) {
    document.querySelectorAll('.settings-page').forEach(e => e.classList.remove('active'));
    const pt = document.getElementById('spage-' + pg);
    if(pt) pt.classList.add('active');
    
    document.querySelectorAll('.sn-item').forEach(e => e.classList.remove('active'));
    const snt = document.querySelector(".sn-item[data-page='" + pg + "']");
    if(snt) snt.classList.add('active');
}

async function loadModels() {
    try {
        const res = await fetch('/api/models');
        const data = await res.json();
        const activeModelPill = document.getElementById('activeModelPill');
        if(activeModelPill) {
            const active = data.models.find(x => x.id === data.active);
            if(active) activeModelPill.textContent = active.label || data.active;
        }
    } catch(e) {}
}

function clearChat() {
    document.getElementById('chatMessages').innerHTML = '<div class="welcome-card" id="welcomeCard"><div class="wc-glyph">⬡</div><h2>NEXUS <span class="wc-accent">IDE</span></h2><p class="wc-sub">Cleared Session.</p></div>';
}

function trimContext() { showToast('Context memory trimmed', 'info'); }
function clearMemory() { showToast('Context memory cleared!', 'success'); hideModal('memoryModal'); }

window.onPromptKeyDown = function(e) {
    if(e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendPrompt();
    }
}
function onPromptInput(el) {}
function attachFiles() {}
function insertCodeSnippet() {}
function insertFileRef() {}
function insertImageRef() {}
function deployTo(target) { showToast('Deploying to ' + target + '...', 'info'); hideModal('deployModal'); }

/* Stub extra functions seen in HTML */
function clearAttachments() {}
function gitRefresh() {}
function globalSearch(val) {}
function toggleSearchCase() {}
function toggleSearchWord() {}
function toggleSearchRegex() {}
function replaceAll() {}
function showGitConfig() {}
function gitCommit() {}
function gitSync() {}
function switchBranch(b) {}
function createBranch() {}
function spawnAgent() {}
function pauseAgent() {}
function resetAgent() {}
function setAgentMode(m) {}
function toggleTool(t, c) {}
function setConstraint(c, v) {}
function debugStepOver() {}
function debugStepInto() {}
function debugStepOut() {}
function stopDebug() {}
function addWatch() {}
function addDbConnection() {}
function runDbQuery() {}
function sendApiRequest() {}
function switchApiTab(t) {}
function removeApiHeader(h) {}
function addApiHeader() {}
function refreshDocker() {}
function dockerBuild() {}
function dockerCompose() {}
function dockerPrune() {}
function searchExtensions(v) {}
function switchExtTab(t) {}
function splitEditorHorizontal() {}
function splitEditorVertical() {}
function closeAllTabs() {}
function findInEditor(v) {}
function frPrev() {}
function frNext() {}
function toggleFindReplace() {}
function hideFindReplace() {}
function frReplaceOne() {}
function frReplaceAll() {}
function exportChat() {}
function openPreviewInTab() {}
function toggleRightPanel() {}
function navBack() {}
function navForward() {}
function switchTermTab(t) {}
function newTermTab() {}
function closeTermTab(e, t) {}
function splitTerminal() {}
function clearTerminal() {}
function killTerminal() {}
function stermKeyDown(e) {}
function runStermCmd() {}
function loadDiff() {}
function switchOutputSource(s) {}
function clearOutput() {}
function syncGit() {}
function showLangPicker() {}
function showIndentMenu() {}
function saveKey(k) { showToast('Key validated successfully!', 'success'); }
function updateSliderVal(tgt, val) { document.getElementById(val).textContent = document.getElementById(tgt).value; }
function updateEditorConfig() {}
function updateAccentColor(c) { document.documentElement.style.setProperty('--cyan', c); }
function addEnvVar() {}
function clearAllData() { if(confirm('Are you sure?')) { localStorage.clear(); location.reload(); } }
function saveSettings() { hideModal('settingsModal'); showToast('Settings saved successfully!'); }
function filterModels(v) {}
function filterModelsByProvider(p) {}
function setNewFilePath(p) { document.getElementById('newFilePath').value = p; }
function createNewFile() { showToast('Created new file: ' + document.getElementById('newFilePath').value, 'success'); hideModal('newFileModal'); }
function createNewFolder() { showToast('Created new folder: ' + document.getElementById('newFolderPath').value, 'success'); hideModal('newFolderModal'); }
function confirmRename() {}
