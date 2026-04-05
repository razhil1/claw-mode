'use strict';

/* ═══════════════════════════════════════════════════════════════════════════════
   AGENT.JS — AI chat, streaming, tool handling, memory, ultra mode
   ═══════════════════════════════════════════════════════════════════════════════ */

// ─── Send Prompt ─────────────────────────────────────────────────────────────
async function sendPrompt() {
    const el = document.getElementById('chatPrompt');
    const text = el.value.trim();
    if (!text || NX.isAgentRunning) return;

    // Check if swarm mode
    if (NX.swarmMode) {
        return sendSwarmPrompt();
    }

    el.value = '';
    el.style.height = 'auto';
    document.getElementById('charCount').textContent = '0';

    // Hide welcome card
    const wc = document.getElementById('welcomeCard');
    if (wc) wc.style.display = 'none';

    // Hide quick chips after first message
    const qc = document.getElementById('quickChips');
    if (qc && NX.chatHistory.length > 0) qc.style.display = 'none';

    // Add user message
    appendMessage(text, 'user');
    NX.chatHistory.push({ role: 'user', content: text, time: Date.now() });
    setAgentWorking(true);

    try {
        const body = {
            prompt: text,
            session_id: NX.sessionId,
            mode: NX.taskMode || '',
        };

        // Include attachments if any
        if (NX.attachments.length > 0) {
            body.attachments = NX.attachments.map(a => ({ name: a.name, content: a.content }));
            clearAttachments();
        }

        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const contentDiv = appendMessage('', 'agent');
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let fullResponse = '';
        let toolsUsed = 0;
        let filesChanged = [];
        let lastFileRefresh = 0;

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
                    handleStreamEvent(evt, contentDiv);

                    if (evt.type === 'token' && evt.text) {
                        fullResponse += evt.text;
                    }
                    if (evt.type === 'tool_call') {
                        toolsUsed++;
                        NX.toolCallCount++;
                        logToolCall(evt);
                    }
                    if (evt.type === 'tool_result') {
                        logToolCall({
                            type: 'tool_result',
                            tool: evt.tool || 'unknown',
                            summary: `${evt.tool} (${evt.elapsed}s)`,
                            result: evt.result || '',
                        });

                        // ─── AUTO-REFRESH FILE TREE after file operations ───
                        const isFileOp = ['FileEditTool', 'FilePatchTool', 'FileDeleteTool'].includes(evt.tool);
                        const isBash = evt.tool === 'BashTool';
                        const now = Date.now();

                        if (isFileOp || isBash) {
                            // Throttle file refreshes to once per 800ms
                            if (now - lastFileRefresh > 800) {
                                lastFileRefresh = now;
                                loadFiles().then(() => {
                                    // Auto-open the file in editor if it was created/edited
                                    if (isFileOp && evt.success && evt.result) {
                                        const match = evt.result.match(/['"]([^'"]+)['"]/);
                                        if (match) {
                                            const filePath = match[1];
                                            // Only auto-open if it's a success write
                                            if (evt.result.includes('Success') && !evt.result.includes('deleted')) {
                                                openFileInEditor(filePath);
                                            }
                                        }
                                    }
                                });
                            }

                            // Show bash output in terminal panel too
                            if (isBash && evt.result) {
                                appendTerminalOutput(evt.result);
                            }
                        }

                        // Track changed files
                        if (isFileOp && evt.success) {
                            const match = evt.result?.match(/['"]([^'"]+)['"]/);
                            if (match) filesChanged.push(match[1]);
                        }
                    }
                    if (evt.type === 'done') {
                        NX.turnCount++;
                        toolsUsed = evt.tools_used || toolsUsed;
                        if (evt.files_changed) filesChanged = evt.files_changed;
                    }
                    if (evt.type === 'error') {
                        showToast('Agent error: ' + evt.message, 'error');
                    }
                    if (evt.type === 'key_error') {
                        showToast('API Key Error: ' + evt.message, 'error');
                        appendSystemMessage('⚠️ API key error. Please configure your key in Settings.');
                    }
                } catch (e) { }
            }
        }

        NX.chatHistory.push({ role: 'assistant', content: fullResponse, time: Date.now() });
        updateAgentStats();

        // Final reload of files after agent completes
        await loadFiles();

        // Auto-open first changed file if no file is currently open
        if (filesChanged.length > 0 && !NX.currentFile) {
            openFileInEditor(filesChanged[0]);
        }

        // Auto-refresh preview if HTML was modified
        if (filesChanged.some(f => f.endsWith('.html')) || fullResponse.includes('.html')) {
            refreshPreview();
        }

        // Completion summary is now rendered as a done_summary card (agent SSE event)

    } catch (e) {
        showToast('Communication error: ' + e.message, 'error');
        appendSystemMessage('⚠️ Connection lost. Check that the server is running.');
    }

    setAgentWorking(false);
}

// ─── Terminal Output Helper ──────────────────────────────────────────────────
function appendTerminalOutput(output) {
    // Write to xterm if available
    if (typeof _xterm !== 'undefined' && _xterm) {
        const lines = output.split('\n');
        lines.forEach(line => _xterm.writeln(line));
        _xterm.write('\x1b[36m❯\x1b[0m ');
    }

    // Also write to simple terminal
    const stermOutput = document.getElementById('stermOutput');
    if (stermOutput) {
        const resultDiv = document.createElement('div');
        resultDiv.className = 'sterm-result agent-output';
        resultDiv.innerHTML = `<span class="sterm-agent-tag">🤖 Agent</span><pre>${escapeHtml(output)}</pre>`;
        stermOutput.appendChild(resultDiv);
        stermOutput.scrollTop = stermOutput.scrollHeight;
    }

    // Update terminal badge
    const badge = document.getElementById('rptab-terminal-badge');
    if (badge) {
        badge.style.display = '';
        badge.textContent = '●';
    }
}

