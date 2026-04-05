'use strict';

/* ═══════════════════════════════════════════════════════════════════════════════
   TERMINAL.JS — Terminal emulator, command execution, multi-tab
   ═══════════════════════════════════════════════════════════════════════════════ */

let _xterm = null;
let _fitAddon = null;
let _xtermInited = false;

// ─── Initialize Terminal ─────────────────────────────────────────────────────
function initTerminal() {
    if (_xtermInited) return;

    const container = document.getElementById('xtermContainer');
    if (!container) return;

    // Try xterm.js first
    if (typeof Terminal !== 'undefined') {
        try {
            _xterm = new Terminal({
                theme: {
                    background: '#0d1117',
                    foreground: '#c9d1d9',
                    cyan: '#00d4ff',
                    green: '#3fb950',
                    red: '#f85149',
                    yellow: '#d29922',
                    blue: '#58a6ff',
                    magenta: '#bc8cff',
                },
                fontFamily: "'Space Mono', 'Fira Code', monospace",
                fontSize: 14.5,
                cursorBlink: true,
                cursorStyle: 'bar',
                scrollback: 5000,
            });
            _xterm.open(container);

            // Load FitAddon
            if (typeof FitAddon !== 'undefined') {
                _fitAddon = new FitAddon.FitAddon();
                _xterm.loadAddon(_fitAddon);
                setTimeout(() => _fitAddon.fit(), 100);
            }

            _xterm.writeln('\x1b[36m⬡ NEXUS IDE Terminal\x1b[0m');
            _xterm.writeln('\x1b[90m  Type commands below or use the simple terminal fallback.\x1b[0m');
            _xterm.writeln('');

            let cmdBuffer = '';
            _xterm.onData(data => {
                if (data === '\r') {
                    _xterm.writeln('');
                    if (cmdBuffer.trim()) {
                        executeTerminalCommand(cmdBuffer.trim(), (output) => {
                            output.split('\n').forEach(line => _xterm.writeln(line));
                            _xterm.write('\x1b[36m❯\x1b[0m ');
                        });
                    } else {
                        _xterm.write('\x1b[36m❯\x1b[0m ');
                    }
                    cmdBuffer = '';
                } else if (data === '\x7f') { // backspace
                    if (cmdBuffer.length > 0) {
                        cmdBuffer = cmdBuffer.slice(0, -1);
                        _xterm.write('\b \b');
                    }
                } else if (data >= ' ') {
                    cmdBuffer += data;
                    _xterm.write(data);
                }
            });

            _xterm.write('\x1b[36m❯\x1b[0m ');

            // Resize listener
            window.addEventListener('resize', () => {
                if (_fitAddon) _fitAddon.fit();
            });

            _xtermInited = true;
            return;
        } catch (e) {
            console.warn('xterm.js init failed, falling back to simple terminal');
        }
    }

    // Fallback: show simple terminal
    container.style.display = 'none';
    const simple = document.getElementById('simpleTerminal');
    if (simple) simple.style.display = '';
    _xtermInited = true;
}

// ─── Execute Command ─────────────────────────────────────────────────────────
async function executeTerminalCommand(command, callback) {
    try {
        const res = await fetch('/api/terminal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
        });
        const data = await res.json();
        const output = data.output || data.message || 'No output';

        // Log to tool log
        logToolCall({
            type: 'tool_call',
            tool: 'BashExec',
            summary: command,
            result: output.slice(0, 200),
        });

        if (callback) callback(output);
        return output;
    } catch (e) {
        const errorMsg = 'Error: ' + e.message;
        if (callback) callback(errorMsg);
        return errorMsg;
    }
}

// ─── Simple Terminal ─────────────────────────────────────────────────────────
function stermKeyDown(event) {
    const input = document.getElementById('stermInput');
    if (event.key === 'Enter') {
        event.preventDefault();
        runStermCmd();
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (NX.termIdx < NX.termHistory.length - 1) {
            NX.termIdx++;
            input.value = NX.termHistory[NX.termHistory.length - 1 - NX.termIdx] || '';
        }
    } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (NX.termIdx > 0) {
            NX.termIdx--;
            input.value = NX.termHistory[NX.termHistory.length - 1 - NX.termIdx] || '';
        } else {
            NX.termIdx = -1;
            input.value = '';
        }
    }
}

async function runStermCmd() {
    const input = document.getElementById('stermInput');
    const output = document.getElementById('stermOutput');
    const cmd = input.value.trim();
    if (!cmd) return;

    // Add to history
    NX.termHistory.push(cmd);
    NX.termIdx = -1;

    // Show command
    const cmdDiv = document.createElement('div');
    cmdDiv.className = 'sterm-cmd';
    cmdDiv.innerHTML = `<span class="sterm-prompt">❯</span> <span class="sterm-cmd-text">${escapeHtml(cmd)}</span>`;
    output.appendChild(cmdDiv);

    input.value = '';
    input.disabled = true;

    // Handle built-in commands
    if (cmd === 'clear') {
        output.innerHTML = '<div class="sterm-welcome">NEXUS IDE — Terminal cleared</div>';
        input.disabled = false;
        input.focus();
        return;
    }

    const result = await executeTerminalCommand(cmd);

    const resultDiv = document.createElement('div');
    resultDiv.className = 'sterm-result';
    resultDiv.innerHTML = `<pre>${escapeHtml(result)}</pre>`;
    output.appendChild(resultDiv);
    output.scrollTop = output.scrollHeight;

    input.disabled = false;
    input.focus();
}

