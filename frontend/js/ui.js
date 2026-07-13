/* ═══════════════════════════════════════════════════════════════════
   UI Rendering Module
   All DOM manipulation lives here — keeps app.js clean.
   ═══════════════════════════════════════════════════════════════════ */

const UI = (() => {
    // ── Helpers ──────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const esc = (s) => {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    };

    // ── Login ────────────────────────────────────────────────────
    function showLoginError(show) {
        $('login-error').classList.toggle('hidden', !show);
    }

    function hideLogin() {
        $('login-screen').classList.add('hidden');
        $('dashboard').classList.add('visible');
    }

    function showLogin() {
        // Reset root layout visibility explicitly
        $('dashboard').classList.remove('visible');
        $('dashboard').classList.add('hidden');
        $('login-screen').classList.remove('hidden');
        $('login-screen').classList.remove('visible');
        // Clear credentials and errors
        $('login-passphrase').value = '';
        $('login-error').classList.add('hidden');
        // Reset telemetry badges to default state
        var phaseEl = $('phase-badge');
        if (phaseEl) { phaseEl.className = 'badge badge-blue'; phaseEl.textContent = 'Phase: SCOUT'; }
        var confEl = $('conf-badge');
        if (confEl) { confEl.className = 'badge badge-green'; confEl.textContent = 'Conf: 100%'; }
        var retryEl = $('retry-text');
        if (retryEl) { retryEl.className = 'text-xs text-gray-500 self-center'; retryEl.textContent = 'Retries: 0/3'; }
        var targetEl = $('target-label');
        if (targetEl) { targetEl.textContent = 'Target: None'; }
        // Nullify any lingering WS reference
        if (typeof WS_MODULE !== 'undefined' && WS_MODULE.disconnect) {
            WS_MODULE.disconnect();
        }
    }

    function resetDashboard() {
        showLogin();
        $('chat-stream').innerHTML = '';
        $('tool-stream').innerHTML = '<p style="color:var(--success);font-size:12px;font-family:Consolas">&gt; Ready...</p>';
        $('context-viewer').innerHTML = '<p style="color:var(--text-muted);font-size:12px;font-family:Consolas">No context loaded.</p>';
        $('warning-panel').innerHTML = '<p style="color:var(--text-muted);font-size:12px;font-family:Consolas">No active system warnings.</p>';
        $('file-tree').innerHTML = '<p style="color:var(--text-muted);font-style:italic">No workspace loaded.</p>';
        $('ast-metadata').textContent = 'No AST map available.';
        setStatus('Idle', 'text-gray-500');
        sessionStorage.removeItem('dave_theme');
    }

    // ── Chat ─────────────────────────────────────────────────────
    function addChatMessage(text, color = 'text-gray-100') {
        const stream = $('chat-stream');
        const isUser = text.startsWith('You:');
        const div = document.createElement('div');
        div.className = isUser ? 'chat-user' : 'chat-dave';
        div.innerHTML = `<div class="chat-bubble ${color} text-sm">${esc(text)}</div>`;
        stream.appendChild(div);
        stream.scrollTop = stream.scrollHeight;
    }

    function addTurnWidget(thought, tools, reply) {
        const stream = $('chat-stream');
        const replyText = (reply && reply.trim() && reply !== '...')
            ? reply : 'Executing task...';

        const card = document.createElement('div');
        card.className = 'turn-card';

        let toolsHtml = '';
        if (tools && tools.length) {
            toolsHtml = '<p class="text-xs text-gray-500 font-bold mt-2">Tools Planned:</p>';
            tools.forEach(t => {
                toolsHtml += `<p class="text-xs text-green-400 font-mono ml-2">• ${esc(t)}</p>`;
            });
        }

        card.innerHTML = `
            <div class="turn-header" onclick="this.nextElementSibling.classList.toggle('open'); this.querySelector('.turn-toggle').classList.toggle('expanded')">
                <span class="turn-toggle">▼</span>
                <span class="text-green-400 font-bold text-sm">D.A.V.E.: ${esc(replyText)}</span>
            </div>
            <div class="turn-details">
                ${thought ? `<p class="text-xs text-gray-400 font-bold">Thinking:</p><p class="text-xs text-gray-300 mb-2">${esc(thought)}</p>` : ''}
                ${toolsHtml}
            </div>
        `;

        stream.appendChild(card);
        stream.scrollTop = stream.scrollHeight;
    }

    function clearChat() {
        $('chat-stream').innerHTML = '';
    }

    // ── Status ───────────────────────────────────────────────────
    function setStatus(text, color = 'text-gray-500') {
        const el = $('status-text');
        el.textContent = `Status: ${text}`;
        el.className = `text-xs font-semibold ${color}`;
    }

    // ── Telemetry ────────────────────────────────────────────────
    function updateTelemetry(phase, conf, retries) {
        const phaseEl = $('phase-badge');
        const confEl = $('conf-badge');

        const phaseColors = {
            'Scout': 'badge-blue',
            'Chat': 'badge-blue',
            'Plan': 'badge-amber',
            'Execute': 'badge-green',
        };
        phaseEl.className = `badge ${phaseColors[phase] || 'badge-blue'}`;
        phaseEl.textContent = `Phase: ${phase}`;

        const confPct = Math.round(conf * 100);
        confEl.className = `badge ${conf >= 0.8 ? 'badge-green' : 'badge-red'}`;
        confEl.textContent = `Conf: ${confPct}%`;

        const retryEl = $('retry-text');
        retryEl.className = `text-xs ${retries > 0 ? 'text-red-400' : 'text-gray-500'} self-center`;
        retryEl.textContent = `Retries: ${retries}/3`;
    }

    function setTarget(target) {
        $('target-label').textContent = `Target: ${target || 'None'}`;
    }

    // ── Tool Stream ──────────────────────────────────────────────
    function addToolStream(text, color = 'text-green-400') {
        const stream = $('tool-stream');
        // Remove placeholder if present
        const placeholder = stream.querySelector('.placeholder');
        if (placeholder) placeholder.remove();

        const p = document.createElement('p');
        p.className = `text-xs font-mono ${mapColor(color)}`;
        p.textContent = text;
        stream.appendChild(p);
        stream.scrollTop = stream.scrollHeight;
    }

    function clearToolStream() {
        $('tool-stream').innerHTML = '<p class="text-green-400 text-xs font-mono placeholder">&gt; Ready...</p>';
    }

    // ── Context Viewer ───────────────────────────────────────────
    function addContextMsg(text, color = 'text-gray-300') {
        const viewer = $('context-viewer');
        const placeholder = viewer.querySelector('.placeholder');
        if (placeholder) placeholder.remove();

        const p = document.createElement('p');
        p.className = `text-xs font-mono ${mapColor(color)}`;
        p.textContent = text;
        viewer.appendChild(p);
        viewer.scrollTop = viewer.scrollHeight;
    }

    // ── Warnings ─────────────────────────────────────────────────
    function showWarning(text) {
        const panel = $('warning-panel');
        const placeholder = panel.querySelector('.placeholder');
        if (placeholder) placeholder.remove();

        const p = document.createElement('p');
        p.className = 'text-xs font-mono text-red-400';
        p.textContent = text;
        panel.appendChild(p);
        panel.scrollTop = panel.scrollHeight;
    }

    // ── Toast ────────────────────────────────────────────────────
    function showToast(message, level = 'info') {
        const bgColors = {
            green: 'bg-green-900',
            red: 'bg-red-900',
            blue: 'bg-blue-900',
            yellow: 'bg-amber-900',
            info: 'bg-gray-800',
        };
        const bg = bgColors[level] || bgColors.info;

        const toast = document.createElement('div');
        toast.className = `fixed bottom-20 left-1/2 -translate-x-1/2 ${bg} text-white text-sm px-4 py-2 rounded-lg shadow-lg z-50 transition-opacity duration-300`;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // ── File Tree ────────────────────────────────────────────────
    function renderFileTree(treeData) {
        const container = $('file-tree');
        container.innerHTML = '';
        if (!treeData || !treeData.children) {
            container.innerHTML = '<p class="text-gray-600 italic">No workspace loaded.</p>';
            return;
        }
        renderNode(treeData, container, 0);
    }

    function renderNode(node, parent, depth) {
        if (node.type === 'directory') {
            const div = document.createElement('div');
            div.className = 'tree-folder';
            div.style.paddingLeft = `${depth * 16}px`;
            div.innerHTML = `<span>${esc(node.name)}</span>`;
            parent.appendChild(div);

            if (node.expanded && node.children) {
                node.children.forEach(child => renderNode(child, parent, depth + 1));
            }
        } else if (node.type === 'file') {
            const div = document.createElement('div');
            div.className = 'tree-file';
            div.style.paddingLeft = `${depth * 16 + 20}px`;
            const heatClass = node.heat >= 10 ? 'tree-heat-high'
                : node.heat >= 5 ? 'tree-heat-mid'
                : 'tree-heat-low';
            div.innerHTML = `<span class="${heatClass}">${esc(node.heat_bar)} ${esc(node.name)}</span>`;
            parent.appendChild(div);
        }
    }

    // ── Input State ──────────────────────────────────────────────
    function setInputEnabled(enabled) {
        $('message-input').disabled = !enabled;
        $('send-btn').disabled = !enabled;
        $('message-input').placeholder = enabled
            ? 'Give D.A.V.E. a task...'
            : 'D.A.V.E. is thinking...';
    }

    function setAllControlsEnabled(enabled) {
        $('undo-btn').disabled = !enabled;
        // Stop button always stays enabled so user can cancel
        $('mode-switch').disabled = !enabled;
        $('llm-dropdown').disabled = !enabled;
        $('demo-switch').disabled = !enabled;
    }

    // ── Color mapping ────────────────────────────────────────────
    function mapColor(color) {
        const map = {
            'white': 'text-gray-100',
            'blue': 'text-blue-400',
            'green': 'text-green-400',
            'red': 'text-red-400',
            'yellow': 'text-yellow-400',
            'cyan': 'text-cyan-400',
        };
        return map[color] || 'text-gray-100';
    }

    // ── Public API ───────────────────────────────────────────────
    return {
        showLoginError,
        hideLogin,
        showLogin,
        resetDashboard,
        addChatMessage,
        addTurnWidget,
        clearChat,
        setStatus,
        updateTelemetry,
        setTarget,
        addToolStream,
        clearToolStream,
        addContextMsg,
        showWarning,
        showToast,
        renderFileTree,
        setInputEnabled,
        setAllControlsEnabled,
        mapColor,
    };
})();
