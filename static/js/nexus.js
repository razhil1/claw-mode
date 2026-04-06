'use strict';

/* ═══════════════════════════════════════════════════════════════════════════════
   NEXUS.JS — Core state, initialization, theme, layout, toasts, utilities
   ═══════════════════════════════════════════════════════════════════════════════ */

// ─── Global State ────────────────────────────────────────────────────────────
const NX = {
    sessionId: localStorage.getItem('nexus_session_id') || crypto.randomUUID?.() || Math.random().toString(36).slice(2),
    allFiles: [],
    openTabs: [],          // [{path, content, dirty, cm}]
    activeTab: null,       // index into openTabs
    currentFile: null,
    isAgentRunning: false,
    theme: localStorage.getItem('nexus_theme') || 'dark',
    ultraMode: localStorage.getItem('nexus_ultra') === '1',
    swarmMode: localStorage.getItem('nexus_swarm') === '1',
    activeModel: 'Loading…',
    activeModelId: '',
    agentMode: 'auto',
    chatHistory: [],       // messages for memory viewer
    toolLog: [],
    outputLog: [],
    tokenStats: { input: 0, output: 0, total: 0, cost: 0 },
    turnCount: 0,
    toolCallCount: 0,
    contextLimit: 128000,
    attachments: [],
    termHistory: [],
    termIdx: -1,
    termTabs: [{ id: 1, name: 'bash', output: [] }],
    activeTermTab: 1,
    termCounter: 1,
    constraints: {
        confirm_delete: true,
        confirm_run: true,
        sandbox_only: false,
        auto_save: true,
    },
    enabledTools: {
        file_read: true, file_write: true, file_patch: true,
        bash: true, web_search: true, web_fetch: true, npm: true,
        image_gen: false, db_query: false, git_ops: false,
    },
    envVars: [],
    searchOpts: { caseSensitive: false, wholeWord: false, regex: false },
    sidePanelVisible: true,
    rightPanelVisible: true,
    layouts: ['default', 'split', 'zen'],
    layoutIdx: 0,
    taskMode: localStorage.getItem('nexus_task_mode') || '',
};

if (!localStorage.getItem('nexus_session_id')) {
    localStorage.setItem('nexus_session_id', NX.sessionId);
}

// ─── Initialization ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(NX.theme);
    loadFiles();
    loadModels();
    initKeybindings();
    initTerminal();
    initResizeHandles();
    checkApiKey();
    loadSettings();

    // Auto-resize chat textarea
    const promptEl = document.getElementById('chatPrompt');
    if (promptEl) {
        promptEl.addEventListener('input', function () {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 180) + 'px';
            const cc = document.getElementById('charCount');
            if (cc) cc.textContent = this.value.length;
        });
    }

    // Restore ultra mode UI
    if (NX.ultraMode) {
        const badge = document.getElementById('ultraBadge');
        if (badge) badge.style.display = 'inline-flex';
        const toggle = document.getElementById('ultraToggle');
        if (toggle) toggle.checked = true;
        const sbUltra = document.getElementById('sbUltra');
        if (sbUltra) sbUltra.innerHTML = '<i class="fa-solid fa-bolt"></i> Ultra';
    }

    // Restore swarm mode UI
    if (NX.swarmMode) {
        const badge = document.getElementById('swarmBadge');
        if (badge) badge.style.display = 'inline-flex';
        const toggle = document.getElementById('swarmToggle');
        if (toggle) toggle.checked = true;
    }

    if (typeof refreshSystemStatus === 'function') {
        refreshSystemStatus();
        setInterval(refreshSystemStatus, 15000);
    }

    console.log('%c⬡ NEXUS IDE v2.0 — Ready', 'color:#00d4ff;font-size:14px;font-weight:bold;');
});

// ─── Theme System ────────────────────────────────────────────────────────────
function applyTheme(name) {
    NX.theme = name;
    localStorage.setItem('nexus_theme', name);
    document.documentElement.setAttribute('data-theme', name);

    // Update theme toggle icon
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        const icon = btn.querySelector('i');
        if (icon) icon.className = name === 'light' ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
    }

    // Update theme swatches
    document.querySelectorAll('.theme-swatch').forEach(s => {
        s.classList.toggle('active', s.dataset.themeName === name);
    });

    // Update CodeMirror theme
    if (typeof updateEditorTheme === 'function') updateEditorTheme(name);
}

