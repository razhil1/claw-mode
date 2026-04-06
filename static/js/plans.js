(function() {
    'use strict';

    window.NXPlans = {
        current: null,
        all: null,
    };

    async function loadPlans() {
        try {
            const resp = await fetch('/api/plans');
            const data = await resp.json();
            NXPlans.current = data.current;
            NXPlans.all = data.plans;
            updatePlanBadge();
            return data;
        } catch (e) {
            console.warn('Failed to load plans:', e);
        }
    }

    function updatePlanBadge() {
        const badge = document.getElementById('planBadge');
        if (!badge || !NXPlans.current) return;
        const tier = NXPlans.current.current_tier || 'community';
        const name = NXPlans.current.name || 'Community';
        const color = NXPlans.current.badge_color || '#4ade80';
        badge.textContent = name;
        badge.style.background = color + '22';
        badge.style.color = color;
        badge.style.borderColor = color + '44';
        badge.style.display = 'inline-flex';
    }

    window.showPlansModal = async function() {
        if (!NXPlans.all) await loadPlans();
        const modal = document.getElementById('plansModal');
        if (!modal) return;
        renderPlansContent();
        modal.style.display = 'flex';
    };

    function renderPlansContent() {
        const container = document.getElementById('plansContent');
        if (!container || !NXPlans.all) return;
        const current = NXPlans.current?.current_tier || 'community';
        let html = '<div class="plans-grid">';
        const tiers = ['community', 'pro', 'enterprise'];
        const icons = { community: 'fa-users', pro: 'fa-rocket', enterprise: 'fa-building' };
        tiers.forEach(tid => {
            const plan = NXPlans.all[tid];
            if (!plan) return;
            const isCurrent = tid === current;
            html += `
                <div class="plan-card ${isCurrent ? 'plan-current' : ''} ${tid === 'pro' ? 'plan-featured' : ''}">
                    ${tid === 'pro' ? '<div class="plan-popular">Most Popular</div>' : ''}
                    <div class="plan-header">
                        <i class="fa-solid ${icons[tid]}" style="color:${plan.badge_color};font-size:24px"></i>
                        <h3>${plan.name}</h3>
                        <div class="plan-price">${plan.price}</div>
                    </div>
                    <ul class="plan-features">
                        ${plan.highlights.map(h => `<li><i class="fa-solid fa-check"></i> ${h}</li>`).join('')}
                    </ul>
                    <div class="plan-action">
                        ${isCurrent
                            ? '<button class="btn-ghost" disabled>Current Plan</button>'
                            : tid === 'community'
                                ? '<button class="btn-ghost" onclick="downgradeToFree()">Downgrade</button>'
                                : `<button class="btn-primary" onclick="upgradePlan('${tid}')"><i class="fa-solid fa-arrow-up"></i> Upgrade</button>`
                        }
                    </div>
                </div>`;
        });
        html += '</div>';
        html += `
            <div class="plan-license-section">
                <h4><i class="fa-solid fa-key"></i> Have a license key?</h4>
                <div class="plan-license-row">
                    <input type="text" id="licenseKeyInput" placeholder="NX-PRO-XXXXXXXX or NX-ENT-XXXXXXXX" class="modal-input" />
                    <button class="btn-primary" onclick="activateLicense()"><i class="fa-solid fa-check"></i> Activate</button>
                </div>
                ${NXPlans.current?.license_key ? `<div class="plan-active-license"><i class="fa-solid fa-circle-check"></i> Active: ${NXPlans.current.license_key}</div>` : ''}
            </div>
            <div class="plan-telegram-cta">
                <i class="fa-brands fa-telegram" style="font-size:20px;color:#29b6f6"></i>
                <span>Purchase via Telegram: Send <code>/plans</code> to <strong>@NexusIDEBot</strong></span>
            </div>`;
        container.innerHTML = html;
    }

    window.upgradePlan = async function(tier) {
        try {
            const resp = await fetch('/api/plans/purchase-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tier }),
            });
            const data = await resp.json();
            if (data.code) {
                showToast(`Purchase code: ${data.code}\n\nSend to @NexusIDEBot on Telegram:\n/activate ${data.code}`, 'info', 15000);
                const el = document.getElementById('licenseKeyInput');
                if (el) el.placeholder = `Got a key from Telegram? Paste it here`;
            } else {
                showToast(data.error || 'Failed to generate code', 'error');
            }
        } catch (e) {
            showToast('Failed to generate purchase code', 'error');
        }
    };

    window.activateLicense = async function() {
        const input = document.getElementById('licenseKeyInput');
        const settingsInput = document.getElementById('settingsLicenseKey');
        const key = (input?.value?.trim()) || (settingsInput?.value?.trim());
        if (!key) { showToast('Enter a license key', 'warn'); return; }
        try {
            const resp = await fetch('/api/license/activate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ license_key: key }),
            });
            const data = await resp.json();
            if (data.success) {
                showToast(data.message, 'success');
                await loadPlans();
                renderPlansContent();
            } else {
                showToast(data.message || 'Activation failed', 'error');
            }
        } catch (e) {
            showToast('Activation error', 'error');
        }
    };

    window.downgradeToFree = async function() {
        if (!confirm('Downgrade to Community (free)? You will lose Pro/Enterprise features.')) return;
        try {
            const resp = await fetch('/api/license/deactivate', { method: 'POST' });
            const data = await resp.json();
            showToast(data.message, 'info');
            await loadPlans();
            renderPlansContent();
        } catch (e) {
            showToast('Failed to downgrade', 'error');
        }
    };

    window.showProfileModal = async function() {
        try {
            const resp = await fetch('/api/profile');
            const profile = await resp.json();
            const modal = document.getElementById('profileModal');
            const content = document.getElementById('profileContent');
            if (!modal || !content) return;
            const limit = profile.features?.messages_per_day;
            const limitText = limit === -1 ? 'Unlimited' : `${profile.messages_today} / ${limit}`;
            content.innerHTML = `
                <div class="profile-grid">
                    <div class="profile-card">
                        <div class="profile-avatar"><i class="fa-solid fa-user-astronaut"></i></div>
                        <h3>NEXUS User</h3>
                        <div class="profile-plan-badge" style="background:${profile.badge_color}22;color:${profile.badge_color};border:1px solid ${profile.badge_color}44">${profile.plan_name}</div>
                    </div>
                    <div class="profile-stats">
                        <div class="profile-stat"><label>Messages Today</label><span>${limitText}</span></div>
                        <div class="profile-stat"><label>Plan</label><span>${profile.plan_name}</span></div>
                        <div class="profile-stat"><label>Support</label><span style="text-transform:capitalize">${profile.features?.support || 'community'}</span></div>
                        ${profile.license_key ? `<div class="profile-stat"><label>License</label><span style="font-family:var(--font-mono);font-size:11px">${profile.license_key}</span></div>` : ''}
                        ${profile.expires_at ? `<div class="profile-stat"><label>Expires</label><span>${new Date(profile.expires_at).toLocaleDateString()}</span></div>` : ''}
                        <div class="profile-stat"><label>Agent Modes</label><span>${profile.features?.agent_modes?.length || 3}</span></div>
                        <div class="profile-stat"><label>Multi-Agent</label><span>${profile.features?.multi_agent ? 'Yes' : 'No'}</span></div>
                    </div>
                </div>
                <div class="profile-actions">
                    <button class="btn-primary" onclick="hideModal('profileModal');showPlansModal()"><i class="fa-solid fa-arrow-up"></i> ${profile.plan === 'community' ? 'Upgrade Plan' : 'Manage Plan'}</button>
                    <button class="btn-ghost" onclick="hideModal('profileModal')">Close</button>
                </div>`;
            modal.style.display = 'flex';
        } catch (e) {
            showToast('Failed to load profile', 'error');
        }
    };

    window.showGuideModal = async function() {
        try {
            const resp = await fetch('/api/guide');
            const data = await resp.json();
            const modal = document.getElementById('guideModal');
            const content = document.getElementById('guideContent');
            if (!modal || !content) return;
            content.innerHTML = data.sections.map((s, i) => `
                <div class="guide-section">
                    <div class="guide-header" onclick="this.parentElement.classList.toggle('open')">
                        <span class="guide-num">${i + 1}</span>
                        <h4>${s.title}</h4>
                        <i class="fa-solid fa-chevron-down guide-chevron"></i>
                    </div>
                    <div class="guide-body"><pre>${s.content}</pre></div>
                </div>`).join('');
            document.querySelector('.guide-section')?.classList.add('open');
            modal.style.display = 'flex';
        } catch (e) {
            showToast('Failed to load guide', 'error');
        }
    };

    window.showCommunityModal = async function() {
        try {
            const resp = await fetch('/api/community');
            const data = await resp.json();
            const modal = document.getElementById('communityModal');
            const content = document.getElementById('communityContent');
            if (!modal || !content) return;
            content.innerHTML = `<div class="community-grid">${data.links.map(l => `
                <a href="${l.url}" target="_blank" rel="noopener" class="community-card">
                    <i class="fa-solid fa-${l.icon}"></i>
                    <h4>${l.name}</h4>
                    <p>${l.description}</p>
                </a>`).join('')}</div>`;
            modal.style.display = 'flex';
        } catch (e) {
            showToast('Failed to load community info', 'error');
        }
    };

    window.setupTelegramBot = async function() {
        const input = document.getElementById('telegramTokenInput');
        const token = input?.value?.trim();
        if (!token) { showToast('Enter a bot token', 'warn'); return; }
        try {
            const resp = await fetch('/api/telegram/setup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token }),
            });
            const data = await resp.json();
            if (data.success) {
                showToast(data.message, 'success');
                document.getElementById('telegramStatus')?.classList.add('connected');
            } else {
                showToast(data.message, 'error');
            }
        } catch (e) {
            showToast('Failed to connect Telegram bot', 'error');
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        loadPlans();
    });
})();