function handleStreamEvent(evt, contentDiv) {
    if (evt.type === 'token' && evt.text) {
        contentDiv.dataset.md = (contentDiv.dataset.md || '') + evt.text;
        if (typeof marked !== 'undefined') {
            contentDiv.innerHTML = marked.parse(contentDiv.dataset.md);
            // Highlight code blocks
            contentDiv.querySelectorAll('pre code').forEach(block => {
                if (typeof hljs !== 'undefined') hljs.highlightElement(block);
            });
        } else {
            contentDiv.textContent = contentDiv.dataset.md;
        }
        scrollChat();
    }

    if (evt.type === 'live_text' && evt.text) {
        // Show live streamed text (prose without tool calls)
        contentDiv.dataset.md = evt.text;
        if (typeof marked !== 'undefined') {
            contentDiv.innerHTML = marked.parse(evt.text);
            contentDiv.querySelectorAll('pre code').forEach(block => {
                if (typeof hljs !== 'undefined') hljs.highlightElement(block);
            });
        } else {
            contentDiv.textContent = evt.text;
        }
        scrollChat();
    }

    if (evt.type === 'thinking') {
        updateStepBar('Thinking', evt.text || 'Agent is reasoning...');
    }

    if (evt.type === 'thought') {
        // Create or update a thought block in the current message
        let thoughtEl = contentDiv.querySelector('.agent-thought');
        if (!thoughtEl) {
            thoughtEl = document.createElement('div');
            thoughtEl.className = 'agent-thought';
            thoughtEl.innerHTML = `
                <div class="thought-header" onclick="this.parentElement.classList.toggle('collapsed')">
                   <i class="fa-solid fa-brain"></i> Agent Reasoning Trace
                   <i class="fa-solid fa-chevron-down toggle-icon"></i>
                </div>
                <div class="thought-content"></div>
            `;
            contentDiv.prepend(thoughtEl);
        }
        const inner = thoughtEl.querySelector('.thought-content');
        if (inner) {
            if (typeof marked !== 'undefined') {
                inner.innerHTML = marked.parse(evt.text);
            } else {
                inner.textContent = evt.text;
            }
        }
    }

    if (evt.type === 'plan') {
        // Show plan as a collapsible block
        let planEl = contentDiv.querySelector('.agent-plan');
        if (!planEl) {
            planEl = document.createElement('div');
            planEl.className = 'agent-plan';
            planEl.innerHTML = `
                <div class="plan-header" onclick="this.parentElement.classList.toggle('collapsed')">
                   <i class="fa-solid fa-list-check"></i> Execution Plan
                   <i class="fa-solid fa-chevron-down toggle-icon"></i>
                </div>
                <div class="plan-content"></div>
            `;
            contentDiv.appendChild(planEl);
        }
        const inner = planEl.querySelector('.plan-content');
        if (inner) {
            if (typeof marked !== 'undefined') {
                inner.innerHTML = marked.parse(evt.text);
            } else {
                inner.textContent = evt.text;
            }
        }
    }

    if (evt.type === 'plan_steps' && evt.steps && evt.steps.length > 0) {
        // Always create/replace the plan element with an interactive step checklist
        let planEl = contentDiv.querySelector('.agent-plan');
        if (!planEl) {
            planEl = document.createElement('div');
            planEl.className = 'agent-plan';
            contentDiv.appendChild(planEl);
        }
        // Always rewrite inner HTML so we get a real <ol>, even if plan event
        // already created a <div class="plan-content">
        planEl.innerHTML = `
            <div class="plan-header" onclick="this.parentElement.classList.toggle('collapsed')">
               <i class="fa-solid fa-list-check"></i> Execution Plan
               <i class="fa-solid fa-chevron-down toggle-icon"></i>
            </div>
            <ol class="plan-step-list"></ol>
        `;
        const list = planEl.querySelector('.plan-step-list');
        evt.steps.forEach((step, i) => {
            const li = document.createElement('li');
            li.className = 'plan-step pending';
            li.dataset.stepIndex = i;
            li.innerHTML = `<span class="step-icon"><i class="fa-regular fa-circle"></i></span><span class="step-label">${escapeHtml(step)}</span>`;
            list.appendChild(li);
        });
        planEl.dataset.stepsCount = evt.steps.length;
        scrollChat();
    }

    if (evt.type === 'step_start') {
        // Activate the matching plan step
        const li = contentDiv.querySelector(`.plan-step[data-step-index="${evt.index}"]`);
        if (li) {
            li.className = 'plan-step active';
            li.querySelector('.step-icon').innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
        }
        updateStepBar('Executing', `Step ${evt.index + 1}: ${(evt.label || '').slice(0, 60)}`);
    }

    if (evt.type === 'step_done') {
        const li = contentDiv.querySelector(`.plan-step[data-step-index="${evt.index}"]`);
        if (li) {
            li.className = 'plan-step done';
            li.querySelector('.step-icon').innerHTML = '<i class="fa-solid fa-circle-check"></i>';
        }
    }

    if (evt.type === 'step_failed') {
        const li = contentDiv.querySelector(`.plan-step[data-step-index="${evt.index}"]`);
        if (li) {
            li.className = 'plan-step failed';
            li.querySelector('.step-icon').innerHTML = '<i class="fa-solid fa-circle-xmark"></i>';
            li.title = evt.error || 'Step failed';
        }
    }

    if (evt.type === 'done_summary') {
        // Render a collapsible completion summary card
        const card = document.createElement('div');
        card.className = 'agent-done-summary';
        const filesHtml = (evt.files_changed || []).length > 0
            ? `<div class="ds-section"><div class="ds-section-title"><i class="fa-solid fa-file-pen"></i> Files Changed</div><ul class="ds-file-list">${(evt.files_changed || []).map(f => `<li><code>${escapeHtml(f)}</code></li>`).join('')}</ul></div>`
            : '';
        const cmdsHtml = (evt.commands_run || []).length > 0
            ? `<div class="ds-section"><div class="ds-section-title"><i class="fa-solid fa-terminal"></i> Commands Run</div><ul class="ds-cmd-list">${(evt.commands_run || []).map(c => `<li><code>${escapeHtml(c.slice(0, 80))}</code></li>`).join('')}</ul></div>`
            : '';
        const errHtml = (evt.errors_encountered || []).length > 0
            ? `<div class="ds-section ds-errors"><div class="ds-section-title"><i class="fa-solid fa-triangle-exclamation"></i> Errors Encountered</div><ul class="ds-err-list">${(evt.errors_encountered || []).map(e => `<li><code>${escapeHtml(e.slice(0, 120))}</code></li>`).join('')}</ul></div>`
            : '';
        const stepsHtml = evt.steps_total > 0
            ? `<div class="ds-stat"><i class="fa-solid fa-check-double"></i> ${evt.steps_done}/${evt.steps_total} steps</div>`
            : '';
        const turnsHtml = `<div class="ds-stat"><i class="fa-solid fa-rotate"></i> ${evt.turns} turn${evt.turns !== 1 ? 's' : ''}</div>`;
        const modeHtml = evt.mode ? `<div class="ds-stat"><i class="fa-solid fa-tag"></i> ${escapeHtml(evt.mode)}</div>` : '';
        const resultStmt = evt.result_statement ? `<div class="ds-result-stmt">${escapeHtml(evt.result_statement)}</div>` : '';

        card.innerHTML = `
            <div class="ds-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <i class="fa-solid fa-circle-check"></i> Task Complete
                <div class="ds-stats-row">${turnsHtml}${stepsHtml}${modeHtml}</div>
                <i class="fa-solid fa-chevron-down ds-toggle-icon"></i>
            </div>
            <div class="ds-body">
                ${resultStmt}
                ${filesHtml}
                ${cmdsHtml}
                ${errHtml}
                ${(evt.files_changed || []).length === 0 && (evt.commands_run || []).length === 0 && (evt.errors_encountered || []).length === 0 ? '<div class="ds-empty">No files modified.</div>' : ''}
            </div>
        `;
        // Append after the whole message-wrap, not inside it
        const msgWrap = contentDiv.closest('.message-wrap') || contentDiv.parentElement;
        const chatContainer = document.getElementById('chatMessages');
        if (chatContainer) {
            chatContainer.appendChild(card);
        } else {
            msgWrap.parentElement.insertBefore(card, msgWrap.nextSibling);
        }
        scrollChat();
    }

    if (evt.type === 'tool_call') {
        updateStepBar('Tool Call', `${evt.tool || 'tool'}: ${(evt.payload || '').slice(0, 80)}`);
        showStepToolBadge(evt.tool || 'tool');

        // Show tool call in chat as inline badge
        const toolBadge = document.createElement('div');
        toolBadge.className = 'agent-tool-badge';
        toolBadge.innerHTML = `<i class="fa-solid fa-wrench"></i> <strong>${evt.tool}</strong>: <code>${escapeHtml((evt.payload || '').slice(0, 120))}</code>`;
        contentDiv.appendChild(toolBadge);
        scrollChat();
    }

    if (evt.type === 'tool_result') {
        updateStepBar('Processing', `${evt.tool} completed (${evt.elapsed || 0}s)`);

        // Show tool result in chat
        const resultBadge = document.createElement('div');
        resultBadge.className = `agent-tool-result ${evt.success ? 'success' : 'error'}`;
        const resultPreview = (evt.result || '').slice(0, 300);
        resultBadge.innerHTML = `
            <div class="tool-result-header" onclick="this.parentElement.classList.toggle('expanded')">
                <i class="fa-solid ${evt.success ? 'fa-check-circle' : 'fa-times-circle'}"></i>
                <strong>${evt.tool}</strong> — ${evt.success ? 'OK' : 'Error'} (${evt.elapsed || 0}s)
                <i class="fa-solid fa-chevron-down toggle-icon"></i>
            </div>
            <pre class="tool-result-body">${escapeHtml(resultPreview)}</pre>
        `;
        contentDiv.appendChild(resultBadge);
        scrollChat();
    }

    if (evt.type === 'retry') {
        const retryBadge = document.createElement('div');
        retryBadge.className = 'agent-tool-badge retry';
        retryBadge.innerHTML = `<i class="fa-solid fa-rotate-right"></i> Retry #${evt.attempt}: ${evt.tool} — ${escapeHtml((evt.error || '').slice(0, 150))}`;
        contentDiv.appendChild(retryBadge);
    }

    if (evt.type === 'loop_warn') {
        const warnBadge = document.createElement('div');
        warnBadge.className = 'agent-tool-badge warning';
        warnBadge.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(evt.text || '')}`;
        contentDiv.appendChild(warnBadge);
    }

    if (evt.type === 'compressed') {
        appendSystemMessage('🗜️ ' + (evt.text || 'Context compressed'));
    }

    if (evt.type === 'phase') {
        updateStepBar(evt.phase || 'Working', evt.label || '');
    }

    if (evt.type === 'stopped') {
        appendSystemMessage('⏹️ ' + (evt.message || 'Agent stopped'));
    }

    if (evt.type === 'mode') {
        // Show mode badge in the step bar
        const bar = document.getElementById('agentStepBar');
        if (bar) {
            let tag = document.getElementById('agentModeTag');
            if (!tag) {
                tag = document.createElement('span');
                tag.id = 'agentModeTag';
                tag.style.cssText = 'margin-left:8px;padding:2px 8px;border-radius:12px;font-size:0.75rem;font-weight:600;background:rgba(88,166,255,0.12);color:#58a6ff;border:1px solid rgba(88,166,255,0.25);';
                bar.appendChild(tag);
            }
            tag.textContent = `${evt.emoji || ''} ${evt.label || evt.mode}`;
            tag.style.display = 'inline-block';
        }
        // Also show in chat
        const modeHint = evt.hint ? ` — ${evt.hint}` : '';
        const modeBadge = document.createElement('div');
        modeBadge.className = 'agent-tool-badge';
        modeBadge.style.cssText = 'opacity:0.7;font-size:12px;';
        modeBadge.innerHTML = `<i class="fa-solid fa-tag"></i> Mode: <strong>${evt.emoji || ''} ${evt.label || evt.mode}</strong>${escapeHtml(modeHint)}`;
        contentDiv.appendChild(modeBadge);
        scrollChat();
    }
}

// ─── Message Rendering ──────────────────────────────────────────────────────
function appendMessage(text, role) {
    const wrap = document.createElement('div');
    wrap.className = `message-wrap ${role}-msg`;

    const avatar = role === 'user' ? '<i class="fa-solid fa-user"></i>' : '<i class="fa-solid fa-robot"></i>';
    const roleName = role === 'user' ? 'You' : 'NEXUS Agent';
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    wrap.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            <div class="message-meta">
                <span class="message-role">${roleName}</span>
                <span class="message-time">${time}</span>
            </div>
            <div class="message-content" data-md="${escapeHtml(text)}">${role === 'user' ? escapeHtml(text) : ''}</div>
            <div class="message-actions">
                <button onclick="copyMessageContent(this)" title="Copy"><i class="fa-solid fa-copy"></i></button>
                ${role === 'agent' ? '<button onclick="retryLastMessage()" title="Retry"><i class="fa-solid fa-rotate-right"></i></button>' : ''}
            </div>
        </div>
    `;

    const container = document.getElementById('chatMessages');
    container.appendChild(wrap);
    scrollChat();

    return wrap.querySelector('.message-content');
}