function toggleTheme() {
    const themes = ['dark', 'light', 'cyberpunk', 'matrix', 'nord', 'solarized'];
    const idx = themes.indexOf(NX.theme);
    applyTheme(themes[(idx + 1) % themes.length]);
    showToast(`Theme: ${NX.theme}`, 'info');
}

function updateAccentColor(color) {
    document.documentElement.style.setProperty('--cyan', color);
    document.documentElement.style.setProperty('--accent', color);
    localStorage.setItem('nexus_accent', color);
}

// ─── Layout System ───────────────────────────────────────────────────────────
function toggleLayout(mode) {
    document.documentElement.setAttribute('data-layout', mode);
    NX.layoutIdx = NX.layouts.indexOf(mode);
    const layout = document.getElementById('nexusLayout');
    if (mode === 'zen') {
        document.getElementById('activityBar')?.classList.add('hidden');
        document.getElementById('sidePanel')?.classList.add('hidden');
        document.getElementById('statusBar')?.classList.add('hidden');
        if (layout) layout.style.gridTemplateColumns = '0 0 1fr 0';
    } else {
        document.getElementById('activityBar')?.classList.remove('hidden');
        document.getElementById('sidePanel')?.classList.remove('hidden');
        document.getElementById('statusBar')?.classList.remove('hidden');
        if (layout) layout.style.gridTemplateColumns = '';
    }
}

function cycleLayout() {
    NX.layoutIdx = (NX.layoutIdx + 1) % NX.layouts.length;
    toggleLayout(NX.layouts[NX.layoutIdx]);
    showToast(`Layout: ${NX.layouts[NX.layoutIdx]}`, 'info');
}

function toggleActivity() {
    const ab = document.getElementById('activityBar');
    const sp = document.getElementById('sidePanel');
    const layout = document.getElementById('nexusLayout');
    if (ab) ab.classList.toggle('hidden');
    if (sp) sp.classList.toggle('hidden');
    NX.sidePanelVisible = !NX.sidePanelVisible;
    if (layout) {
        if (NX.sidePanelVisible) {
            layout.style.gridTemplateColumns = '';
        } else {
            layout.style.gridTemplateColumns = `var(--activity-bar-w) var(--agent-panel-w) 1fr 0`;
        }
    }
}

function toggleRightPanel() {
    const rp = document.getElementById('rightPanel');
    const btn = document.getElementById('termToggleBtn');
    const layout = document.getElementById('nexusLayout');
    if (rp) {
        NX.rightPanelVisible = !NX.rightPanelVisible;
        if (NX.rightPanelVisible) {
            rp.classList.remove('collapsed');
            if (layout) layout.style.gridTemplateRows = '1fr var(--bottom-panel-h)';
        } else {
            rp.classList.add('collapsed');
            if (layout) layout.style.gridTemplateRows = '1fr 0';
        }
        if (btn) btn.classList.toggle('active', NX.rightPanelVisible);
        if (NX.rightPanelVisible && typeof fitTerminal === 'function') {
            setTimeout(fitTerminal, 100);
        }
    }
}

function toggleChatExpand() {
    const layout = document.getElementById('nexusLayout');
    if (!layout) return;
    const current = layout.style.gridTemplateColumns;
    if (current && current.includes('1fr 0')) {
        layout.style.gridTemplateColumns = '';
    } else {
        layout.style.gridTemplateColumns = `var(--activity-bar-w) 1fr 0 0`;
    }
}

// ─── Panel Navigation ────────────────────────────────────────────────────────
function togglePanel(name) {
    const sp = document.getElementById('sidePanel');
    const layout = document.getElementById('nexusLayout');
    const currentActive = document.querySelector('.sp-section.active');
    const target = document.getElementById('panel-' + name);

    if (currentActive === target && NX.sidePanelVisible) {
        if (sp) sp.classList.add('hidden');
        NX.sidePanelVisible = false;
        if (layout) layout.style.gridTemplateColumns = `var(--activity-bar-w) var(--agent-panel-w) 1fr 0`;
        document.querySelectorAll('.ab-btn').forEach(b => b.classList.remove('active'));
        return;
    }

    if (sp) sp.classList.remove('hidden');
    NX.sidePanelVisible = true;
    if (layout) layout.style.gridTemplateColumns = '';

    document.querySelectorAll('.sp-section').forEach(el => el.classList.remove('active'));
    if (target) target.classList.add('active');

    document.querySelectorAll('.ab-btn').forEach(btn => btn.classList.remove('active'));
    const btn = document.querySelector(`.ab-btn[data-panel='${name}']`);
    if (btn) btn.classList.add('active');
}

