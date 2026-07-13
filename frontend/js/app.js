/* ═══════════════════════════════════════════════════════════════════
   Main Application Controller
   Ties together WebSocket, UI, and API calls.
   ═══════════════════════════════════════════════════════════════════ */

let sessionToken = null;
let currentPanel = 'telemetry';
let pendingTaskTimeout = null;

// ═══════════════════════════════════════════════════════════════════
//  AUTH
// ═══════════════════════════════════════════════════════════════════

async function handleLogin() {
    const passphrase = document.getElementById('login-passphrase').value;
    if (!passphrase) return;

    const btn = document.getElementById('login-btn');
    btn.disabled = true;
    btn.textContent = 'Authenticating...';

    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ passphrase }),
        });

        const data = await res.json();

        if (res.ok && data.success) {
            sessionToken = data.token;
            UI.hideLogin();
            connectWebSocket();
            fetchStatus();
        } else {
            UI.showLoginError(true);
        }
    } catch (e) {
        UI.showLoginError(true);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Authenticate';
    }
}

// ═══════════════════════════════════════════════════════════════════
//  WEBSOCKET
// ═══════════════════════════════════════════════════════════════════

function connectWebSocket() {
    WS_MODULE.onMessage(handleWsMessage);
    WS_MODULE.onStatus((status) => {
        if (status === 'connected') {
            UI.setStatus('Connected', 'text-green-400');
        } else if (status === 'disconnected') {
            UI.setStatus('Disconnected', 'text-red-400');
        } else if (status === 'reconnecting') {
            UI.setStatus('Reconnecting...', 'text-yellow-400');
        }
    });
    WS_MODULE.connect(sessionToken);
}

function handleWsMessage(msg) {
    switch (msg.type) {
        case 'reply':
            UI.addChatMessage(msg.data, UI.mapColor(msg.color));
            break;

        case 'agent_turn':
            if (typeof msg.data === 'object') {
                UI.addTurnWidget(
                    msg.data.thought || '',
                    msg.data.tools || [],
                    msg.data.reply || ''
                );
            }
            break;

        case 'status':
            UI.setStatus(msg.data, msg.color === 'gray' ? 'text-gray-500' : 'text-green-400');
            break;

        case 'telemetry':
            if (typeof msg.data === 'object') {
                UI.updateTelemetry(
                    msg.data.phase || 'Scout',
                    msg.data.conf || 0,
                    msg.data.retries || 0
                );
                if (msg.data.target) UI.setTarget(msg.data.target);
            }
            break;

        case 'terminal':
        case 'tool_stream':
            UI.addToolStream(msg.data, msg.color);
            break;

        case 'context_viewer':
            UI.addContextMsg(msg.data, msg.color);
            break;

        case 'memory':
            UI.addContextMsg(msg.data, msg.color);
            break;

        case 'update_plan':
            UI.addContextMsg(`\nPLAN UPDATE:\n${msg.data}\n`, msg.color);
            break;

        case 'warning':
            UI.showWarning(msg.data);
            break;

        case 'unlock':
            if (pendingTaskTimeout) {
                clearTimeout(pendingTaskTimeout);
                pendingTaskTimeout = null;
            }
            UI.setInputEnabled(true);
            UI.setAllControlsEnabled(true);
            break;

        case 'workspace_refreshed':
            fetchFileTree();
            fetchAstMetadata();
            break;

        default:
            console.log('[WS] Unknown message type:', msg.type);
    }
}

// ═══════════════════════════════════════════════════════════════════
//  ACTIONS
// ═══════════════════════════════════════════════════════════════════

