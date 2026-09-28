import json
import pytest
from aiohttp.test_utils import make_mocked_request
from src.events.bus import EventBus
from src.events.base import ConversationContext, VoiceListeningStarted, ToolStarted
from src.brain.trajectory import TrajectoryManager
from src.security.approval import ApprovalManager
from src.reminders.scheduler import AsyncReminderScheduler
from src.ui.web_overlay import WebOverlayServer


@pytest.mark.asyncio
async def test_web_overlay_initialization():
    bus = EventBus()
    overlay = WebOverlayServer(bus, port=8888)
    assert overlay.port == 8888
    assert overlay.host == "127.0.0.1"   # nunca toda la red por defecto
    assert len(overlay.sockets) == 0

    # Emitir eventos no debe fallar cuando no hay sockets
    ctx = ConversationContext()
    bus.publish(VoiceListeningStarted(ctx))
    bus.publish(ToolStarted(ctx, tool_name="demo_tool", arguments={"x": 1}))


@pytest.mark.asyncio
async def test_web_overlay_bind_es_loopback():
    """Seguridad: el HUD (trayectoria con correos, HITL, mute) solo escucha en
    localhost; antes bindeaba 0.0.0.0 y quedaba expuesto a toda la red local."""
    overlay = WebOverlayServer(EventBus(), port=0)  # puerto efímero
    await overlay.start()
    try:
        addr = overlay._runner.addresses[0]
        assert addr[0] == "127.0.0.1"
    finally:
        await overlay.stop()


@pytest.mark.asyncio
async def test_web_overlay_index_route():
    bus = EventBus()
    overlay = WebOverlayServer(bus, port=8889)
    req = make_mocked_request('GET', '/')
    resp = await overlay.handle_index(req)
    assert resp.status == 200
    assert "Atlas HUD" in resp.text


@pytest.mark.asyncio
async def test_web_overlay_api_routes():
    bus = EventBus()
    tm = TrajectoryManager(event_bus=bus, file_path="test_overlay_traj.json")
    tm.add_step(event_type="TestEvent", role="user", summary="Test prompt")
    approval = ApprovalManager(event_bus=bus)
    reminders = AsyncReminderScheduler(event_bus=bus)

    overlay = WebOverlayServer(
        event_bus=bus,
        port=8890,
        approval_manager=approval,
        trajectory_manager=tm,
        reminder_scheduler=reminders
    )

    # 1. Test /api/status
    req_status = make_mocked_request('GET', '/api/status')
    resp_status = await overlay.handle_api_status(req_status)
    assert resp_status.status == 200
    data_status = json.loads(resp_status.text)
    assert data_status["status"] == "online"

    # 2. Test /api/trajectory
    req_traj = make_mocked_request('GET', '/api/trajectory')
    resp_traj = await overlay.handle_api_trajectory(req_traj)
    assert resp_traj.status == 200
    data_traj = json.loads(resp_traj.text)
    assert data_traj["total_steps"] >= 1

    # Cleanup
    import os
    if os.path.exists("test_overlay_traj.json"):
        os.remove("test_overlay_traj.json")
    if os.path.exists("test_overlay_traj.json.tmp"):
        os.remove("test_overlay_traj.json.tmp")


@pytest.mark.asyncio
async def test_api_status_incluye_modelo_de_voz():
    overlay = WebOverlayServer(EventBus(), port=8891)
    overlay.get_voice_status = lambda: {
        "model": "gemini-3.8-live",
        "fallback_model": "gemini-3.1-flash-live-preview",
        "active_model": "gemini-3.1-flash-live-preview",
        "fallback_active": True,
    }
    req = make_mocked_request('GET', '/api/status')
    resp = await overlay.handle_api_status(req)
    data = json.loads(resp.text)

    assert data["voice"]["active_model"] == "gemini-3.1-flash-live-preview"
    assert data["voice"]["fallback_active"] is True

    # Sin callback, el campo va vacío pero el endpoint sigue respondiendo
    overlay.get_voice_status = None
    resp2 = await overlay.handle_api_status(req)
    assert json.loads(resp2.text)["voice"] == {}