function switchRightTab(name) {
    document.querySelectorAll('.rp-tab').forEach(el => el.classList.remove('active'));
    const tabBtn = document.getElementById('rptab-' + name);
    if (tabBtn) tabBtn.classList.add('active');

    document.querySelectorAll('.rp-section').forEach(el => el.classList.remove('active'));
    const sec = document.getElementById('rpsec-' + name);
    if (sec) sec.classList.add('active');

    if (name === 'terminal') initTerminal();
}

// ─── Toast Notifications ─────────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 3500) {
    const stack = document.getElementById('toastStack');
    if (!stack) return;

    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    const icons = {
        info: 'fa-solid fa-circle-info',
        success: 'fa-solid fa-circle-check',
        warning: 'fa-solid fa-triangle-exclamation',
        error: 'fa-solid fa-circle-xmark',
    };
    el.innerHTML = `<i class="${icons[type] || icons.info}"></i><span>${msg}</span><button onclick="this.parentElement.remove()"><i class="fa-solid fa-xmark"></i></button>`;
    stack.appendChild(el);

    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 300);
    }, duration);
}

// ─── Modal System ────────────────────────────────────────────────────────────
function hideModal(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
}

function showModal(id) {
    const el = document.getElementById(id);
    if (el) {
        el.style.display = 'flex';
        const input = el.querySelector('input[autofocus], input:first-of-type');
        if (input) setTimeout(() => input.focus(), 100);
    }
}

// ─── Confirm Dialog ──────────────────────────────────────────────────────────
let _confirmResolve = null;
function showConfirm(title, message) {
    return new Promise(resolve => {
        _confirmResolve = resolve;
        document.getElementById('confirmTitle').textContent = title;
        document.getElementById('confirmMessage').textContent = message;
        showModal('confirmModal');
    });
}
function okConfirm() { if (_confirmResolve) _confirmResolve(true); _confirmResolve = null; hideModal('confirmModal'); }
function cancelConfirm() { if (_confirmResolve) _confirmResolve(false); _confirmResolve = null; hideModal('confirmModal'); }

// ─── Settings ────────────────────────────────────────────────────────────────
function showSettings() { showModal('settingsModal'); }
function showModelSelector() { showModal('modelSelectorModal'); renderModelCards(); }
function showKeyboardShortcuts() { showModal('shortcutsModal'); }
function showAbout() { showModal('aboutModal'); }
function showDeployPanel() { showModal('deployModal'); }
function showGuide() { if (typeof showGuideModal === 'function') showGuideModal(); else showToast('Guide loading...', 'info'); }
function showApiDocs() { if (typeof showGuideModal === 'function') showGuideModal(); else showToast('API docs loading...', 'info'); }
function showProfileMenu() { if (typeof showProfileModal === 'function') showProfileModal(); else showToast('Profile loading...', 'info'); }

function switchSettingsPage(pg) {
    document.querySelectorAll('.settings-page').forEach(e => e.classList.remove('active'));
    const pt = document.getElementById('spage-' + pg);
    if (pt) pt.classList.add('active');

    document.querySelectorAll('.sn-item').forEach(e => e.classList.remove('active'));
    const snt = document.querySelector(`.sn-item[data-page='${pg}']`);
    if (snt) snt.classList.add('active');

    if (pg === 'env' && typeof loadEnvVars === 'function') loadEnvVars();
}

function saveSettings() {
    // Persist settings to localStorage
    const settings = {
        maxTurns: document.getElementById('cfg-maxTurns')?.value,
        temp: document.getElementById('cfg-temp')?.value,
        maxTokens: document.getElementById('cfg-maxTokens')?.value,
        sysPrompt: document.getElementById('cfg-sysPrompt')?.value,
        autoRun: document.getElementById('cfg-autoRun')?.checked,
        streaming: document.getElementById('cfg-streaming')?.checked,
        ultra: document.getElementById('cfg-ultra')?.checked,
        fontSize: document.getElementById('cfg-fontSize')?.value,
        fontFamily: document.getElementById('cfg-fontFamily')?.value,
        tabSize: document.getElementById('cfg-tabSize')?.value,
        wordWrap: document.getElementById('cfg-wordWrap')?.checked,
        lineNums: document.getElementById('cfg-lineNums')?.checked,
        minimap: document.getElementById('cfg-minimap')?.checked,
        autoSave: document.getElementById('cfg-autoSave')?.value,
        formatOnSave: document.getElementById('cfg-formatOnSave')?.checked,
        baseUrl: document.getElementById('cfg-baseUrl')?.value,
        timeout: document.getElementById('cfg-timeout')?.value,
        verbose: document.getElementById('cfg-verbose')?.checked,
    };
    localStorage.setItem('nexus_settings', JSON.stringify(settings));
    if (typeof updateEditorConfig === 'function') updateEditorConfig();
    hideModal('settingsModal');
    showToast('Settings saved successfully!', 'success');
}