// ─── Terminal Tabs ───────────────────────────────────────────────────────────
function switchTermTab(id) {
    NX.activeTermTab = id;
    document.querySelectorAll('.term-tab').forEach(t => t.classList.remove('active'));
    const tab = document.getElementById('term-tab-' + id);
    if (tab) tab.classList.add('active');
}

function newTermTab() {
    NX.termCounter++;
    const id = NX.termCounter;
    NX.termTabs.push({ id, name: `bash-${id}`, output: [] });

    const scroll = document.getElementById('termTabsScroll');
    if (scroll) {
        const btn = document.createElement('button');
        btn.className = 'term-tab';
        btn.id = 'term-tab-' + id;
        btn.onclick = () => switchTermTab(id);
        btn.innerHTML = `<i class="fa-solid fa-terminal"></i> bash-${id}
            <span onclick="closeTermTab(event,${id})" class="term-tab-close"><i class="fa-solid fa-xmark"></i></span>`;
        scroll.appendChild(btn);
    }
    switchTermTab(id);
    showToast(`Terminal ${id} created`, 'info');
}

function closeTermTab(event, id) {
    event.stopPropagation();
    NX.termTabs = NX.termTabs.filter(t => t.id !== id);
    const tab = document.getElementById('term-tab-' + id);
    if (tab) tab.remove();
    if (NX.activeTermTab === id && NX.termTabs.length > 0) {
        switchTermTab(NX.termTabs[0].id);
    }
}

function clearTerminal() {
    if (_xterm) {
        _xterm.clear();
        _xterm.write('\x1b[36m❯\x1b[0m ');
    }
    const output = document.getElementById('stermOutput');
    if (output) output.innerHTML = '<div class="sterm-welcome">NEXUS IDE — Terminal cleared</div>';
}

function killTerminal() {
    clearTerminal();
    showToast('Terminal process killed', 'warning');
}

function splitTerminal() {
    showToast('Split terminal coming soon', 'info');
}

// ─── Run/Debug ───────────────────────────────────────────────────────────────
async function runProject() {
    showToast('Running project...', 'info');
    switchRightTab('terminal');

    // Try to detect the project type and run appropriate command
    let cmd = 'echo "No run configuration found. Set up in Run > Run Configurations."';

    // Check for common entry points
    const files = NX.allFiles.map(f => typeof f === 'object' ? f.path : f);
    if (files.includes('package.json')) cmd = 'npm start';
    else if (files.includes('main.py')) cmd = 'python main.py';
    else if (files.includes('app.py')) cmd = 'python app.py';
    else if (files.includes('index.html')) { refreshPreview(); return; }
    else if (files.includes('Makefile')) cmd = 'make run';
    else if (files.includes('Cargo.toml')) cmd = 'cargo run';

    const result = await executeTerminalCommand(cmd);
    appendOutputLine(result, 'app');
}

async function runCurrentFile() {
    if (!NX.currentFile) { showToast('No file open', 'warning'); return; }
    switchRightTab('terminal');

    const ext = NX.currentFile.split('.').pop().toLowerCase();
    let cmd;
    if (ext === 'py') cmd = `python ${NX.currentFile}`;
    else if (ext === 'js') cmd = `node ${NX.currentFile}`;
    else if (ext === 'sh') cmd = `bash ${NX.currentFile}`;
    else if (ext === 'rs') cmd = `rustc ${NX.currentFile} && ./${NX.currentFile.replace('.rs', '')}`;
    else if (ext === 'html') { refreshPreview(); return; }
    else { showToast(`Cannot run .${ext} files directly`, 'warning'); return; }

    const result = await executeTerminalCommand(cmd);
    appendOutputLine(result, 'app');
    showToast(`Ran: ${NX.currentFile}`, 'success');
}

function debugProject() { showToast('Debug mode starting...', 'info'); togglePanel('debug'); }
function debugStepOver() { showToast('Step Over', 'info'); }
function debugStepInto() { showToast('Step Into', 'info'); }
function debugStepOut() { showToast('Step Out', 'info'); }
function stopDebug() { showToast('Debug stopped', 'warning'); }
function addWatch() {
    const input = document.getElementById('watchInput');
    const expr = input?.value?.trim();
    if (!expr) return;
    const watchList = document.getElementById('watchList');
    if (watchList) {
        watchList.innerHTML = watchList.innerHTML.replace('<div class="debug-empty">Nothing watching</div>', '');
        watchList.innerHTML += `<div class="watch-item"><span>${escapeHtml(expr)}</span><span class="watch-value">undefined</span></div>`;
    }
    if (input) input.value = '';
}

function showRunConfig() { showToast('Run configurations: coming soon', 'info'); }
function showEnvManager() { showSettings(); switchSettingsPage('env'); }
function showDockerPanel() { togglePanel('docker'); }

// ─── Environment Variables ───────────────────────────────────────────────────
function addEnvVar() {
    const envList = document.getElementById('envList');
    if (!envList) return;
    const row = document.createElement('div');
    row.className = 'env-row';
    row.innerHTML = `
        <input type="text" placeholder="KEY" class="env-key" />
        <input type="text" placeholder="value" class="env-val" />
        <button onclick="this.parentElement.remove()" class="env-remove"><i class="fa-solid fa-trash"></i></button>
    `;
    envList.appendChild(row);
}