function appendSystemMessage(text) {
    const el = document.createElement('div');
    el.className = 'message-wrap system-msg';
    el.innerHTML = `<div class="message-body"><div class="message-content system-content">${text}</div></div>`;
    document.getElementById('chatMessages').appendChild(el);
    scrollChat();
}

function scrollChat() {
    const container = document.getElementById('chatMessages');
    if (container) container.scrollTop = container.scrollHeight;
}

function copyMessageContent(btn) {
    const content = btn.closest('.message-body')?.querySelector('.message-content');
    if (content) {
        navigator.clipboard.writeText(content.textContent).then(() => {
            showToast('Copied to clipboard', 'success');
        });
    }
}

function retryLastMessage() {
    if (NX.chatHistory.length < 2) return;
    const lastUser = [...NX.chatHistory].reverse().find(m => m.role === 'user');
    if (lastUser) {
        document.getElementById('chatPrompt').value = lastUser.content;
        sendPrompt();
    }
}

// ─── Agent State ─────────────────────────────────────────────────────────────
function setAgentWorking(isWorking) {
    NX.isAgentRunning = isWorking;

    const stepBar = document.getElementById('agentStepBar');
    const crhText = document.getElementById('crhStatusText');
    const crhDot = document.getElementById('crhDot');
    const agentText = document.getElementById('agentStatusText');
    const agentDot = document.getElementById('agentDot');
    const stopBtn = document.getElementById('stopBtn');
    const sendBtn = document.getElementById('sendBtn');
    const sbActivity = document.getElementById('sbActivity');
    const sbDot = document.getElementById('sbActivityDot');
    const statusPill = document.getElementById('agentStatusPill');

    if (isWorking) {
        if (stepBar) stepBar.style.display = '';
        if (crhText) crhText.textContent = 'Working…';
        if (crhDot) crhDot.className = 'crh-dot amber pulse';
        if (agentText) agentText.textContent = 'Working…';
        if (agentDot) agentDot.className = 'agent-dot pulse';
        if (stopBtn) stopBtn.style.display = '';
        if (sendBtn) sendBtn.disabled = true;
        if (sbActivity) sbActivity.textContent = 'Agent working…';
        if (sbDot) sbDot.className = 'sb-activity-dot pulse';
        if (statusPill) { statusPill.textContent = 'Working'; statusPill.className = 'agent-status-pill working'; }
        document.body.classList.add('agent-is-working');
    } else {
        if (stepBar) stepBar.style.display = 'none';
        if (crhText) crhText.textContent = 'Ready';
        if (crhDot) crhDot.className = 'crh-dot green';
        if (agentText) agentText.textContent = 'Agent Ready';
        if (agentDot) agentDot.className = 'agent-dot';
        if (stopBtn) stopBtn.style.display = 'none';
        if (sendBtn) sendBtn.disabled = false;
        if (sbActivity) sbActivity.textContent = 'Agent ready';
        if (sbDot) sbDot.className = 'sb-activity-dot';
        if (statusPill) { statusPill.textContent = 'Ready'; statusPill.className = 'agent-status-pill'; }
        document.body.classList.remove('agent-is-working');
        hideStepToolBadge();
    }
}

