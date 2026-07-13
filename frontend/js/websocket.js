/* ═══════════════════════════════════════════════════════════════════
   WebSocket Client Module
   Manages the WS connection to the DAVE server with auto-reconnect.
   ═══════════════════════════════════════════════════════════════════ */

const WS_MODULE = (() => {
    let ws = null;
    let reconnectTimer = null;
    let isConnected = false;
    let messageHandler = null;
    let statusHandler = null;

    const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];

    function connect(token) {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/agent`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            isConnected = true;
            if (statusHandler) statusHandler('connected');
            // Send auth
            ws.send(JSON.stringify({ type: 'auth', token: token }));
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (messageHandler) messageHandler(msg);
            } catch (e) {
                console.warn('[WS] Failed to parse message:', e);
            }
        };

        ws.onclose = () => {
            isConnected = false;
            if (statusHandler) statusHandler('disconnected');
            scheduleReconnect(token);
        };

        ws.onerror = () => {
            // onclose will fire after onerror
        };
    }

    function scheduleReconnect(token) {
        if (reconnectTimer) return;
        let attempt = 0;
        function tryReconnect() {
            const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
            reconnectTimer = setTimeout(() => {
                attempt++;
                if (statusHandler) statusHandler('reconnecting');
                connect(token);
                reconnectTimer = null;
            }, delay);
        }
        tryReconnect();
    }

    function send(data) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(data));
            return true;
        }
        return false;
    }

    function disconnect() {
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (ws) {
            try { ws.onclose = null; } catch(e) {}
            try { ws.close(); } catch(e) {}
            ws = null;
        }
        isConnected = false;
    }

    function onMessage(callback) {
        messageHandler = callback;
    }

    function onStatus(callback) {
        statusHandler = callback;
    }

    function getIsConnected() {
        return isConnected;
    }

    return {
        connect,
        disconnect,
        send,
        onMessage,
        onStatus,
        getIsConnected,
    };
})();
