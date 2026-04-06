'use strict';
/* MODELS.JS — Model selector, provider tabs, Ollama discovery */

let _allModels = [];

async function loadModels() {
    try {
        const res  = await fetch('/api/models');
        const data = await res.json();
        _allModels = data.models || [];
        NX.activeModelId = data.active || '';

        const active = _allModels.find(m => m.id === data.active);
        NX.activeModel = active?.label || active?.short || data.active || 'Unknown';

        const pill = document.getElementById('activeModelPill');
        if (pill) pill.textContent = active?.short || NX.activeModel;

        const cardModel = document.getElementById('agentCardModel');
        if (cardModel) cardModel.textContent = NX.activeModel;

        const sbProvider = document.getElementById('sbProvider');
        if (sbProvider && active) sbProvider.textContent = (active.provider || 'nvidia').toUpperCase();

        renderModelCards();
        renderSettingsModelGrid();
        loadProviderStatus();
    } catch (e) {
        console.warn('Failed to load models:', e);
        const pill = document.getElementById('activeModelPill');
        if (pill) pill.textContent = 'Offline';
    }
}

/* ── Provider status indicators ─────────────────────────────────────────── */
async function loadProviderStatus() {
    try {
        const res  = await fetch('/api/settings/key-status');
        const data = await res.json();
        const providers = data.providers || {};
        Object.entries(providers).forEach(([p, info]) => {
            const dot = document.getElementById(`pDot-${p}`);
            const lbl = document.getElementById(`pLabel-${p}`);
            if (dot) dot.className = `p-status-dot ${info.configured ? 'ok' : 'off'}`;
            if (lbl) lbl.textContent = info.configured
                ? (p === 'ollama' ? 'Connected' : info.prefix || 'Saved')
                : (p === 'ollama' ? 'Not running' : 'Not configured');
        });
        // Populate existing key inputs (masked)
        Object.entries(providers).forEach(([p, info]) => {
            const inp = document.getElementById(`${p}KeyInput`);
            if (inp && info.configured && p !== 'ollama') inp.placeholder = info.prefix || 'Key saved';
            const urlInp = document.getElementById(`${p}BaseUrlInput`);
            if (urlInp && info.base_url) urlInp.value = info.base_url;
        });
    } catch {}
}

/* ── Model cards ─────────────────────────────────────────────────────────── */
function renderModelCards(filterProvider = 'all') {
    const grid = document.getElementById('modelCardsGrid');
    if (!grid) return;

    const models = filterProvider === 'all'
        ? _allModels
        : _allModels.filter(m => (m.provider || 'nvidia') === filterProvider);

    grid.innerHTML = models.map(m => {
        const isActive = m.id === NX.activeModelId;
        const providerClass = `model-card-${m.provider || 'nvidia'}`;
        const tier = m.tier || 'free';
        const tierBadge = tier === 'free'  ? '<span class="mc-tier free">FREE</span>'
                        : tier === 'local' ? '<span class="mc-tier local">LOCAL</span>'
                        :                    '<span class="mc-tier paid">PRO</span>';
        const ctxK = m.context ? (m.context >= 1000000 ? Math.round(m.context/1000)+'k' : Math.round(m.context/1000)+'k') : '?';

        return `<div class="model-card ${providerClass} ${isActive ? 'active' : ''}" onclick="selectModel('${m.id}')">
            <div class="mc-header">
                <span class="mc-emoji">${m.emoji || '⚡'}</span>
                <div class="mc-info">
                    <div class="mc-name">${m.label || m.id}</div>
                    <div class="mc-provider">${(m.provider || 'nvidia').toUpperCase()}</div>
                </div>
                ${tierBadge}
            </div>
            <div class="mc-desc">${m.description || ''}</div>
            <div class="mc-footer">
                <span class="mc-ctx"><i class="fa-solid fa-memory"></i> ${ctxK}</span>
                <span class="mc-role">${m.role || 'balanced'}</span>
                <span class="mc-price">${m.price_note || 'Free'}</span>
            </div>
            ${isActive ? '<div class="mc-active-badge"><i class="fa-solid fa-check"></i> Active</div>' : ''}
        </div>`;
    }).join('') || '<div class="mc-empty">No models found for this provider</div>';
}

function renderSettingsModelGrid() {
    const grid = document.getElementById('settingsModelGrid');
    if (!grid) return;

    grid.innerHTML = _allModels.map(m => {
        const isActive = m.id === NX.activeModelId;
        return `<div class="model-option ${isActive ? 'active' : ''}" onclick="selectModel('${m.id}')">
            <span class="mo-emoji">${m.emoji || '⚡'}</span>
            <div class="mo-info">
                <div class="mo-label">${m.label || m.id}</div>
                <div class="mo-sub">${(m.provider || '').toUpperCase()} · ${m.role || ''}</div>
            </div>
            ${isActive ? '<i class="fa-solid fa-circle-check mo-check"></i>' : ''}
        </div>`;
    }).join('');
}

