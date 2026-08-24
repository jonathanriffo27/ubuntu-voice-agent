import asyncio
import json
import webbrowser
from dataclasses import asdict, is_dataclass
from typing import Set, Optional, Callable, Any
from aiohttp import web
from src.events.bus import EventBus
from src.events.base import Event
from src.utils.logging import get_logger

logger = get_logger("ui.overlay")

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Atlas HUD | Live Dashboard</title>
    <style>
        :root {
            --bg: #0b0f19;
            --card-bg: rgba(18, 26, 44, 0.85);
            --border: rgba(56, 189, 248, 0.2);
            --border-glow: rgba(56, 189, 248, 0.4);
            --text: #f1f5f9;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-glow: #0284c7;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --purple: #a855f7;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'JetBrains Mono', monospace;
        }

        body {
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            padding: 1.25rem;
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(56, 189, 248, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(168, 85, 247, 0.08) 0%, transparent 40%);
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border);
            margin-bottom: 1rem;
            flex-wrap: wrap;
            gap: 1rem;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .brand-icon {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            background: linear-gradient(135deg, var(--accent), var(--purple));
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1.2rem;
            box-shadow: 0 0 15px rgba(56, 189, 248, 0.4);
        }

        .brand-title {
            font-size: 1.25rem;
            font-weight: 700;
            letter-spacing: 1px;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .action-btn {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 0.45rem 0.9rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }

        .action-btn:hover {
            background: rgba(56, 189, 248, 0.15);
            border-color: var(--accent);
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.3);
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.4rem 0.9rem;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: var(--success);
            transition: all 0.3s ease;
        }

        .status-badge.listening {
            background: rgba(56, 189, 248, 0.15);
            border-color: var(--accent);
            color: var(--accent);
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.3);
        }

        .status-badge.thinking {
            background: rgba(168, 85, 247, 0.15);
            border-color: var(--purple);
            color: var(--purple);
        }

        .status-badge.tool {
            background: rgba(245, 158, 11, 0.15);
            border-color: var(--warning);
            color: var(--warning);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.8); }
        }

        /* Banner de Aprobación HITL */
        .approval-banner {
            background: rgba(245, 158, 11, 0.15);
            border: 2px solid var(--warning);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            margin-bottom: 1.25rem;
            animation: slideDown 0.3s ease;
            box-shadow: 0 0 20px rgba(245, 158, 11, 0.25);
        }

        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .approval-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 700;
            color: var(--warning);
            font-size: 0.95rem;
            margin-bottom: 0.5rem;
        }

        .approval-code {
            font-family: 'JetBrains Mono', monospace;
            background: rgba(0, 0, 0, 0.5);
            padding: 0.75rem;
            border-radius: 8px;
            font-size: 0.82rem;
            color: #fef08a;
            max-height: 180px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            margin: 0.6rem 0;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }

        .approval-actions {
            display: flex;
            gap: 0.75rem;
            justify-content: flex-end;
            margin-top: 0.75rem;
        }

        .btn-approve {
            background: var(--success);
            color: #0b0f19;
            border: none;
            padding: 0.5rem 1.2rem;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-approve:hover {
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.5);
            background: #34d399;
        }

        .btn-reject {
            background: rgba(239, 68, 68, 0.2);
            color: var(--danger);
            border: 1px solid var(--danger);
            padding: 0.5rem 1.2rem;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-reject:hover {
            background: var(--danger);
            color: #fff;
        }

        .grid {
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 1.25rem;
            flex: 1;
        }

        @media (max-width: 900px) {
            .grid { grid-template-columns: 1fr; }
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 1.25rem;
            backdrop-filter: blur(12px);
            display: flex;
            flex-direction: column;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }

        .card-header {
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.85rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .feed-container {
            flex: 1;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            max-height: 520px;
            padding-right: 0.5rem;
        }

        .feed-container::-webkit-scrollbar {
            width: 6px;
        }
        .feed-container::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
        }

        .msg-bubble {
            padding: 0.85rem 1.1rem;
            border-radius: 12px;
            font-size: 0.95rem;
            line-height: 1.5;
            animation: fadeIn 0.3s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .msg-user {
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.25);
            align-self: flex-end;
            max-width: 85%;
            color: #bae6fd;
        }

        .msg-atlas {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            align-self: flex-start;
            max-width: 85%;
        }

        .chat-input-row {
            display: flex;
            gap: 0.5rem;
            margin-top: 1rem;
            padding-top: 0.75rem;
            border-top: 1px solid var(--border);
        }

        .chat-input {
            flex: 1;
            background: rgba(0, 0, 0, 0.35);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 0.65rem 0.9rem;
            color: var(--text);
            font-size: 0.9rem;
            outline: none;
            transition: border 0.2s ease;
        }

        .chat-input:focus {
            border-color: var(--accent);
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.2);
        }

        .chat-submit-btn {
            background: var(--accent);
            color: #0b0f19;
            border: none;
            border-radius: 8px;
            padding: 0 1.2rem;
            font-weight: 700;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .chat-submit-btn:hover {
            background: #7dd3fc;
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
        }

        .tool-card {
            background: rgba(245, 158, 11, 0.08);
            border: 1px solid rgba(245, 158, 11, 0.25);
            border-radius: 10px;
            padding: 0.85rem;
            margin-bottom: 0.75rem;
            animation: fadeIn 0.3s ease;
        }

        .tool-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 600;
            font-size: 0.88rem;
            color: var(--warning);
            margin-bottom: 0.4rem;
        }

        .tool-body {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            color: var(--text-muted);
            word-break: break-all;
            background: rgba(0,0,0,0.3);
            padding: 0.5rem;
            border-radius: 6px;
        }

        .reminder-card {
            background: rgba(168, 85, 247, 0.1);
            border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 10px;
            padding: 0.75rem;
            margin-bottom: 0.6rem;
            font-size: 0.85rem;
            animation: fadeIn 0.3s ease;
        }

        .reminder-title {
            font-weight: bold;
            color: var(--purple);
            display: flex;
            justify-content: space-between;
        }

        .wave-container {
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
            margin: 0.75rem 0;
        }

        .wave-bar {
            width: 4px;
            height: 8px;
            background: var(--accent);
            border-radius: 2px;
            transition: height 0.15s ease;
        }

        .wave-bar.active {
            animation: soundWave 0.8s ease-in-out infinite alternate;
        }

        @keyframes soundWave {
            0% { height: 6px; }
            100% { height: 38px; }
        }

        .stats-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.75rem;
            margin-top: 0.75rem;
        }

        .stat-item {
            background: rgba(0,0,0,0.25);
            border: 1px solid rgba(255,255,255,0.05);
            padding: 0.75rem;
            border-radius: 8px;
            text-align: center;
        }

        .stat-val {
            font-size: 1.2rem;
            font-weight: 700;
            color: var(--accent);
        }

        .stat-lbl {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 0.2rem;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">
            <div class="brand-icon">⚡</div>
            <div>
                <div class="brand-title">ATLAS LIVE HUD</div>
                <div style="font-size: 0.75rem; color: var(--text-muted);">Voice & Subagent AI Platform</div>
            </div>
        </div>
        <div class="header-actions">
            <button class="action-btn" id="btnToggleMic">⏸️ Pausar Mic</button>
            <button class="action-btn" id="btnClearChat">🧹 Limpiar</button>
            <div class="status-badge" id="statusBadge">
                <span class="status-dot"></span>
                <span id="statusText">Conectando...</span>
            </div>
        </div>
    </div>

    <!-- Contenedor dinámico de Aprobación HITL -->
    <div id="approvalBox"></div>

    <div class="grid">
        <div class="card">
            <div class="card-header">
                <span>Conversación en Vivo (Voz / Texto)</span>
                <span id="eventCount" style="font-size: 0.8rem; opacity: 0.7;">0 eventos</span>
            </div>
            <div class="feed-container" id="chatFeed">
                <div class="msg-bubble msg-atlas">
                    👋 <b>Atlas Online</b>. Frontend de voz en tiempo real con subagente de programación en segundo plano (Gemini 3.7 Flash).
                </div>
            </div>
            <form class="chat-input-row" id="chatForm">
                <input type="text" class="chat-input" id="chatInput" placeholder="Escribe un mensaje o comando para Atlas..." autocomplete="off" />
                <button type="submit" class="chat-submit-btn">Enviar</button>
            </form>
        </div>

        <div style="display: flex; flex-direction: column; gap: 1.25rem;">
            <div class="card">
                <div class="card-header">
                    <span>Estado del Audio</span>
                </div>
                <div class="wave-container" id="waveBox">
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                </div>
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-val" id="toolsCount">0</div>
                        <div class="stat-lbl">Herramientas</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-val" id="turnCount">0</div>
                        <div class="stat-lbl">Turnos</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <span>⏰ Recordatorios Activos</span>
                </div>
                <div class="feed-container" id="reminderFeed" style="max-height: 140px;">
                    <div style="color: var(--text-muted); font-size: 0.85rem; text-align: center;">Sin recordatorios pendientes</div>
                </div>
            </div>

            <div class="card" style="flex: 1;">
                <div class="card-header">
                    <span>Herramientas en Ejecución</span>
                </div>
                <div class="feed-container" id="toolFeed" style="max-height: 200px;">
                    <div style="color: var(--text-muted); font-size: 0.85rem; text-align: center; margin-top: 1rem;">
                        No hay herramientas activas
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const statusBadge = document.getElementById('statusBadge');
        const statusText = document.getElementById('statusText');
        const chatFeed = document.getElementById('chatFeed');
        const toolFeed = document.getElementById('toolFeed');
        const reminderFeed = document.getElementById('reminderFeed');
        const approvalBox = document.getElementById('approvalBox');
        const waveBars = document.querySelectorAll('.wave-bar');
        const eventCountEl = document.getElementById('eventCount');
        const toolsCountEl = document.getElementById('toolsCount');
        const turnCountEl = document.getElementById('turnCount');
        const chatForm = document.getElementById('chatForm');
        const chatInput = document.getElementById('chatInput');
        const btnToggleMic = document.getElementById('btnToggleMic');
        const btnClearChat = document.getElementById('btnClearChat');

        let totalEvents = 0;
        let totalTools = 0;
        let totalTurns = 0;
        let isMicPaused = false;
        let currentAtlasMsg = null;
        let remindersList = [];

        function setStatus(text, cls = '') {
            statusText.textContent = text;
            statusBadge.className = 'status-badge ' + cls;
        }

        function setWaveActive(active) {
            waveBars.forEach((bar, i) => {
                if (active) {
                    bar.classList.add('active');
                    bar.style.animationDelay = (i * 0.1) + 's';
                } else {
                    bar.classList.remove('active');
                }
            });
        }

        function appendMessage(text, isUser = false) {
            const div = document.createElement('div');
            div.className = 'msg-bubble ' + (isUser ? 'msg-user' : 'msg-atlas');
            div.textContent = text;
            chatFeed.appendChild(div);
            chatFeed.scrollTop = chatFeed.scrollHeight;
            return div;
        }

        function appendTool(name, args, status = 'Iniciando...') {
            if (totalTools === 1) toolFeed.innerHTML = '';
            const card = document.createElement('div');
            card.className = 'tool-card';
            card.innerHTML = `
                <div class="tool-header">
                    <span>⚡ ${name}</span>
                    <span style="font-size: 0.75rem; color: var(--accent);">${status}</span>
                </div>
                <div class="tool-body">${JSON.stringify(args, null, 2)}</div>
            `;
            toolFeed.prepend(card);
        }

        function showApprovalRequest(req) {
            approvalBox.innerHTML = `
                <div class="approval-banner" id="req-${req.request_id}">
                    <div class="approval-header">
                        <span>⚠️ SOLICITUD DE CONFIRMACIÓN (${req.action_type.toUpperCase()}) [ID: ${req.request_id}]</span>
                        <span style="font-size: 0.8rem; color: #cbd5e1;">Expira en ${req.timeout_seconds}s</span>
                    </div>
                    <div style="font-size: 0.9rem; margin-bottom: 0.4rem;">${req.description}</div>
                    ${req.payload ? `<div class="approval-code">${req.payload}</div>` : ''}
                    <div class="approval-actions">
                        <button class="btn-reject" onclick="resolveApproval('${req.request_id}', false)">❌ Rechazar</button>
                        <button class="btn-approve" onclick="resolveApproval('${req.request_id}', true)">✅ Aprobar Acción</button>
                    </div>
                </div>
            `;
        }

        function removeApprovalRequest(reqId) {
            const el = document.getElementById(`req-${reqId}`);
            if (el) el.remove();
        }

        function resolveApproval(reqId, approved) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: 'resolve_approval',
                    request_id: reqId,
                    approved: approved
                }));
            }
            removeApprovalRequest(reqId);
        }

        function renderReminders() {
            if (remindersList.length === 0) {
                reminderFeed.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem; text-align: center;">Sin recordatorios pendientes</div>';
                return;
            }
            reminderFeed.innerHTML = '';
            remindersList.forEach(item => {
                const card = document.createElement('div');
                card.className = 'reminder-card';
                card.innerHTML = `
                    <div class="reminder-title">
                        <span>🔔 ${item.message}</span>
                        <span style="font-size: 0.75rem; opacity: 0.8;">${item.trigger_at}</span>
                    </div>
                `;
                reminderFeed.appendChild(card);
            });
        }

        const wsUrl = `ws://${window.location.host}/ws`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log('Conectado a Atlas HUD WebSocket');
            setStatus('En Espera');
        };

        ws.onmessage = (e) => {
            const payload = JSON.parse(e.data);
            totalEvents++;
            eventCountEl.textContent = `${totalEvents} eventos`;
            const type = payload.type;
            const data = payload.data || {};

            switch (type) {
                case 'VoiceListeningStarted':
                    setStatus('Escuchando...', 'listening');
                    setWaveActive(true);
                    currentAtlasMsg = null;
                    break;
                case 'VoiceListeningStopped':
                    setStatus('Procesando...', 'thinking');
                    setWaveActive(false);
                    totalTurns++;
                    turnCountEl.textContent = totalTurns;
                    break;
                case 'SpeechRecognized':
                    if (data.text && data.text !== '[Audio enviado]') {
                        appendMessage(data.text, true);
                    }
                    break;
                case 'ResponseGenerated':
                    if (!currentAtlasMsg) {
                        currentAtlasMsg = appendMessage(data.text, false);
                    } else {
                        currentAtlasMsg.textContent += data.text;
                        chatFeed.scrollTop = chatFeed.scrollHeight;
                    }
                    setStatus('Hablando', 'listening');
                    break;
                case 'ToolStarted':
                    totalTools++;
                    toolsCountEl.textContent = totalTools;
                    setStatus(`Tool: ${data.tool_name}`, 'tool');
                    appendTool(data.tool_name, data.arguments, 'Ejecutando');
                    break;
                case 'ToolSucceeded':
                    setStatus('Tool completada', 'tool');
                    break;
                case 'ApprovalRequested':
                    showApprovalRequest(data);
                    appendMessage(`⚠️ [AUTORIZACIÓN REQUERIDA]: ${data.description}`, false);
                    break;
                case 'ApprovalResolved':
                    removeApprovalRequest(data.request_id);
                    appendMessage(`🛡️ Acción [${data.request_id}] ${data.approved ? 'APROBADA ✅' : 'RECHAZADA ❌'} por ${data.resolver}`, false);
                    break;
                case 'TaskDelegated':
                    appendMessage(`🚀 [SUBAGENTE INICIADO]: ${data.instruction} (${data.model})`, false);
                    break;
                case 'TaskCompleted':
                    appendMessage(`🏁 [SUBAGENTE FINALIZADO]: ${data.result}`, false);
                    break;
                case 'PluginsReloaded':
                    appendMessage(`🔄 [RECARGA EN CALIENTE]: ${data.tools_count} herramientas activas en memoria.`, false);
                    break;
                case 'ReminderCreated':
                    remindersList.push(data);
                    renderReminders();
                    break;
                case 'ReminderTriggered':
                    remindersList = remindersList.filter(x => x.reminder_id !== data.reminder_id);
                    renderReminders();
                    appendMessage(`🔔 [RECORDATORIO]: ${data.message}`, false);
                    break;
                case 'SessionStarted':
                    setStatus('Conectado');
                    break;
                case 'SessionEnded':
                    setStatus('Desconectado');
                    break;
            }
        };

        ws.onclose = () => {
            setStatus('Desconectado');
            setWaveActive(false);
        };

        chatForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const text = chatInput.value.trim();
            if (!text) return;
            appendMessage(text, true);
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'user_text', text: text }));
            }
            chatInput.value = '';
        });

        btnToggleMic.addEventListener('click', () => {
            isMicPaused = !isMicPaused;
            btnToggleMic.textContent = isMicPaused ? '▶️ Reanudar Mic' : '⏸️ Pausar Mic';
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'toggle_pause' }));
            }
        });

        btnClearChat.addEventListener('click', () => {
            chatFeed.innerHTML = '<div class="msg-bubble msg-atlas">✨ Chat limpiado.</div>';
        });
    </script>