function loadSettings() {
    try {
        const s = JSON.parse(localStorage.getItem('nexus_settings') || '{}');
        if (s.maxTurns) setVal('cfg-maxTurns', s.maxTurns);
        if (s.temp) { setVal('cfg-temp', s.temp); setVal('cfg-temp-val', s.temp); }
        if (s.maxTokens) setVal('cfg-maxTokens', s.maxTokens);
        if (s.sysPrompt) setVal('cfg-sysPrompt', s.sysPrompt);
        if (s.fontSize) setVal('cfg-fontSize', s.fontSize);
        if (s.baseUrl) setVal('cfg-baseUrl', s.baseUrl);
        if (s.timeout) setVal('cfg-timeout', s.timeout);
        setChecked('cfg-autoRun', s.autoRun !== false);
        setChecked('cfg-streaming', s.streaming !== false);
        setChecked('cfg-ultra', !!s.ultra);
        setChecked('cfg-wordWrap', !!s.wordWrap);
        setChecked('cfg-lineNums', s.lineNums !== false);
        setChecked('cfg-verbose', !!s.verbose);

        const accent = localStorage.getItem('nexus_accent');
        if (accent) {
            document.documentElement.style.setProperty('--cyan', accent);
            setVal('cfg-accentColor', accent);
        }
    } catch (e) {}
}

function setVal(id, val) { const el = document.getElementById(id); if (el) el.value = val; }
function setChecked(id, val) { const el = document.getElementById(id); if (el) el.checked = val; }
function updateSliderVal(tgt, valId) {
    const el = document.getElementById(valId);
    const src = document.getElementById(tgt);
    if (el && src) el.textContent = src.value;
}

function clearAllData() {
    showConfirm('Clear All Data', 'This will reset all settings, sessions, and local data. Are you sure?').then(ok => {
        if (ok) { localStorage.clear(); location.reload(); }
    });
}

function dismissKeyWarning() {
    localStorage.setItem('nexus_hide_key_warn', '1');
    const warn = document.getElementById('wcKeyWarning');
    if (warn) warn.style.display = 'none';
}

async function checkApiKey() {
    if (localStorage.getItem('nexus_hide_key_warn') === '1') return;
    try {
        const res = await fetch('/api/settings/key-status');
        const data = await res.json();
        const warn = document.getElementById('wcKeyWarning');
        if (warn && !data.nvidia?.configured) {
            warn.style.display = 'flex';
        }
    } catch (e) {}
}

// ─── Resize Handles ──────────────────────────────────────────────────────────
function initResizeHandles() {
    _setupGridResize();
}