async function selectModel(modelId) {
    try {
        await fetch('/api/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: modelId })
        });

        NX.activeModelId = modelId;
        const m = _allModels.find(x => x.id === modelId);
        NX.activeModel = m?.label || modelId;

        document.getElementById('activeModelPill').textContent = m?.short || NX.activeModel;
        document.getElementById('agentCardModel').textContent  = NX.activeModel;

        const sbProvider = document.getElementById('sbProvider');
        if (sbProvider && m) sbProvider.textContent = (m.provider || 'nvidia').toUpperCase();

        NX.contextLimit = m?.context || 128000;
        updateContextBudget();
        renderModelCards();
        renderSettingsModelGrid();
        hideModal('modelSelectorModal');
        showToast(`Model: ${NX.activeModel}`, 'success');
    } catch (e) {
        showToast('Failed to switch model', 'error');
    }
}

function filterModels(query) {
    const q = (query || '').toLowerCase();
    if (!q) { renderModelCards(); return; }
    const orig = _allModels;
    _allModels = _allModels.filter(m =>
        (m.label || '').toLowerCase().includes(q) ||
        (m.id || '').toLowerCase().includes(q) ||
        (m.provider || '').toLowerCase().includes(q) ||
        (m.description || '').toLowerCase().includes(q)
    );
    renderModelCards();
    _allModels = orig;
}

function filterModelsByProvider(provider, el) {
    document.querySelectorAll('.mfc').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    renderModelCards(provider);
}

/* ── Provider key management ─────────────────────────────────────────────── */
async function saveKey(provider) {
    const inp    = document.getElementById(`${provider}KeyInput`);
    const status = document.getElementById(`${provider}KeyStatus`);
    const key    = inp?.value?.trim();

    if (!key) { showToast('Please enter a key', 'warning'); return; }
    if (status) status.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Validating...';

    try {
        const res  = await fetch('/api/settings/set-key', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, provider }),
        });
        const data = await res.json();

        // Validate after saving
        let valid = { ok: true };
        if (provider !== 'ollama') {
            const vRes = await fetch('/api/settings/validate-key', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key, provider }),
            });
            valid = await vRes.json();
        }

        if (valid.ok) {
            if (status) status.innerHTML = `<i class="fa-solid fa-circle-check" style="color:var(--green)"></i> ${valid.message || 'Key saved & validated'}`;
            showToast(`${provider.toUpperCase()} key saved!`, 'success');
            const warn = document.getElementById('wcKeyWarning');
            if (warn) warn.style.display = 'none';
            loadProviderStatus();
            loadModels();
        } else {
            if (status) status.innerHTML = `<i class="fa-solid fa-circle-xmark" style="color:var(--red)"></i> ${valid.message || 'Invalid key'}`;
        }
    } catch {
        if (status) status.innerHTML = '<i class="fa-solid fa-circle-xmark" style="color:var(--red)"></i> Validation failed';
    }
}

async function saveOllamaConfig() {
    const urlInp = document.getElementById('ollamaBaseUrlInput');
    const status = document.getElementById('ollamaKeyStatus');
    const base_url = urlInp?.value?.trim() || 'http://localhost:11434/v1';

    if (status) status.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Connecting...';

    try {
        await fetch('/api/settings/set-provider', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: 'ollama', base_url }),
        });

        const res  = await fetch(`/api/ollama/models?base_url=${encodeURIComponent(base_url)}`);
        const data = await res.json();

        if (data.ok) {
            if (status) status.innerHTML = `<i class="fa-solid fa-circle-check" style="color:var(--green)"></i> ${data.message}`;
            showToast('Ollama connected!', 'success');
            loadModels();
        } else {
            if (status) status.innerHTML = `<i class="fa-solid fa-circle-xmark" style="color:var(--red)"></i> ${data.message}`;
        }
    } catch {
        if (status) status.innerHTML = '<i class="fa-solid fa-circle-xmark" style="color:var(--red)"></i> Connection failed';
    }
}

async function saveCustomProvider() {
    const keyInp  = document.getElementById('customKeyInput');
    const urlInp  = document.getElementById('customBaseUrlInput');
    const status  = document.getElementById('customKeyStatus');
    const key     = keyInp?.value?.trim() || '';
    const base_url = urlInp?.value?.trim() || '';

    if (!base_url) { showToast('Base URL is required for custom provider', 'warning'); return; }
    if (status) status.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';

    try {
        await fetch('/api/settings/set-provider', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: 'custom', key, base_url }),
        });
        if (status) status.innerHTML = '<i class="fa-solid fa-circle-check" style="color:var(--green)"></i> Custom provider saved';
        showToast('Custom provider saved!', 'success');
        loadModels();
    } catch {
        if (status) status.innerHTML = '<i class="fa-solid fa-circle-xmark" style="color:var(--red)"></i> Failed to save';
    }
}
