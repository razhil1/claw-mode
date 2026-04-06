'use strict';
/* TOOLS.JS — Tool log, global search, diff viewer, output console, API tester, docker, database */

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
function replaceAll() {
    const searchVal = document.getElementById('globalSearchInput')?.value;
    const replaceVal = document.getElementById('globalReplaceInput')?.value;
    if (!searchVal) { showToast('Enter search text first', 'warn'); return; }
    if (_cmEditor) {
        const doc = _cmEditor.getValue();
        const flags = NX.searchOpts?.caseSensitive ? 'g' : 'gi';
        let pattern;
        try {
            pattern = NX.searchOpts?.regex ? new RegExp(searchVal, flags) : new RegExp(searchVal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags);
        } catch(e) { showToast('Invalid regex', 'error'); return; }
        const count = (doc.match(pattern) || []).length;
        if (count === 0) { showToast('No matches found', 'info'); return; }
        const newDoc = doc.replace(pattern, replaceVal || '');
        _cmEditor.setValue(newDoc);
        showToast(`Replaced ${count} occurrence(s)`, 'success');
    } else {
        showToast('No editor open', 'warning');
    }
}

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
async function refreshDocker() {
    const daemonEl = document.getElementById('dockerDaemon');
    const imagesEl = document.getElementById('dockerImages');
    const containersEl = document.getElementById('dockerContainers');

    if (daemonEl) daemonEl.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Checking daemon…';

    try {
        const res = await fetch('/api/docker/status');
        const data = await res.json();

        if (!data.available) {
            if (daemonEl) daemonEl.innerHTML = '<i class="fa-solid fa-circle text-red"></i> Docker: Not available';
            if (imagesEl) imagesEl.innerHTML = '<div class="docker-empty">Docker daemon not running or not installed in this environment.</div>';
            if (containersEl) containersEl.innerHTML = '';
            return;
        }

        if (daemonEl) daemonEl.innerHTML = `<i class="fa-solid fa-circle text-green"></i> ${escapeHtml(data.daemon || 'Docker')}`;

        if (imagesEl) {
            if (!data.images || !data.images.length) {
                imagesEl.innerHTML = '<div class="docker-empty">No images found</div>';
            } else {
                imagesEl.innerHTML = data.images.map(img => `
                    <div class="docker-item">
                        <i class="fa-brands fa-docker"></i>
                        <div class="docker-item-info">
                            <span class="docker-item-name">${escapeHtml(img.name)}</span>
                            <span class="docker-item-meta">${escapeHtml(img.size)} · ${escapeHtml(img.created)}</span>
                        </div>
                    </div>`).join('');
            }
        }

        if (containersEl) {
            if (!data.containers || !data.containers.length) {
                containersEl.innerHTML = '<div class="docker-empty">No containers found</div>';
            } else {
                containersEl.innerHTML = data.containers.map(c => {
                    const isRunning = (c.status || '').toLowerCase().includes('up');
                    const statusColor = isRunning ? 'text-green' : 'text-red';
                    return `<div class="docker-item">
                        <i class="fa-solid fa-cube ${statusColor}"></i>
                        <div class="docker-item-info">
                            <span class="docker-item-name">${escapeHtml(c.name)}</span>
                            <span class="docker-item-meta">${escapeHtml(c.image)} · ${escapeHtml(c.status)}</span>
                            ${c.ports ? `<span class="docker-item-ports">${escapeHtml(c.ports)}</span>` : ''}
                        </div>
                        ${isRunning ? `<button class="docker-stop-btn" style="margin-right:4px;" onclick="dockerExecContainer('${escapeHtml(c.name)}')"><i class="fa-solid fa-terminal"></i></button><button class="docker-stop-btn" onclick="dockerStopContainer('${escapeHtml(c.name)}')"><i class="fa-solid fa-stop"></i></button>` : ''}
                    </div>`;
                }).join('');
            }
        }
    } catch (e) {
        if (daemonEl) daemonEl.innerHTML = '<i class="fa-solid fa-circle text-red"></i> Error connecting';
        showToast('Docker status check failed', 'error');
    }
}

async function dockerBuild() {
    const tag = prompt('Image tag (e.g. myapp:latest):', 'nexus-app:latest');
    if (!tag) return;
    showToast('Building Docker image…', 'info');
    try {
        const res = await fetch('/api/docker/build', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag })
        });
        const data = await res.json();
        if (data.ok) {
            showToast(`Built ${tag} successfully`, 'success');
            appendOutputLine(data.output || '', 'docker');
        } else {
            showToast(data.message || 'Build failed', 'error');
            appendOutputLine(data.message || data.output || '', 'error');
        }
        refreshDocker();
    } catch (e) { showToast('Docker build error: ' + e.message, 'error'); }
}

