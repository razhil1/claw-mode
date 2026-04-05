'use strict';
/* MODELS.JS — Model selector, cards, filtering, switching */

let _allModels = [];

async function loadModels() {
    try {
        const res = await fetch('/api/models');
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
    } catch (e) {
        console.warn('Failed to load models:', e);
        document.getElementById('activeModelPill').textContent = 'Offline';
    }
}

function renderModelCards() {
    const grid = document.getElementById('modelCardsGrid');
    if (!grid) return;

    grid.innerHTML = _allModels.map(m => {
        const isActive = m.id === NX.activeModelId;
        const providerClass = `model-card-${m.provider || 'nvidia'}`;
        const tierBadge = m.tier === 'free' ? '<span class="mc-tier free">FREE</span>' : '<span class="mc-tier paid">PRO</span>';
        const ctxK = m.context ? Math.round(m.context / 1000) + 'k' : '?';

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
    }).join('');
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
        document.getElementById('agentCardModel').textContent = NX.activeModel;

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
    if (!query) { renderModelCards(); return; }
    const q = query.toLowerCase();
    const filtered = _allModels.filter(m =>
        (m.label || '').toLowerCase().includes(q) ||
        (m.id || '').toLowerCase().includes(q) ||
        (m.provider || '').toLowerCase().includes(q) ||
        (m.description || '').toLowerCase().includes(q)
    );
    const grid = document.getElementById('modelCardsGrid');
    if (!grid) return;
    // Re-render with filtered list
    const orig = _allModels;
    _allModels = filtered;
    renderModelCards();
    _allModels = orig;
}

function filterModelsByProvider(provider) {
    document.querySelectorAll('.mfc').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');

    if (provider === 'all') { renderModelCards(); return; }
    const filtered = _allModels.filter(m => (m.provider || 'nvidia') === provider);
    const orig = _allModels;
    _allModels = filtered;
    renderModelCards();
    _allModels = orig;
}

async function saveKey(provider) {
    const inputMap = { nvidia: 'nvidiaKeyInput', openai: 'openaiKeyInput', anthropic: 'anthropicKeyInput', google: 'googleKeyInput' };
    const statusMap = { nvidia: 'nvidiaKeyStatus', openai: 'openaiKeyStatus', anthropic: 'anthropicKeyStatus', google: 'googleKeyStatus' };
    const input = document.getElementById(inputMap[provider]);
    const status = document.getElementById(statusMap[provider]);
    const key = input?.value?.trim();

    if (!key) { showToast('Please enter a key', 'warning'); return; }

    if (status) status.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Validating...';

    if (provider === 'nvidia') {
        try {
            const res = await fetch('/api/settings/set-key', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key })
            });
            const data = await res.json();
            if (data.ok) {
                if (status) status.innerHTML = '<i class="fa-solid fa-circle-check text-green"></i> Key saved & validated';
                showToast('NVIDIA key saved!', 'success');
                const warn = document.getElementById('wcKeyWarning');
                if (warn) warn.style.display = 'none';
            } else {
                if (status) status.innerHTML = '<i class="fa-solid fa-circle-xmark text-red"></i> Invalid key';
            }
        } catch { if (status) status.innerHTML = '<i class="fa-solid fa-circle-xmark text-red"></i> Validation failed'; }
    } else {
        // Store other keys in localStorage
        localStorage.setItem(`nexus_key_${provider}`, key);
        if (status) status.innerHTML = '<i class="fa-solid fa-circle-check text-green"></i> Key saved locally';
        showToast(`${provider} key saved`, 'success');
    }
}