function updateStepBar(phase, label) {
    const phaseEl = document.getElementById('stepPhase');
    const labelEl = document.getElementById('stepLabel');
    if (phaseEl) phaseEl.textContent = phase;
    if (labelEl) labelEl.textContent = label;

    // Animate progress
    const fill = document.getElementById('stepProgressFill');
    if (fill) {
        const w = Math.min(95, parseFloat(fill.style.width || '5') + Math.random() * 15);
        fill.style.width = w + '%';
    }
}

function showStepToolBadge(toolName) {
    const badge = document.getElementById('stepToolBadge');
    const name = document.getElementById('stepToolName');
    if (badge) badge.style.display = '';
    if (name) name.textContent = toolName;
}

function hideStepToolBadge() {
    const badge = document.getElementById('stepToolBadge');
    if (badge) badge.style.display = 'none';
    const fill = document.getElementById('stepProgressFill');
    if (fill) fill.style.width = '0%';
}

// ─── Agent Controls ──────────────────────────────────────────────────────────
async function stopAgent() {
    try {
        await fetch('/api/chat/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: NX.sessionId })
        });
        showToast('Stop signal sent', 'warning');
        setAgentWorking(false);
    } catch { showToast('Could not send stop signal', 'error'); }
}

function pauseAgent() {
    const btn = document.getElementById('pauseAgentBtn');
    if (btn) {
        const isPaused = btn.textContent.includes('Resume');
        btn.innerHTML = isPaused
            ? '<i class="fa-solid fa-pause"></i> Pause'
            : '<i class="fa-solid fa-play"></i> Resume';
        showToast(isPaused ? 'Agent resumed' : 'Agent paused', 'info');
    }
}