async function dockerCompose() {
    showToast('Running docker compose up -d…', 'info');
    try {
        const res = await fetch('/api/docker/compose', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        const data = await res.json();
        if (data.ok) {
            showToast('Compose up succeeded', 'success');
            appendOutputLine(data.output || '', 'docker');
        } else {
            showToast(data.message || 'Compose up failed', 'error');
            appendOutputLine(data.message || '', 'error');
        }
        refreshDocker();
    } catch (e) { showToast('Docker compose error: ' + e.message, 'error'); }
}

async function dockerPrune() {
    if (!confirm('Remove all stopped containers and dangling images?')) return;
    showToast('Pruning Docker resources…', 'info');
    try {
        const res = await fetch('/api/docker/prune', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        const data = await res.json();
        showToast(data.ok ? 'Prune complete' : (data.message || 'Prune failed'), data.ok ? 'success' : 'error');
        if (data.output) appendOutputLine(data.output, 'docker');
        refreshDocker();
    } catch (e) { showToast('Docker prune error: ' + e.message, 'error'); }
}

async function dockerStopContainer(name) {
    if (!confirm(`Stop container "${name}"?`)) return;
    try {
        const res = await fetch('/api/docker/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        const data = await res.json();
        showToast(data.ok ? `Stopped ${name}` : (data.message || 'Stop failed'), data.ok ? 'success' : 'error');
        refreshDocker();
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function dockerExecContainer(name) {
    const cmd = prompt(`Run command in container "${name}":`, 'ps aux');
    if (!cmd) return;
    showToast(`Running in ${name}…`, 'info');
    try {
        const res = await fetch('/api/docker/exec', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, cmd })
        });
        const data = await res.json();
        if (data.ok) {
            showToast(`exec completed`, 'success');
            appendOutputLine(data.output || '', 'docker');
        } else {
            showToast(data.error || data.message || 'exec failed', 'error');
            appendOutputLine(data.error || '', 'error');
        }
    } catch (e) { showToast('Docker exec error: ' + e.message, 'error'); }
}

// Auto-refresh Docker when panel opens
document.addEventListener('DOMContentLoaded', () => {
    const dockerBtn = document.querySelector('[data-panel="docker"]');
    if (dockerBtn) dockerBtn.addEventListener('click', () => setTimeout(refreshDocker, 100));
});

// ─── Database ────────────────────────────────────────────────────────────────
const _dbState = { activeConnId: null };

async function addDbConnection() {
    const dbType = prompt('Database type (sqlite / postgres):', 'sqlite');
    if (!dbType) return;

    let target = '';
    if (dbType.toLowerCase() === 'sqlite') {
        target = prompt('SQLite file path (relative to workspace, e.g. data.db):', 'data.db');
    } else {
        target = prompt('PostgreSQL connection URL:', 'postgresql://user:pass@localhost/dbname');
    }
    if (!target) return;

    try {
        const res = await fetch('/api/db/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: dbType, path: target, url: target })
        });
        const data = await res.json();
        if (data.ok) {
            _dbState.activeConnId = data.conn_id;
            showToast(`Connected to ${target}`, 'success');
            await refreshDbConnections();
            await loadDbTables();
        } else {
            showToast(data.message || 'Connection failed', 'error');
        }
    } catch (e) { showToast('DB connect error: ' + e.message, 'error'); }
}

async function refreshDbConnections() {
    try {
        const res = await fetch('/api/db/connections');
        const data = await res.json();
        const el = document.getElementById('dbConnections');
        if (!el) return;
        if (!data.connections || !data.connections.length) {
            el.innerHTML = '<div class="db-empty">No connections. <button onclick="addDbConnection()">Add one</button></div>';
            return;
        }
        el.innerHTML = data.connections.map(c => {
            const isActive = c.conn_id === _dbState.activeConnId;
            return `<div class="db-conn-item ${isActive ? 'active' : ''}" onclick="selectDbConnection('${c.conn_id}', '${escapeHtml(c.name)}')">
                <i class="fa-solid fa-database"></i>
                <div class="db-conn-info">
                    <span class="db-conn-name">${escapeHtml(c.name)}</span>
                    <span class="db-conn-type">${escapeHtml(c.type)}</span>
                </div>
                ${isActive ? '<i class="fa-solid fa-check text-green"></i>' : ''}
            </div>`;
        }).join('');
    } catch (e) { console.error('DB connections error:', e); }
}

async function selectDbConnection(connId, name) {
    _dbState.activeConnId = connId;
    showToast(`Active: ${name}`, 'info');
    await refreshDbConnections();
    await loadDbTables();
}

async function loadDbTables() {
    const conn_id = _dbState.activeConnId || '';
    try {
        const url = conn_id ? `/api/db/tables?conn_id=${conn_id}` : '/api/db/tables';
        const res = await fetch(url);
        const data = await res.json();
        const el = document.getElementById('dbResults');
        if (!el) return;

        if (data.sqlite_files && !_dbState.activeConnId) {
            el.innerHTML = `<div class="db-result-info">
                <strong>SQLite databases in workspace:</strong><br>
                ${data.sqlite_files.map(f => `<span class="db-file-link" onclick="quickConnectSqlite('${f}')">${f}</span>`).join('<br>')}
                ${!data.sqlite_files.length ? 'None found.' : ''}
            </div>`;
            return;
        }

        if (!data.tables || !data.tables.length) {
            el.innerHTML = '<div class="db-result-info">No tables found in this database.</div>';
            return;
        }
        el.innerHTML = `<div class="db-tables-list">
            <div class="db-result-label">Tables (${data.tables.length})</div>
            ${data.tables.map(t => `<div class="db-table-item" onclick="previewTable('${escapeHtml(t.name)}')">
                <i class="fa-solid fa-table"></i> ${escapeHtml(t.name)}
                <span class="db-table-type">${escapeHtml(t.type || '')}</span>
            </div>`).join('')}
        </div>`;
    } catch (e) { console.error('DB tables error:', e); }
}

async function quickConnectSqlite(path) {
    try {
        const res = await fetch('/api/db/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'sqlite', path })
        });
        const data = await res.json();
        if (data.ok) {
            _dbState.activeConnId = data.conn_id;
            showToast(`Connected to ${path}`, 'success');
            await refreshDbConnections();
            await loadDbTables();
        } else {
            showToast(data.message || 'Connection failed', 'error');
        }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function previewTable(tableName) {
    const conn_id = _dbState.activeConnId;
    if (!conn_id) { showToast('No active connection', 'warning'); return; }
    const query = `SELECT * FROM "${tableName}" LIMIT 100`;
    document.getElementById('dbQueryInput').value = query;
    await runDbQuery();
}

async function runDbQuery() {
    const query = document.getElementById('dbQueryInput')?.value?.trim();
    if (!query) { showToast('Enter a query', 'warning'); return; }

    const results = document.getElementById('dbResults');
    if (results) results.innerHTML = '<div class="db-result-info"><i class="fa-solid fa-spinner fa-spin"></i> Running query…</div>';

    const conn_id = _dbState.activeConnId;
    if (!conn_id) {
        if (results) results.innerHTML = '<div class="db-result-info">No active connection. Use the + button to connect to a database first.</div>';
        showToast('No database connection', 'warning');
        return;
    }

    try {
        const res = await fetch('/api/db/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conn_id, query })
        });
        const data = await res.json();

        if (!data.ok) {
            if (results) results.innerHTML = `<div class="db-result-error"><i class="fa-solid fa-circle-xmark"></i> ${escapeHtml(data.message || 'Query failed')}</div>`;
            return;
        }

        const cols = data.columns || [];
        const rows = data.rows || [];

        if (!cols.length && !rows.length) {
            if (results) results.innerHTML = '<div class="db-result-info">Query returned no results.</div>';
            return;
        }

        let html = `<div class="db-result-meta">${rows.length} row${rows.length !== 1 ? 's' : ''} returned</div>`;
        html += '<div class="db-table-wrap"><table class="db-result-table">';
        html += '<thead><tr>' + cols.map(c => `<th>${escapeHtml(c)}</th>`).join('') + '</tr></thead>';
        html += '<tbody>' + rows.map(row =>
            '<tr>' + cols.map(c => `<td>${row[c] === null ? '<em>NULL</em>' : escapeHtml(String(row[c]))}</td>`).join('') + '</tr>'
        ).join('') + '</tbody>';
        html += '</table></div>';

        if (results) results.innerHTML = html;
    } catch (e) {
        if (results) results.innerHTML = `<div class="db-result-error">Request failed: ${escapeHtml(e.message)}</div>`;
    }
}

// Auto-refresh DB connections when panel opens
document.addEventListener('DOMContentLoaded', () => {
    const dbBtn = document.querySelector('[data-panel="database"]');
    if (dbBtn) dbBtn.addEventListener('click', () => setTimeout(refreshDbConnections, 100));
});

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
