// Atlas Web HUD - Interactive Client & Trajectory Engine
document.addEventListener('DOMContentLoaded', () => {
    let ws = null;
    let isMicPaused = false;
    let currentApprovalId = null;

    // DOM Elements
    const statusDot = document.getElementById('statusDot');
    const connectionStatus = document.getElementById('connectionStatus');
    const pulseIndicator = document.getElementById('pulseIndicator');
    const chatFeed = document.getElementById('chatFeed');
    const toolsFeed = document.getElementById('toolsFeed');
    const chatForm = document.getElementById('chatForm');
    const chatInput = document.getElementById('chatInput');
    const btnToggleMic = document.getElementById('btnToggleMic');
    const btnClearChat = document.getElementById('btnClearChat');
    const waveContainer = document.getElementById('waveContainer');

    // HITL Elements
    const hitlBanner = document.getElementById('hitlBanner');
    const hitlTitle = document.getElementById('hitlTitle');
    const hitlDesc = document.getElementById('hitlDesc');
    const hitlCode = document.getElementById('hitlCode');
    const btnApprove = document.getElementById('btnApprove');
    const btnReject = document.getElementById('btnReject');

    // Tab Elements
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    const trajectoryTimeline = document.getElementById('trajectoryTimeline');
    const btnRefreshTrajectory = document.getElementById('btnRefreshTrajectory');
    const stepCountBadge = document.getElementById('stepCountBadge');
    const remindersFeed = document.getElementById('remindersFeed');

    // Tab Switching Logic
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            tabButtons.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            const targetId = btn.getAttribute('data-tab');
            const targetContent = document.getElementById(targetId);
            if (targetContent) {
                targetContent.classList.add('active');
            }
            if (targetId === 'tabTrajectory') {
                loadTrajectory();
            }
        });
    });

    // WebSocket Connection
    function connectWS() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            statusDot.classList.remove('disconnected');
            connectionStatus.textContent = 'En línea';
            pulseIndicator.textContent = '● Escuchando';
            waveContainer.classList.add('active');
        };

        ws.onclose = () => {
            statusDot.classList.add('disconnected');
            connectionStatus.textContent = 'Desconectado';
            pulseIndicator.textContent = '○ Inactivo';
            waveContainer.classList.remove('active');
            setTimeout(connectWS, 2000);
        };

        ws.onerror = (err) => {
            console.error('WebSocket Error:', err);
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleEvent(msg);
            } catch (e) {
                console.error('Error parsing event:', e);
            }
        };
    }

    function appendMessage(text, isUser = false, isTool = false) {
        const bubble = document.createElement('div');
        bubble.className = `msg-bubble ${isUser ? 'msg-user' : (isTool ? 'msg-tool' : 'msg-atlas')}`;
        bubble.textContent = text;
        chatFeed.appendChild(bubble);
        chatFeed.scrollTop = chatFeed.scrollHeight;
    }

    function appendToolEvent(badge, name) {
        const item = document.createElement('div');
        item.className = 'tool-item';
        item.innerHTML = `<span class="tool-badge">${badge}</span><span class="tool-name">${name}</span>`;
        toolsFeed.insertBefore(item, toolsFeed.firstChild);
    }

    function handleEvent(event) {
        const type = event.type;
        const data = event.data || {};

        if (type === 'SpeechRecognized' && data.text) {
            appendMessage(data.text, true);
        } else if (type === 'ResponseGenerated' && data.text) {
            appendMessage(data.text, false);
        } else if (type === 'ToolStarted') {
            appendToolEvent('RUNNING', `⚡ ${data.tool_name}`);
        } else if (type === 'ToolSucceeded') {
            appendToolEvent('SUCCESS', `✅ ${data.tool_name}`);
        } else if (type === 'ToolFailed') {
            appendToolEvent('FAILED', `❌ ${data.tool_name}: ${data.error}`);
        } else if (type === 'ApprovalRequested') {
            showHITLBanner(data.request_id, data.action_type, data.description, data.payload);
        } else if (type === 'ApprovalResolved') {
            hideHITLBanner();
        } else if (type === 'TaskDelegated') {
            appendToolEvent('SUBAGENT', `🤖 [${data.task_id}] ${data.instruction}`);
        } else if (type === 'WakeWordDetected') {
            const conf = Math.round((data.confidence || 0) * 100);
            appendMessage(`🔔 Wake Word '${data.wake_word}' detectado (${conf}%)`, false);
            pulseIndicator.textContent = '● Escuchando';
            waveContainer.classList.add('active');
        } else if (type === 'WakeWordStandby') {
            pulseIndicator.textContent = '💤 En Espera';
            waveContainer.classList.remove('active');
        }
    }

    function showHITLBanner(id, action, desc, payload) {
        currentApprovalId = id;
        hitlTitle.textContent = `🛡️ [HITL: ${id}] ${action}`;
        hitlDesc.textContent = desc;
        hitlCode.textContent = payload || '(Sin payload adicional)';
        hitlBanner.classList.add('active');
    }

    function hideHITLBanner() {
        hitlBanner.classList.remove('active');
        currentApprovalId = null;
    }

    // Trajectory Loader
    async function loadTrajectory() {
        try {
            const resp = await fetch('/api/trajectory');
            if (!resp.ok) return;
            const data = await resp.json();
            const steps = data.steps || [];
            stepCountBadge.textContent = `${steps.length} Pasos`;

            trajectoryTimeline.innerHTML = '';
            if (steps.length === 0) {
                trajectoryTimeline.innerHTML = '<div class="reminder-empty">Aún no hay pasos registrados en la trayectoria.</div>';
                return;
            }

            steps.forEach(step => {
                const item = document.createElement('div');
                item.className = 'timeline-item';
                item.innerHTML = `
                    <div class="timeline-dot ${step.role}"></div>
                    <div class="timeline-header">
                        <span class="timeline-role">${step.role} · ${step.event_type}</span>
                        <span class="timeline-time">${step.iso_time}</span>
                    </div>
                    <div class="timeline-summary">${step.summary}</div>
                    ${step.thinking ? `<div class="thinking-box">🧠 <strong>Thinking:</strong>\n${step.thinking}</div>` : ''}
                `;
                trajectoryTimeline.appendChild(item);
            });
            trajectoryTimeline.scrollTop = trajectoryTimeline.scrollHeight;
        } catch (e) {
            console.error('Error cargando trayectoria:', e);
        }
    }

    // Actions
    btnApprove.addEventListener('click', () => {
        if (currentApprovalId && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'resolve_approval', request_id: currentApprovalId, approved: true }));
            hideHITLBanner();
        }
    });

    btnReject.addEventListener('click', () => {
        if (currentApprovalId && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'resolve_approval', request_id: currentApprovalId, approved: false }));
            hideHITLBanner();
        }
    });

    btnRefreshTrajectory.addEventListener('click', loadTrajectory);

    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const text = chatInput.value.trim();
        if (!text) return;
        appendMessage(text, true);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'user_text', text: text }));
        }
        chatInput.value = '';
    });

    btnToggleMic.addEventListener('click', () => {
        isMicPaused = !isMicPaused;
        btnToggleMic.textContent = isMicPaused ? '▶️ Reanudar Mic' : '⏸️ Pausar Mic';
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'toggle_pause' }));
        }
    });

    btnClearChat.addEventListener('click', () => {
        chatFeed.innerHTML = '<div class="msg-bubble msg-atlas">✨ Chat limpiado.</div>';
    });

    connectWS();
});