async function resetAgent() {
    const ok = await showConfirm('Reset Agent', 'Clear all context and start fresh?');
    if (!ok) return;
    try {
        await fetch(`/api/session/${NX.sessionId}/clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ clear_memory: true })
        });
        clearChat();
        NX.chatHistory = [];
        NX.tokenStats = { input: 0, output: 0, total: 0, cost: 0 };
        NX.turnCount = 0;
        NX.toolCallCount = 0;
        updateAgentStats();
        showToast('Agent context reset', 'success');
    } catch { showToast('Failed to reset agent', 'error'); }
}

async function newSession() {
    const ok = await showConfirm('New Session', 'Start a new session? Context will be cleared.');
    if (!ok) return;
    try {
        await fetch(`/api/session/${NX.sessionId}/clear`, { method: 'POST' });
    } catch { }
    NX.sessionId = crypto.randomUUID?.() || Math.random().toString(36).slice(2);
    localStorage.setItem('nexus_session_id', NX.sessionId);
    clearChat();
    NX.chatHistory = [];
    NX.turnCount = 0;
    NX.toolCallCount = 0;
    updateAgentStats();
    showToast('New session started', 'success');
}

function clearChat() {
    const msgs = document.getElementById('chatMessages');
    if (msgs) {
        msgs.innerHTML = `
            <div class="welcome-card" id="welcomeCard">
                <div class="wc-glyph">⬡</div>
                <h2>NEXUS <span class="wc-accent">IDE</span></h2>
                <p class="wc-sub">Session cleared. Ready for new instructions.</p>
            </div>`;
    }
    const qc = document.getElementById('quickChips');
    if (qc) qc.style.display = '';
}

// ─── Agent Mode ──────────────────────────────────────────────────────────────
function setAgentMode(mode) {
    NX.agentMode = mode;
    document.querySelectorAll('.mode-pill').forEach(p => {
        p.classList.toggle('active', p.dataset.mode === mode);
    });
    showToast(`Agent mode: ${mode}`, 'info');
}

// ─── Task Mode Selector ──────────────────────────────────────────────────────
function setTaskMode(mode, btn) {
    NX.taskMode = mode;
    localStorage.setItem('nexus_task_mode', mode);
    // Update all buttons
    document.querySelectorAll('.task-mode-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
    });
    // Update textarea placeholder
    const ta = document.getElementById('chatPrompt');
    if (ta) {
        const hints = {
            '':           'Describe what to build, ask a question, or say \'@filename\' to reference a file\u2026',
            'builder':    '\uD83C\uDFD7\uFE0F Builder mode — describe what to create or add\u2026',
            'debugger':   '\uD83D\uDD0D Debugger mode — describe the bug or paste error output\u2026',
            'refactorer': '\u267B\uFE0F Refactorer mode — describe what to clean up or improve\u2026',
            'researcher': '\uD83D\uDCDA Researcher mode — ask a question about the codebase\u2026',
            'reviewer':   '\uD83D\uDC41\uFE0F Reviewer mode — ask for a code review or audit\u2026',
        };
        ta.placeholder = hints[mode] || hints[''];
    }
    const labels = { '': 'Auto', builder: 'Builder', debugger: 'Debugger', refactorer: 'Refactorer', researcher: 'Researcher', reviewer: 'Reviewer' };
    if (mode) showToast(`Task mode: ${labels[mode] || mode}`, 'info');
}

// ─── Restore saved task mode on page load ────────────────────────────────────
document.addEventListener('DOMContentLoaded', function restoreTaskMode() {
    const saved = NX.taskMode;
    if (saved) {
        const btn = document.querySelector(`.task-mode-btn[data-mode="${saved}"]`);
        if (btn) setTaskMode(saved, btn);
    }
});

// ─── Ultra Mode ──────────────────────────────────────────────────────────────
async function toggleUltraMode() {
    NX.ultraMode = !NX.ultraMode;
    localStorage.setItem('nexus_ultra', NX.ultraMode ? '1' : '0');

    const badge = document.getElementById('ultraBadge');
    const toggle = document.getElementById('ultraToggle');
    const sbUltra = document.getElementById('sbUltra');

    if (badge) badge.style.display = NX.ultraMode ? 'inline-flex' : 'none';
    if (toggle) toggle.checked = NX.ultraMode;
    if (sbUltra) sbUltra.innerHTML = NX.ultraMode ? '<i class="fa-solid fa-bolt"></i> Ultra' : '<i class="fa-solid fa-bolt"></i> Standard';

    try {
        await fetch('/api/mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: NX.ultraMode ? 'ultra' : 'standard' })
        });
    } catch { }

    showToast(NX.ultraMode ? '⚡ Ultra Mode enabled' : 'Standard mode', NX.ultraMode ? 'success' : 'info');
}

// ─── Tools & Constraints ─────────────────────────────────────────────────────
function toggleTool(tool, enabled) {
    NX.enabledTools[tool] = enabled;
    showToast(`${tool}: ${enabled ? 'enabled' : 'disabled'}`, 'info');
}

function setConstraint(key, value) {
    NX.constraints[key] = value;
}

// ─── Quick Prompts ───────────────────────────────────────────────────────────
function quickPrompt(txt) {
    const p = document.getElementById('chatPrompt');
    if (p) {
        p.value = txt;
        sendPrompt();
    }
}

function onPromptKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendPrompt();
    }
}

function onPromptInput(el) {
    // Auto-resize handled in nexus.js
}

// ─── Attachments ─────────────────────────────────────────────────────────────
function attachFiles() {
    document.getElementById('attachInput')?.click();
}

function handleAttachments(files) {
    for (const file of files) {
        const reader = new FileReader();
        reader.onload = (e) => {
            NX.attachments.push({ name: file.name, content: e.target.result });
            renderAttachments();
        };
        reader.readAsText(file);
    }
}

function renderAttachments() {
    const bar = document.getElementById('attachmentBar');
    const list = document.getElementById('attachmentList');
    if (NX.attachments.length === 0) {
        if (bar) bar.style.display = 'none';
        return;
    }
    if (bar) bar.style.display = '';
    if (list) {
        list.innerHTML = NX.attachments.map((a, i) => `
            <div class="attachment-item">
                <i class="fa-solid fa-paperclip"></i>
                <span>${a.name}</span>
                <button onclick="removeAttachment(${i})"><i class="fa-solid fa-xmark"></i></button>
            </div>
        `).join('');
    }
}

function removeAttachment(idx) {
    NX.attachments.splice(idx, 1);
    renderAttachments();
}

function clearAttachments() {
    NX.attachments = [];
    renderAttachments();
}

function insertCodeSnippet() {
    const p = document.getElementById('chatPrompt');
    if (p) p.value += '\n```\n\n```';
    showToast('Code block inserted', 'info');
}

function insertFileRef() {
    const p = document.getElementById('chatPrompt');
    if (p && NX.currentFile) {
        p.value += `@${NX.currentFile} `;
        p.focus();
    } else {
        showToast('No file open to reference', 'warning');
    }
}

function insertImageRef() {
    showToast('Image reference: coming soon', 'info');
}

// ─── Memory & Stats ──────────────────────────────────────────────────────────
async function showMemory() {
    showModal('memoryModal');

    document.getElementById('memMsgs').textContent = NX.chatHistory.length;
    document.getElementById('memTokens').textContent = NX.tokenStats.total.toLocaleString();
    document.getElementById('memCost').textContent = '$' + NX.tokenStats.cost.toFixed(4);
    document.getElementById('memTools').textContent = NX.toolCallCount;

    try {
        const res = await fetch('/api/session/memory');
        const data = await res.json();
        const viewer = document.getElementById('memoryViewer');
        if (viewer) {
            viewer.innerHTML = typeof marked !== 'undefined'
                ? marked.parse(data.memory || 'No memories stored.')
                : `<pre>${data.memory || 'No memories stored.'}</pre>`;
        }
    } catch { }
}

async function clearMemory() {
    try {
        await fetch('/api/session/memory', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: '' })
        });
        showToast('Context memory cleared!', 'success');
        hideModal('memoryModal');
        document.getElementById('memorySummary').textContent = 'No memories yet.';
    } catch { showToast('Failed to clear memory', 'error'); }
}

function trimContext() {
    if (NX.chatHistory.length > 6) {
        NX.chatHistory = NX.chatHistory.slice(-6);
        showToast('Trimmed oldest messages', 'info');
    } else {
        showToast('Context is already minimal', 'info');
    }
    updateContextBudget();
}

function updateContextBudget() {
    const pct = Math.min(100, (NX.tokenStats.total / NX.contextLimit) * 100);
    const fill = document.getElementById('cbbFill');
    const count = document.getElementById('cbbCount');
    const tokenDisplay = document.getElementById('tokenDisplay');

    if (fill) fill.style.width = pct + '%';
    if (count) count.textContent = `${NX.tokenStats.total.toLocaleString()} / ${(NX.contextLimit / 1000).toFixed(0)}k tokens`;
    if (tokenDisplay) tokenDisplay.textContent = `${(NX.tokenStats.total / 1000).toFixed(1)}k / ${(NX.contextLimit / 1000).toFixed(0)}k`;
}

function updateAgentStats() {
    document.getElementById('astat-turns').textContent = NX.turnCount;
    document.getElementById('astat-tokens').textContent = NX.tokenStats.total.toLocaleString();
    document.getElementById('astat-tools').textContent = NX.toolCallCount;
    document.getElementById('astat-cost').textContent = '$' + NX.tokenStats.cost.toFixed(2);
    updateContextBudget();
}

function showTokenStats() {
    showModal('tokenStatsModal');
    const bd = document.getElementById('tokenBreakdown');
    if (bd) {
        bd.innerHTML = `
            <div class="token-stat-row"><span>Input tokens</span><span>${NX.tokenStats.input.toLocaleString()}</span></div>
            <div class="token-stat-row"><span>Output tokens</span><span>${NX.tokenStats.output.toLocaleString()}</span></div>
            <div class="token-stat-row"><span>Total tokens</span><span>${NX.tokenStats.total.toLocaleString()}</span></div>
            <div class="token-stat-row"><span>Estimated cost</span><span>$${NX.tokenStats.cost.toFixed(4)}</span></div>
            <div class="token-stat-row"><span>Turns</span><span>${NX.turnCount}</span></div>
            <div class="token-stat-row"><span>Tool calls</span><span>${NX.toolCallCount}</span></div>
            <div class="token-stat-row"><span>Model</span><span>${NX.activeModel}</span></div>
            <div class="token-stat-row"><span>Mode</span><span>${NX.ultraMode ? 'Ultra' : 'Standard'}</span></div>
        `;
    }
}

// ─── Chat Export ─────────────────────────────────────────────────────────────
function exportChat() {
    const content = NX.chatHistory.map(m =>
        `[${m.role.toUpperCase()}] ${new Date(m.time).toLocaleString()}\n${m.content}\n`
    ).join('\n---\n\n');

    const blob = new Blob([content], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `nexus-chat-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    showToast('Chat exported', 'success');
}

function showAgentConfig() { showSettings(); switchSettingsPage('agent'); }

// ═══════════════════════════════════════════════════════════════════════════════
// MULTI-AGENT SWARM MODE
// ═══════════════════════════════════════════════════════════════════════════════

NX.swarmMode = false;
NX.swarmAgents = {};  // { agent_id: { role, name, emoji, color, status, output } }

function toggleSwarmMode() {
    NX.swarmMode = !NX.swarmMode;
    const badge = document.getElementById('swarmBadge');
    if (badge) badge.style.display = NX.swarmMode ? 'inline-flex' : 'none';
    const toggle = document.getElementById('swarmToggle');
    if (toggle) toggle.checked = NX.swarmMode;
    showToast(NX.swarmMode ? '🐝 Swarm Mode enabled — agents run in parallel' : 'Single agent mode', NX.swarmMode ? 'success' : 'info');
}

function spawnAgent() {
    if (!NX.swarmMode) {
        NX.swarmMode = true;
        const badge = document.getElementById('swarmBadge');
        if (badge) badge.style.display = 'inline-flex';
    }
    showToast('🐝 Swarm Mode active — next prompt will spawn multiple agents', 'success');
}

async function sendSwarmPrompt() {
    const el = document.getElementById('chatPrompt');
    const text = el.value.trim();
    if (!text || NX.isAgentRunning) return;

    el.value = '';
    el.style.height = 'auto';
    const cc = document.getElementById('charCount');
    if (cc) cc.textContent = '0';

    const wc = document.getElementById('welcomeCard');
    if (wc) wc.style.display = 'none';
    const qc = document.getElementById('quickChips');
    if (qc) qc.style.display = 'none';

    appendMessage(text, 'user');
    NX.chatHistory.push({ role: 'user', content: text, time: Date.now() });
    setAgentWorking(true);
    NX.swarmAgents = {};

    try {
        const res = await fetch('/api/multi-agent/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                prompt: text,
                session_id: NX.sessionId,
                max_agents: 3,
            })
        });

        // Create swarm container in chat
        const swarmWrap = document.createElement('div');
        swarmWrap.className = 'swarm-container';
        swarmWrap.id = 'activeSwarm';
        swarmWrap.innerHTML = `
            <div class="swarm-header">
                <span class="swarm-icon">🐝</span>
                <span class="swarm-title">Multi-Agent Swarm</span>
                <span class="swarm-status" id="swarmStatus">Initializing...</span>
            </div>
            <div class="swarm-agents" id="swarmAgentCards"></div>
            <div class="swarm-output" id="swarmOutput"></div>
        `;
        document.getElementById('chatMessages').appendChild(swarmWrap);
        scrollChat();

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
                    handleSwarmEvent(evt);
                } catch (e) {}
            }
        }

        // Final summary
        const allFiles = Object.values(NX.swarmAgents).flatMap(a => a.files || []);
        const totalTurns = Object.values(NX.swarmAgents).reduce((s, a) => s + (a.turns || 0), 0);

        const summaryDiv = document.createElement('div');
        summaryDiv.className = 'swarm-summary';
        summaryDiv.innerHTML = `
            <div class="ss-header"><i class="fa-solid fa-check-double"></i> Swarm Complete</div>
            <div class="ss-stats">
                <span><strong>${Object.keys(NX.swarmAgents).length}</strong> agents</span>
                <span><strong>${totalTurns}</strong> total turns</span>
                <span><strong>${[...new Set(allFiles)].length}</strong> files changed</span>
            </div>
        `;
        const swarmEl = document.getElementById('activeSwarm');
        if (swarmEl) swarmEl.appendChild(summaryDiv);

        NX.chatHistory.push({ role: 'assistant', content: `[Swarm completed: ${Object.keys(NX.swarmAgents).length} agents, ${totalTurns} turns]`, time: Date.now() });
        await loadFiles();

    } catch (e) {
        showToast('Swarm error: ' + e.message, 'error');
    }

    setAgentWorking(false);
}

