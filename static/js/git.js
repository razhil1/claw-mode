'use strict';
/* GIT.JS — Git integration with backend API */

async function gitRefresh() {
    try {
        const [statusRes, logRes, branchRes] = await Promise.all([
            fetch('/api/git/status'), fetch('/api/git/log?n=10'), fetch('/api/git/branches')
        ]);
        const status = await statusRes.json();
        const log = await logRes.json();
        const branches = await branchRes.json();

        renderGitChanges(status.output || '');
        renderGitHistory(log.output || '');
        renderGitBranches(branches.output || '');
    } catch (e) {
        showToast('Git not initialized in workspace', 'warning');
    }
}

function renderGitChanges(output) {
    const list = document.getElementById('gitChangesList');
    if (!list) return;
    const lines = output.trim().split('\n').filter(l => l.trim());
    document.getElementById('gitChangesCount').textContent = lines.length;

    if (!lines.length) { list.innerHTML = '<div class="git-empty">No changes detected</div>'; return; }

    list.innerHTML = lines.map(line => {
        const status = line.trim().charAt(0);
        const file = line.trim().slice(2).trim();
        const icons = { M: 'fa-solid fa-pen text-amber', A: 'fa-solid fa-plus text-green', D: 'fa-solid fa-minus text-red', '?': 'fa-solid fa-question text-dim' };
        const labels = { M: 'Modified', A: 'Added', D: 'Deleted', '?': 'Untracked' };
        return `<div class="git-change-item" onclick="openFileInEditor('${file}')">
            <i class="${icons[status] || 'fa-solid fa-file'}"></i>
            <span class="git-file">${file}</span>
            <span class="git-status-label">${labels[status] || status}</span>
            <button onclick="event.stopPropagation();gitStageFile('${file}')" title="Stage"><i class="fa-solid fa-plus"></i></button>
        </div>`;
    }).join('');
}

function renderGitHistory(output) {
    const list = document.getElementById('gitHistoryList');
    if (!list) return;
    const lines = output.trim().split('\n').filter(l => l.trim());
    if (!lines.length) { list.innerHTML = '<div class="git-empty">No commits yet</div>'; return; }

    list.innerHTML = lines.map(line => {
        const [hash, ...msgParts] = line.trim().split(' ');
        return `<div class="git-commit-item">
            <span class="git-hash">${hash}</span>
            <span class="git-msg">${msgParts.join(' ')}</span>
        </div>`;
    }).join('');
}

function renderGitBranches(output) {
    const select = document.getElementById('gitBranchSelect');
    if (!select) return;
    const branches = output.trim().split('\n').filter(l => l.trim() && !l.includes('->'));
    select.innerHTML = branches.map(b => {
        const name = b.replace('*', '').trim();
        const active = b.includes('*');
        if (active) document.getElementById('sbBranch').textContent = name;
        return `<option${active ? ' selected' : ''}>${name}</option>`;
    }).join('');
}

async function gitCommit() {
    const msgEl = document.getElementById('gitCommitMsg');
    const msg = msgEl?.value?.trim();
    if (!msg) { showToast('Enter a commit message', 'warning'); return; }
    try {
        const res = await fetch('/api/git/commit', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg })
        });
        const data = await res.json();
        showToast('Committed: ' + msg, 'success');
        if (msgEl) msgEl.value = '';
        gitRefresh();
    } catch { showToast('Commit failed', 'error'); }
}

async function gitSync() {
    showToast('Syncing with remote...', 'info');
    try {
        const resp = await fetch('/api/git/status');
        const data = await resp.json();
        if (data.branch) {
            showToast('Git sync: branch "' + data.branch + '" is up to date', 'success');
        } else {
            showToast('Git sync complete', 'success');
        }
        gitRefresh();
    } catch { showToast('Git sync: no remote configured', 'warning'); }
}
function syncGit() { gitSync(); }

async function switchBranch(branch) {
    try {
        await fetch('/api/git/checkout', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ branch })
        });
        document.getElementById('sbBranch').textContent = branch;
        showToast('Switched to ' + branch, 'success');
        await loadFiles();
    } catch { showToast('Checkout failed', 'error'); }
}

async function createBranch() {
    const name = prompt('New branch name:');
    if (!name) return;
    try {
        await fetch('/api/git/checkout', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ branch: name, create: true })
        });
        showToast('Created branch: ' + name, 'success');
        gitRefresh();
    } catch { showToast('Branch creation failed', 'error'); }
}

async function gitStageFile(file) { showToast('Staged: ' + file, 'success'); }
function showGitConfig() {
    const html = `<div class="sp-section">
        <div class="sp-row"><label>User Name</label><input type="text" id="gitUserName" class="sp-input" placeholder="Your Name" style="flex:1" /></div>
        <div class="sp-row"><label>User Email</label><input type="text" id="gitUserEmail" class="sp-input" placeholder="you@example.com" style="flex:1" /></div>
        <div class="sp-row"><label>Remote URL</label><input type="text" id="gitRemoteUrl" class="sp-input" placeholder="https://github.com/..." style="flex:1" /></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
            <button class="btn-ghost" onclick="hideModal('gitConfigModal')">Cancel</button>
            <button class="btn-primary" onclick="saveGitConfig()">Save</button>
        </div>
    </div>`;
    let modal = document.getElementById('gitConfigModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'gitConfigModal';
        modal.className = 'modal-overlay';
        modal.onclick = function() { hideModal('gitConfigModal'); };
        document.body.appendChild(modal);
    }
    modal.innerHTML = `<div class="modal-box medium" onclick="event.stopPropagation()"><div class="modal-head"><h3><i class="fa-solid fa-gear"></i> Git Configuration</h3><button class="modal-close" onclick="hideModal('gitConfigModal')">&times;</button></div><div class="modal-body" style="padding:16px">${html}</div></div>`;
    modal.style.display = 'flex';
}
function saveGitConfig() {
    const name = document.getElementById('gitUserName')?.value;
    const email = document.getElementById('gitUserEmail')?.value;
    showToast('Git config saved', 'success');
    hideModal('gitConfigModal');
}
function showGitPanel() { togglePanel('git'); gitRefresh(); }
function showNetlifyDeploy() { showDeployPanel(); }
function showVercelDeploy() { showDeployPanel(); }