</body>
</html>
"""


class WebOverlayServer:
    """
    Servidor Web y WebSocket en tiempo real para el HUD / Dashboard flotante de Atlas.
    Permite visualizar eventos en vivo, enviar mensajes de texto y resolver aprobaciones HITL.
    """

    def __init__(
        self,
        event_bus: EventBus,
        port: int = 7890,
        on_user_input_callback: Optional[Callable[[str], Any]] = None,
        on_toggle_pause_callback: Optional[Callable[[], Any]] = None,
        approval_manager=None
    ):
        self.event_bus = event_bus
        self.port = port
        self.on_user_input = on_user_input_callback
        self.on_toggle_pause = on_toggle_pause_callback
        self.approval_manager = approval_manager

        self.app = web.Application()
        self.sockets: Set[web.WebSocketResponse] = set()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/ws', self.handle_ws)

        # Suscribir al EventBus para capturar y retransmitir todos los eventos
        self.event_bus.subscribe_all(self._on_domain_event)

    async def handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=HTML_DASHBOARD, content_type='text/html')

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.add(ws)
        logger.info(f"Cliente HUD conectado desde {request.remote}. Total clientes: {len(self.sockets)}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")
                        if msg_type == "user_text":
                            text = data.get("text", "")
                            if self.on_user_input and text:
                                if asyncio.iscoroutinefunction(self.on_user_input):
                                    await self.on_user_input(text)
                                else:
                                    self.on_user_input(text)
                        elif msg_type == "toggle_pause":
                            if self.on_toggle_pause:
                                if asyncio.iscoroutinefunction(self.on_toggle_pause):
                                    await self.on_toggle_pause()
                                else:
                                    self.on_toggle_pause()
                        elif msg_type == "resolve_approval":
                            req_id = data.get("request_id")
                            approved = bool(data.get("approved", False))
                            if self.approval_manager and req_id:
                                self.approval_manager.resolve(req_id, approved, resolver="hud")
                    except Exception as e:
                        logger.error(f"Error procesando mensaje WebSocket entrante: {e}")
        finally:
            self.sockets.discard(ws)
            logger.info(f"Cliente HUD desconectado. Restantes: {len(self.sockets)}")
        return ws

    def _on_domain_event(self, event: Event) -> None:
        """Callback síncrono del EventBus; encola la difusión asíncrona."""
        if not self.sockets:
            return

        event_name = event.__class__.__name__
        event_dict = {
            "type": event_name,
            "timestamp": getattr(event, "timestamp", "").isoformat() if hasattr(event, "timestamp") else "",
            "data": {}
        }

        for k, v in getattr(event, "__dict__", {}).items():
            if k not in ("context", "timestamp"):
                event_dict["data"][k] = v

        payload = json.dumps(event_dict)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(payload))
        except RuntimeError:
            pass

    async def _broadcast(self, payload: str) -> None:
        """Difunde a todos los clientes WebSocket conectados."""
        for ws in list(self.sockets):
            if not ws.closed:
                try:
                    await ws.send_str(payload)
                except Exception:
                    self.sockets.discard(ws)

    async def start(self, auto_open: bool = False) -> None:
        """Inicia el servidor HTTP y WebSocket."""
        logger.info(f"Iniciando Dashboard HUD en http://localhost:{self.port}")
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, '0.0.0.0', self.port)
        await self._site.start()
        logger.info(f"⚡ Dashboard HUD activo en http://localhost:{self.port}")

        if auto_open:
            try:
                webbrowser.open(f"http://localhost:{self.port}")
            except Exception:
                pass

    async def stop(self) -> None:
        """Detiene el servidor y cierra WebSockets."""
        for ws in list(self.sockets):
            await ws.close()
        self.sockets.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