function handleSwarmEvent(evt) {
    const cards = document.getElementById('swarmAgentCards');
    const output = document.getElementById('swarmOutput');
    const statusEl = document.getElementById('swarmStatus');

    switch (evt.type) {
        case 'orchestrator_phase':
            if (statusEl) statusEl.textContent = evt.text;
            updateStepBar(evt.phase, evt.text);
            break;

        case 'orchestrator_plan':
            if (statusEl) statusEl.textContent = `${evt.total_agents} agents planned`;
            if (cards) {
                cards.innerHTML = evt.tasks.map(t => {
                    const role = _swarmRoleInfo(t.role);
                    return `
                        <div class="sa-card" id="sa-${t.id}" style="border-left: 3px solid ${role.color}">
                            <div class="sa-header">
                                <span class="sa-emoji">${role.emoji}</span>
                                <span class="sa-name">${role.name}</span>
                                <span class="sa-status sa-pending">Queued</span>
                            </div>
                            <div class="sa-desc">${t.description}</div>
                            <div class="sa-progress"><div class="sa-progress-fill" id="sapf-${t.id}"></div></div>
                        </div>
                    `;
                }).join('');
            }
            break;

        case 'agent_start':
            NX.swarmAgents[evt.agent_id] = {
                role: evt.role, name: evt.role_name, emoji: evt.emoji,
                color: evt.color, status: 'running', output: '', files: [], turns: 0
            };
            _updateAgentCard(evt.agent_id, 'Running', 'sa-running');
            break;

        case 'agent_thinking':
            _updateAgentCard(evt.agent_id, evt.text, 'sa-running');
            _advanceProgress(evt.agent_id);
            break;

        case 'agent_token':
            if (NX.swarmAgents[evt.agent_id]) {
                NX.swarmAgents[evt.agent_id].output += evt.text + '\n';
            }
            _appendSwarmOutput(evt.agent_id, evt.text);
            break;

        case 'agent_plan':
            _appendSwarmOutput(evt.agent_id, `📋 Plan:\n${evt.text}`);
            break;

        case 'agent_tool':
            _updateAgentCard(evt.agent_id, `🔧 ${evt.tool}`, 'sa-running');
            NX.toolCallCount++;
            updateAgentStats();
            break;

        case 'agent_tool_result':
            _advanceProgress(evt.agent_id);
            // Refresh files on file operations
            if (['FileEditTool', 'FilePatchTool', 'FileDeleteTool', 'BashTool'].includes(evt.tool)) {
                loadFiles();
            }
            break;

        case 'agent_error':
            _updateAgentCard(evt.agent_id, 'Error', 'sa-error');
            _appendSwarmOutput(evt.agent_id, `❌ Error: ${evt.message}`);
            break;

        case 'agent_done':
            if (NX.swarmAgents[evt.agent_id]) {
                NX.swarmAgents[evt.agent_id].status = 'done';
                NX.swarmAgents[evt.agent_id].files = evt.files_changed || [];
                NX.swarmAgents[evt.agent_id].turns = evt.turns_used || 0;
            }
            _updateAgentCard(evt.agent_id, `Done (${evt.turns_used}t, ${evt.elapsed_ms}ms)`, 'sa-done');
            _setProgress(evt.agent_id, 100);
            break;

        case 'orchestrator_done':
            if (statusEl) statusEl.textContent = 'Complete ✓';
            NX.turnCount += evt.total_turns || 0;
            updateAgentStats();
            break;
    }
}