function _setupGridResize() {
    const layout = document.getElementById('nexusLayout');
    if (!layout) return;

    const chatRegion = document.getElementById('chatRegion');
    const sidePanel = document.getElementById('sidePanel');

    function getEdgePositions() {
        const layoutRect = layout.getBoundingClientRect();
        const edges = [];
        if (chatRegion) {
            const cr = chatRegion.getBoundingClientRect();
            edges.push({
                x: cr.right - layoutRect.left,
                cursor: 'col-resize',
                min: 260, max: 550,
                variable: '--agent-panel-w',
                direction: 1
            });
        }
        if (sidePanel && !sidePanel.classList.contains('hidden')) {
            const sp = sidePanel.getBoundingClientRect();
            edges.push({
                x: sp.left - layoutRect.left,
                cursor: 'col-resize',
                min: 180, max: 400,
                variable: '--side-panel-w',
                direction: -1
            });
        }
        return edges;
    }

    layout.addEventListener('mousemove', e => {
        if (layout._resizing) return;
        const rect = layout.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const edges = getEdgePositions();
        let cursor = '';
        for (const edge of edges) {
            if (Math.abs(x - edge.x) < 5) { cursor = edge.cursor; break; }
        }
        layout.style.cursor = cursor;
    });

    layout.addEventListener('mousedown', e => {
        const rect = layout.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const edges = getEdgePositions();
        let hitEdge = null;
        for (const edge of edges) {
            if (Math.abs(x - edge.x) < 5) { hitEdge = edge; break; }
        }
        if (!hitEdge) return;
        e.preventDefault();
        layout._resizing = true;
        document.body.classList.add('resizing');
        const startX = e.clientX;
        const startVal = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(hitEdge.variable));

        const onMove = ev => {
            let delta = (ev.clientX - startX) * hitEdge.direction;
            let newVal = startVal + delta;
            if (newVal < hitEdge.min) newVal = hitEdge.min;
            if (newVal > hitEdge.max) newVal = hitEdge.max;
            document.documentElement.style.setProperty(hitEdge.variable, newVal + 'px');
        };
        const onUp = () => {
            layout._resizing = false;
            document.body.classList.remove('resizing');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}

function _setupResize(handleId, targetId, dir, min, max) {
    const handle = document.getElementById(handleId);
    const target = document.getElementById(targetId);
    if (!handle || !target) return;

    let startPos, startSize;
    handle.addEventListener('mousedown', e => {
        e.preventDefault();
        document.body.classList.add('resizing');
        if (dir === 'horizontal') {
            startPos = e.clientX;
            startSize = target.offsetWidth;
        } else if (dir === 'horizontal-right') {
            startPos = e.clientX;
            startSize = target.offsetWidth;
        } else {
            startPos = e.clientY;
            startSize = target.offsetHeight;
        }

        const onMove = ev => {
            let newSize;
            if (dir === 'horizontal') {
                newSize = startSize + (ev.clientX - startPos);
            } else if (dir === 'horizontal-right') {
                newSize = startSize - (ev.clientX - startPos);
            } else {
                newSize = startSize + (ev.clientY - startPos);
            }
            if (min && newSize < min) newSize = min;
            if (max && newSize > max) newSize = max;
            if (dir === 'vertical') {
                target.style.flex = `0 0 ${newSize}px`;
            } else {
                target.style.width = newSize + 'px';
                target.style.flex = 'none';
            }
        };
        const onUp = () => {
            document.body.classList.remove('resizing');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}

// ─── Deploy ──────────────────────────────────────────────────────────────────
async function deployTo(target) {
    const log = document.getElementById('deployLog');
    const _log = (html, cls='') => {
        if (log) log.innerHTML += `<div class="deploy-step ${cls}">${html}</div>`;
    };

    if (log) log.innerHTML = '';
    _log(`<i class="fa-solid fa-spinner fa-spin"></i> Starting deployment to <strong>${target}</strong>…`);
    showToast(`Deploying to ${target}…`, 'info');

    try {
        let endpoint, body = {};

        if (target === 'git') {
            endpoint = '/api/deploy/push';
            const msg = prompt('Commit message (optional):');
            if (msg) body.commit_message = msg;
        } else if (target === 'netlify') {
            endpoint = '/api/deploy/netlify';
        } else if (target === 'vercel') {
            endpoint = '/api/deploy/vercel';
        } else {
            _log(`<i class="fa-solid fa-times-circle"></i> Unknown deploy target: ${escapeHtml(target)}`, 'error');
            return;
        }

        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();

        if (data.ok) {
            _log(`<i class="fa-solid fa-check"></i> Deployment succeeded`, 'success');
            if (data.output) {
                _log(`<pre style="font-size:11px;white-space:pre-wrap;margin:6px 0 0;">${escapeHtml(data.output.slice(0, 2000))}</pre>`);
            }
            showToast(`Deployed to ${target} ✓`, 'success');
        } else {
            const errMsg = data.message || data.error || 'Deployment failed';
            _log(`<i class="fa-solid fa-times-circle"></i> ${escapeHtml(errMsg)}`, 'error');
            showToast(`Deploy failed: ${escapeHtml(errMsg)}`, 'error');
        }
    } catch (err) {
        _log(`<i class="fa-solid fa-times-circle"></i> Network error: ${escapeHtml(err.message)}`, 'error');
        showToast('Deploy request failed', 'error');
    }
}

function showNotifications() {
    showToast('No new notifications', 'info');
    const badge = document.getElementById('notifBadge');
    if (badge) badge.style.display = 'none';
}

// ─── Utility ─────────────────────────────────────────────────────────────────
function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
