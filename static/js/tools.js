'use strict';
/* TOOLS.JS — Tool log, global search, diff viewer, output console, API tester, extensions, docker, database */

// ─── Tool Log ────────────────────────────────────────────────────────────────
function logToolCall(evt) {
    NX.toolLog.push({
        time: Date.now(),
        tool: evt.tool || 'unknown',
        type: evt.type || 'tool_call',
        summary: evt.summary || '',
        result: evt.result || '',
    });
    renderToolLog();
    updateToolBadge();
}

function renderToolLog(filter = 'all') {
    const container = document.getElementById('toolLogEntries');
    if (!container) return;
    let logs = NX.toolLog;
    if (filter !== 'all') logs = logs.filter(l => {
        if (filter === 'tool_call') return l.type === 'tool_call';
        if (filter === 'file_op') return ['file_read','file_write','file_patch','ReadFile','WriteFile','PatchFile'].includes(l.tool);
        if (filter === 'bash') return ['bash','BashExec'].includes(l.tool);
        if (filter === 'error') return l.type === 'error';
        return true;
    });

    if (!logs.length) { container.innerHTML = '<div class="log-empty">No tool calls yet</div>'; return; }

    container.innerHTML = logs.slice(-50).reverse().map(l => {
        const time = new Date(l.time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const iconMap = { BashExec:'fa-solid fa-terminal', ReadFile:'fa-solid fa-file-lines', WriteFile:'fa-solid fa-file-pen', PatchFile:'fa-solid fa-scissors', WebSearch:'fa-solid fa-globe', WebFetch:'fa-solid fa-cloud-arrow-down' };
        const icon = iconMap[l.tool] || 'fa-solid fa-wrench';
        return `<div class="log-entry log-${l.type}">
            <div class="log-header"><i class="${icon}"></i><span class="log-tool">${l.tool}</span><span class="log-time">${time}</span></div>
            ${l.summary ? `<div class="log-summary">${escapeHtml(l.summary)}</div>` : ''}
            ${l.result ? `<div class="log-result"><pre>${escapeHtml(l.result.slice(0,500))}</pre></div>` : ''}
        </div>`;
    }).join('');
}

function filterLogs(type) {
    document.querySelectorAll('.log-filter').forEach(b => b.classList.remove('active'));
    event?.target?.classList?.add('active');
    renderToolLog(type);
}

function clearToolLog() { NX.toolLog = []; renderToolLog(); showToast('Tool log cleared', 'info'); }
function exportToolLog() {
    const content = NX.toolLog.map(l => `[${new Date(l.time).toISOString()}] ${l.tool}: ${l.summary}\n${l.result || ''}`).join('\n---\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'nexus-tool-log.txt'; a.click();
    showToast('Tool log exported', 'success');
}

function showToolLog() { switchRightTab('logs'); }
function updateToolBadge() { const badge = document.getElementById('rptab-terminal-badge'); if (badge && NX.toolLog.length) { badge.textContent = NX.toolLog.length; badge.style.display = ''; } }

// ─── Global Search ───────────────────────────────────────────────────────────
let _searchDebounce = null;
function globalSearch(val) {
    clearTimeout(_searchDebounce);
    if (!val || val.length < 2) { document.getElementById('gsResults').innerHTML = '<div class="gs-hint">Type to search across workspace files</div>'; return; }
    _searchDebounce = setTimeout(async () => {
        try {
            const res = await fetch('/api/files/search?q=' + encodeURIComponent(val));
            const data = await res.json();
            renderSearchResults(data.results || []);
        } catch { document.getElementById('gsResults').innerHTML = '<div class="gs-hint">Search error</div>'; }
    }, 300);
}

function renderSearchResults(results) {
    const container = document.getElementById('gsResults');
    if (!results.length) { container.innerHTML = '<div class="gs-hint">No matches found</div>'; return; }
    container.innerHTML = results.map(r => `
        <div class="gs-file-group">
            <div class="gs-file-header" onclick="openFileInEditor('${r.path}')"><i class="${fileIcon(r.path.split('/').pop())}"></i> ${r.path} <span class="gs-match-count">${r.matches.length}</span></div>
            ${r.matches.slice(0, 5).map(m => `<div class="gs-match-line" onclick="openFileInEditor('${r.path}')"><span class="gs-line-num">${m.line}</span><span class="gs-line-text">${escapeHtml(m.text)}</span></div>`).join('')}
            ${r.matches.length > 5 ? `<div class="gs-more">+${r.matches.length - 5} more matches</div>` : ''}
        </div>
    `).join('');
}

function showGlobalSearch() { togglePanel('search'); document.getElementById('globalSearchInput')?.focus(); }
function toggleSearchCase() { NX.searchOpts.caseSensitive = !NX.searchOpts.caseSensitive; document.getElementById('gsCase')?.classList.toggle('active'); }
function toggleSearchWord() { NX.searchOpts.wholeWord = !NX.searchOpts.wholeWord; document.getElementById('gsWord')?.classList.toggle('active'); }
function toggleSearchRegex() { NX.searchOpts.regex = !NX.searchOpts.regex; document.getElementById('gsRegex')?.classList.toggle('active'); }
function replaceAll() { showToast('Replace All: coming soon', 'info'); }

// ─── Diff Viewer ─────────────────────────────────────────────────────────────
async function loadDiff() {
    const pathA = document.getElementById('diffFileA')?.value;
    const pathB = document.getElementById('diffFileB')?.value;
    const container = document.getElementById('diffContent');
    if (!pathA || !pathB || pathA.startsWith('Select') || pathB.startsWith('Select')) { container.innerHTML = '<div class="diff-empty">Select two files to compare</div>'; return; }

    try {
        const res = await fetch('/api/files/batch', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({paths:[pathA, pathB]}) });
        const data = await res.json();
        const contentA = data.files?.[pathA]?.content || '';
        const contentB = data.files?.[pathB]?.content || '';
        renderDiff(contentA, contentB, container);
    } catch { container.innerHTML = '<div class="diff-empty">Error loading files</div>'; }
}

function renderDiff(textA, textB, container) {
    const linesA = textA.split('\n'), linesB = textB.split('\n');
    const maxLen = Math.max(linesA.length, linesB.length);
    let html = '<div class="diff-table">';
    for (let i = 0; i < maxLen; i++) {
        const la = linesA[i] ?? '', lb = linesB[i] ?? '';
        const cls = la !== lb ? (la && !lb ? 'diff-removed' : !la && lb ? 'diff-added' : 'diff-changed') : '';
        html += `<div class="diff-row ${cls}"><span class="diff-num">${i+1}</span><span class="diff-line-a">${escapeHtml(la)}</span><span class="diff-line-b">${escapeHtml(lb)}</span></div>`;
    }
    html += '</div>';
    container.innerHTML = html;
}

// ─── Output Console ──────────────────────────────────────────────────────────
function appendOutputLine(text, source = 'app') {
    NX.outputLog.push({ text, source, time: Date.now() });
    const container = document.getElementById('outputLines');
    if (!container) return;
    container.innerHTML = container.innerHTML.replace('<div class="output-empty">No output yet. Run your project with F5</div>', '');
    const div = document.createElement('div');
    div.className = 'output-line output-' + source;
    div.innerHTML = `<span class="output-time">${new Date().toLocaleTimeString()}</span> <pre>${escapeHtml(text)}</pre>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function clearOutput() { document.getElementById('outputLines').innerHTML = '<div class="output-empty">Output cleared</div>'; NX.outputLog = []; }
function switchOutputSource(source) {
    const container = document.getElementById('outputLines');
    if (source === 'all') { container.querySelectorAll('.output-line').forEach(l => l.style.display = ''); return; }
    container.querySelectorAll('.output-line').forEach(l => { l.style.display = l.classList.contains('output-'+source) ? '' : 'none'; });
}

// ─── Preview ─────────────────────────────────────────────────────────────────
function refreshPreview() {
    const frame = document.getElementById('previewFrame');
    const urlInput = document.getElementById('browserUrlInput');
    let url = urlInput?.value;
    if (!url) {
        if (NX.currentFile && NX.currentFile.endsWith('.html')) {
            url = '/workspace/' + NX.currentFile;
        } else {
            url = '/workspace/index.html';
        }
        if (urlInput) urlInput.value = url;
    }
    if (frame) { frame.src = url; }
    const emptyState = document.getElementById('previewEmptyState');
    if (emptyState) emptyState.style.display = 'none';
}

function loadPreviewUrl(url) { document.getElementById('browserUrlInput').value = url; refreshPreview(); }
function openPreviewInTab() { const url = document.getElementById('browserUrlInput')?.value; if (url) window.open(url, '_blank'); }
function navBack() { const f = document.getElementById('previewFrame'); if (f?.contentWindow) try { f.contentWindow.history.back(); } catch {} }
function navForward() { const f = document.getElementById('previewFrame'); if (f?.contentWindow) try { f.contentWindow.history.forward(); } catch {} }
function toggleDeviceView(type) { const w = document.getElementById('previewDeviceWrap'); if (w) w.className = 'preview-device-wrap ' + type; }

// ─── API Tester ──────────────────────────────────────────────────────────────
async function sendApiRequest() {
    const method = document.getElementById('apiMethod')?.value || 'GET';
    const url = document.getElementById('apiUrl')?.value?.trim();
    if (!url) { showToast('Enter a URL', 'warning'); return; }

    const respDiv = document.getElementById('apiResponse');
    respDiv.innerHTML = '<div class="api-loading"><i class="fa-solid fa-spinner fa-spin"></i> Sending...</div>';

    try {
        const opts = { method };
        const startTime = performance.now();
        const res = await fetch(url, opts);
        const elapsed = Math.round(performance.now() - startTime);
        const text = await res.text();

        let formatted = text;
        try { formatted = JSON.stringify(JSON.parse(text), null, 2); } catch {}

        respDiv.innerHTML = `
            <div class="api-resp-header">
                <span class="api-status api-status-${Math.floor(res.status/100)}xx">${res.status} ${res.statusText}</span>
                <span class="api-time">${elapsed}ms</span>
            </div>
            <pre class="api-resp-body">${escapeHtml(formatted)}</pre>`;
    } catch (e) {
        respDiv.innerHTML = `<div class="api-resp-error"><i class="fa-solid fa-circle-xmark"></i> ${escapeHtml(e.message)}</div>`;
    }
}

function switchApiTab(tab) {
    document.querySelectorAll('.api-tab').forEach(t => t.classList.remove('active'));
    event?.target?.classList?.add('active');
}
function removeApiHeader(btn) { btn.closest('.api-header-row')?.remove(); }
function addApiHeader() {
    const list = document.getElementById('apiHeadersList');
    if (!list) return;
    const row = document.createElement('div');
    row.className = 'api-header-row';
    row.innerHTML = '<input type="text" placeholder="Key" /><input type="text" placeholder="Value" /><button onclick="removeApiHeader(this)"><i class="fa-solid fa-xmark"></i></button>';
    list.appendChild(row);
}

// ─── Docker ──────────────────────────────────────────────────────────────────
function refreshDocker() { showToast('Docker: checking daemon...', 'info'); document.getElementById('dockerDaemon').innerHTML = '<i class="fa-solid fa-circle text-amber"></i> Daemon: Not connected'; }
function dockerBuild() { showToast('Docker build: coming soon', 'info'); }
function dockerCompose() { showToast('Docker compose up: coming soon', 'info'); }
function dockerPrune() { showToast('Docker prune: coming soon', 'info'); }

// ─── Database ────────────────────────────────────────────────────────────────
function addDbConnection() { showToast('Database connections: coming soon', 'info'); }
async function runDbQuery() {
    const query = document.getElementById('dbQueryInput')?.value?.trim();
    if (!query) { showToast('Enter a query', 'warning'); return; }
    const results = document.getElementById('dbResults');
    if (results) results.innerHTML = '<div class="db-result-info">Query execution: requires database connection setup</div>';
    showToast('Database query execution coming soon', 'info');
}

// ─── Extensions ──────────────────────────────────────────────────────────────
const _builtinExtensions = [
    { name: 'Python Support', desc: 'Syntax highlighting, linting', icon: 'fa-brands fa-python', installed: true },
    { name: 'JavaScript ES6+', desc: 'Modern JS/TS support', icon: 'fa-brands fa-js', installed: true },
    { name: 'Git Integration', desc: 'Source control', icon: 'fa-brands fa-git-alt', installed: true },
    { name: 'Docker Tools', desc: 'Container management', icon: 'fa-brands fa-docker', installed: false },
    { name: 'AI Autocomplete', desc: 'AI-powered suggestions', icon: 'fa-solid fa-wand-magic-sparkles', installed: false },
    { name: 'Theme Pack', desc: 'Additional visual themes', icon: 'fa-solid fa-palette', installed: false },
];

function renderExtensions(tab = 'installed') {
    const list = document.getElementById('extList');
    if (!list) return;
    const exts = tab === 'installed' ? _builtinExtensions.filter(e => e.installed)
        : tab === 'recommended' ? _builtinExtensions.filter(e => !e.installed)
        : _builtinExtensions;

    list.innerHTML = exts.map(e => `
        <div class="ext-item">
            <i class="${e.icon} ext-icon"></i>
            <div class="ext-info"><div class="ext-name">${e.name}</div><div class="ext-desc">${e.desc}</div></div>
            <button class="ext-action" onclick="toggleExtension('${e.name}')">${e.installed ? 'Disable' : 'Install'}</button>
        </div>
    `).join('');
}

function searchExtensions(val) { renderExtensions('all'); }
function switchExtTab(tab) {
    document.querySelectorAll('.ext-tab').forEach(t => t.classList.remove('active'));
    event?.target?.classList?.add('active');
    renderExtensions(tab);
}
function toggleExtension(name) { showToast(`Extension: ${name} toggled`, 'info'); }

// Init extensions on load
document.addEventListener('DOMContentLoaded', () => setTimeout(renderExtensions, 500));