function _swarmRoleInfo(role) {
    const defaults = { emoji: '🤖', name: role, color: '#58a6ff' };
    const map = {
        architect: { emoji: '🏗️', name: 'Architect', color: '#58a6ff' },
        coder: { emoji: '💻', name: 'Coder', color: '#3fb950' },
        reviewer: { emoji: '🔍', name: 'Reviewer', color: '#d29922' },
        terminal: { emoji: '⬛', name: 'Terminal', color: '#bc8cff' },
        researcher: { emoji: '📚', name: 'Researcher', color: '#f85149' },
    };
    return map[role] || defaults;
}

function _updateAgentCard(agentId, status, cls) {
    const card = document.getElementById('sa-' + agentId);
    if (!card) return;
    const statusEl = card.querySelector('.sa-status');
    if (statusEl) {
        statusEl.textContent = status;
        statusEl.className = 'sa-status ' + (cls || '');
    }
}

function _advanceProgress(agentId) {
    const fill = document.getElementById('sapf-' + agentId);
    if (fill) {
        const w = Math.min(95, parseFloat(fill.style.width || '5') + Math.random() * 20);
        fill.style.width = w + '%';
    }
}

function _setProgress(agentId, pct) {
    const fill = document.getElementById('sapf-' + agentId);
    if (fill) fill.style.width = pct + '%';
}

function _appendSwarmOutput(agentId, text) {
    const output = document.getElementById('swarmOutput');
    if (!output) return;
    const agent = NX.swarmAgents[agentId];
    const el = document.createElement('div');
    el.className = 'swarm-output-line';
    el.innerHTML = `<span class="sol-agent" style="color:${agent?.color || '#58a6ff'}">${agent?.emoji || '🤖'} ${agent?.name || agentId}</span> ${escapeHtml(text)}`;
    output.appendChild(el);
    output.scrollTop = output.scrollHeight;
    scrollChat();
}
