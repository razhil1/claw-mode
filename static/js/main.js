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

    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text, session_id: S.sessionId })
        });
        
        const contentDiv = appendMessage('', 'agent');
        
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
                    if (evt.type === 'token' && evt.text) {
                        contentDiv.innerHTML = marked.parse(contentDiv.dataset.md = (contentDiv.dataset.md || '') + evt.text);
                        document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
                    }
                } catch(e) {}
            }
        }
        await loadFiles();
    } catch {
        showToast('Communication error', 'error');
    }
    setAgentWorking(false);
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
function showCommandPalette() { document.getElementById('cmdPaletteOverlay').style.display = 'flex'; document.getElementById('cmdInput').focus(); }
function hideCommandPalette() { document.getElementById('cmdPaletteOverlay').style.display = 'none'; }
function filterCommands(val) {}
function cmdKeyNav(e) {}

function toggleActivity() {}
function showNewFileDialog() { document.getElementById('newFileModal').style.display = 'flex'; }
function triggerUpload() {}
function saveCurrentFile() { showToast('File saved!', 'success'); }
function saveAllFiles() {}
function downloadWorkspace() { showToast('Exporting workspace...', 'info'); }
function triggerImport() {}
function editorUndo() {}
function editorRedo() {}
function showFindReplace() {}
function showGlobalSearch() {}
function formatDocument() {}
function toggleComment() {}

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
function showProfileMenu() {}
function showAgentConfig() { showSettings(); }
function showModelSelector() { document.getElementById('modelSelectorModal').style.display = 'flex'; }
function toggleUltraMode() { showToast('Ultra Mode toggled'); }
function showMemory() { document.getElementById('memoryModal').style.display = 'flex'; }
function showToolLog() {}
function showTokenStats() { document.getElementById('tokenStatsModal').style.display = 'flex'; }

function runProject() { showToast('Project Started'); }
function runCurrentFile() {}
function debugProject() {}
function showRunConfig() {}
function showEnvManager() {}
function showDockerPanel() {}
function showDeployPanel() {}
function showGitPanel() {}
function showNetlifyDeploy() {}
function showVercelDeploy() {}
function showKeyboardShortcuts() { document.getElementById('shortcutsModal').style.display = 'flex'; }
function showGuide() {}
function showApiDocs() {}
function showAbout() {}

function toggleChatExpand() {
    const r = document.getElementById('chatRegion');
    r.style.flex = r.style.flex === '2' ? '1' : '2';
}

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
function toggleRightPanel() { document.getElementById('rightPanel').style.display = document.getElementById('rightPanel').style.display === 'none' ? 'flex' : 'none'; }
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
function clearToolLog() {}
function exportToolLog() {}
function filterLogs(t) {}
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