async function handleSendMessage() {
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!text) return;

    input.value = '';
    UI.addChatMessage(`You: ${text}`, 'text-blue-400');
    UI.setInputEnabled(false);
    UI.setAllControlsEnabled(false);
    UI.setStatus('Running', 'text-green-400');

    // Safety timeout: auto-unlock after 10 minutes to allow deep agent iterations
    if (pendingTaskTimeout) clearTimeout(pendingTaskTimeout);
    pendingTaskTimeout = setTimeout(function() {
        UI.addChatMessage('SYSTEM: Request timed out. The agent did not respond in time.', 'text-red-400');
        UI.setInputEnabled(true);
        UI.setAllControlsEnabled(true);
        UI.setStatus('Timeout', 'text-red-400');
        pendingTaskTimeout = null;
    }, 600000); // 10 minutes in milliseconds

    // Send via WebSocket if connected, else fallback to HTTP POST
    const sent = WS_MODULE.send({ type: 'send_message', message: text });
    if (sent) {
        return;  // WS will handle response asynchronously
    }
    // Fallback: HTTP POST
    try {
        const res = await fetch('/api/send', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${sessionToken}`,
            },
            body: JSON.stringify({ message: text }),
        });
        const data = await res.json();
        if (!res.ok) {
            UI.addChatMessage('SYSTEM: Failed to send message.', 'text-red-400');
            UI.setInputEnabled(true);
            UI.setAllControlsEnabled(true);
        }
    } catch (e) {
        UI.addChatMessage('SYSTEM: Connection error.', 'text-red-400');
        UI.setInputEnabled(true);
        UI.setAllControlsEnabled(true);
    }
}

async function handleSetWorkspace() {
    const pathInput = document.getElementById('workspace-path');
    const path = pathInput.value.trim();
    // Strip surrounding quotes (Windows "Copy as path" or manual quotes)
    const cleaned = path.replace(/^[\s'"`'"]+|[\s'"`'"]+$/g, '');
    if (!path) {
        UI.showToast('Enter a directory path', 'yellow');
        return;
    }

    const btn = document.getElementById('workspace-btn');
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        const res = await fetch('/api/workspace', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${sessionToken}`,
            },
            body: JSON.stringify({ path: cleaned }),
        });

        const data = await res.json();
        if (res.ok && data.success) {
            UI.showToast(data.message, 'green');
            fetchFileTree();
        } else {
            UI.showToast(data.detail || 'Failed to set workspace', 'red');
        }
    } catch (e) {
        UI.showToast('Connection error', 'red');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Set Workspace';
    }
}

function handleStop() {
    WS_MODULE.send({ type: 'stop' });
    UI.setStatus('Halted', 'text-red-400');
    UI.setInputEnabled(true);
    UI.setAllControlsEnabled(true);
}

function handleUndo() {
    WS_MODULE.send({ type: 'undo' });
}

function handleReset() {
    WS_MODULE.send({ type: 'reset' });
    UI.clearChat();
    UI.clearToolStream();
    UI.setStatus('Reset', 'text-yellow-400');
}

function handleLogout() {
    WS_MODULE.disconnect();
    sessionToken = null;
    UI.resetDashboard();
}

function switchPanel(panel) {
    currentPanel = panel;
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.panel === panel);
    });
    document.getElementById('telemetry-view').classList.toggle('hidden', panel !== 'telemetry');
    document.getElementById('explorer-view').classList.toggle('hidden', panel !== 'explorer');
    document.getElementById('settings-view').classList.toggle('hidden', panel !== 'settings');
}

// ═══════════════════════════════════════════════════════════════════
//  API CALLS
// ═══════════════════════════════════════════════════════════════════

async function fetchStatus() {
    try {
        const res = await fetch('/api/status', {
            headers: { 'Authorization': `Bearer ${sessionToken}` },
        });
        if (res.ok) {
            const data = await res.json();
            UI.updateTelemetry(data.phase, data.confidence, data.retries);
            UI.setStatus(data.is_processing ? 'Running' : 'Idle',
                data.is_processing ? 'text-green-400' : 'text-gray-500');
            if (data.target_directory) {
                document.getElementById('workspace-path').value = data.target_directory;
                fetchFileTree();
            }
        }
    } catch (e) {
        console.warn('Failed to fetch status:', e);
    }
}

async function fetchFileTree() {
    try {
        const res = await fetch('/api/explorer', {
            headers: { 'Authorization': `Bearer ${sessionToken}` },
        });
        if (res.ok) {
            const data = await res.json();
            UI.renderFileTree(data);
        }
    } catch (e) {
        console.warn('Failed to fetch file tree:', e);
    }
}

async function fetchAstMetadata() {
    try {
        const res = await fetch('/api/status', {
            headers: { 'Authorization': `Bearer ${sessionToken}` },
        });
        if (res.ok) {
            const data = await res.json();
            // AST skeleton comes via the engine; for now show file count + mode info
            const el = document.getElementById('ast-metadata');
            if (el && data.target_directory) {
                el.textContent = `Workspace: ${data.target_directory} | Files: ${data.workspace_files} | Mode: ${data.mode} | Phase: ${data.phase}`;
            }
        }
    } catch (e) {
        console.warn('Failed to fetch AST metadata:', e);
    }
}

// ═══════════════════════════════════════════════════════════════════
//  SETTINGS CHANGE HANDLERS
// ═══════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    // Theme switch
    document.getElementById('theme-switch').addEventListener('change', (e) => {
        const theme = e.target.checked ? 'light' : 'dark';
        document.documentElement.dataset.theme = theme;
    });
    // Mode switch
    document.getElementById('mode-switch').addEventListener('change', (e) => {
        WS_MODULE.send({
            type: 'settings',
            mode: e.target.checked ? 'agent' : 'chat',
        });
    });

    // LLM dropdown
    document.getElementById('llm-dropdown').addEventListener('change', (e) => {
        WS_MODULE.send({
            type: 'settings',
            llm: e.target.value,
        });
    });

    // Demo switch
    document.getElementById('demo-switch').addEventListener('change', (e) => {
        WS_MODULE.send({
            type: 'settings',
            guided_demo: e.target.checked,
        });
    });

    // Turn limits
    document.getElementById('max-agent-turns').addEventListener('change', (e) => {
        WS_MODULE.send({
            type: 'settings',
            max_agent_turns: parseInt(e.target.value) || 6,
        });
    });

    document.getElementById('max-chat-turns').addEventListener('change', (e) => {
        WS_MODULE.send({
            type: 'settings',
            max_chat_turns: parseInt(e.target.value) || 3,
        });
    });

    // Allow Enter on login
    document.getElementById('login-passphrase').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleLogin();
    });
});
