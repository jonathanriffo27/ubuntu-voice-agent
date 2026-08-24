import pytest
from src.events.bus import EventBus
from src.events.base import ConversationContext, VoiceListeningStarted, ToolStarted
from src.ui.web_overlay import WebOverlayServer


@pytest.mark.asyncio
async def test_web_overlay_initialization():
    bus = EventBus()
    overlay = WebOverlayServer(bus, port=8888)
    assert overlay.port == 8888
    assert len(overlay.sockets) == 0

    # Emitir eventos no debe fallar cuando no hay sockets
    ctx = ConversationContext()
    bus.publish(VoiceListeningStarted(ctx))
    bus.publish(ToolStarted(ctx, tool_name="demo_tool", arguments={"x": 1}))


@pytest.mark.asyncio
async def test_web_overlay_index_route():
    from aiohttp.test_utils import make_mocked_request

    bus = EventBus()
    overlay = WebOverlayServer(bus, port=8889)
    req = make_mocked_request('GET', '/')
    resp = await overlay.handle_index(req)
    assert resp.status == 200
    assert "Atlas HUD" in resp.text

