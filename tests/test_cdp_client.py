"""Tests del cliente CDP (websocket multiplexado con flatten sessions)."""
import asyncio
import json

import pytest

from src.cdp.client import CDPConnection, CDPError, CDPTimeoutError


class FakeWebSocket:
    """Simula el socket del navegador: el test empuja respuestas/eventos."""

    def __init__(self):
        self.sent: list = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send(self, data: str):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        await self.incoming.put(None)


def make_connection(monkeypatch, fake_ws) -> CDPConnection:
    import websockets

    async def fake_connect(url, **kwargs):
        return fake_ws

    monkeypatch.setattr(websockets, "connect", fake_connect)
    return CDPConnection("ws://127.0.0.1:9222/devtools/browser/fake")


class TestSendReceive:
    async def test_send_resuelve_respuesta_por_id(self, monkeypatch):
        ws = FakeWebSocket()
        conn = make_connection(monkeypatch, ws)
        await conn.connect()

        task = asyncio.create_task(conn.send("Target.getTargets"))
        await asyncio.sleep(0)  # dejar que envíe
        assert ws.sent[0]["method"] == "Target.getTargets"
        msg_id = ws.sent[0]["id"]

        await ws.incoming.put(json.dumps({"id": msg_id, "result": {"targetInfos": []}}))
        result = await task
        assert result == {"targetInfos": []}
        await conn.close()

    async def test_send_incluye_session_id(self, monkeypatch):
        ws = FakeWebSocket()
        conn = make_connection(monkeypatch, ws)
        await conn.connect()

        task = asyncio.create_task(
            conn.send("Page.navigate", {"url": "https://x.com"}, session_id="S1"))
        await asyncio.sleep(0)
        assert ws.sent[0]["sessionId"] == "S1"
        assert ws.sent[0]["params"] == {"url": "https://x.com"}

        await ws.incoming.put(json.dumps({"id": ws.sent[0]["id"], "result": {}}))
        await task
        await conn.close()

    async def test_error_cdp_lanza_excepcion(self, monkeypatch):
        ws = FakeWebSocket()
        conn = make_connection(monkeypatch, ws)
        await conn.connect()

        task = asyncio.create_task(conn.send("Target.attachToTarget"))
        await asyncio.sleep(0)
        await ws.incoming.put(json.dumps({
            "id": ws.sent[0]["id"],
            "error": {"code": -32602, "message": "Invalid parameters"},
        }))
        with pytest.raises(CDPError, match="Invalid parameters"):
            await task
        await conn.close()

    async def test_timeout_limpia_pendientes(self, monkeypatch):
        ws = FakeWebSocket()
        conn = make_connection(monkeypatch, ws)
        await conn.connect()

        with pytest.raises(CDPTimeoutError):
            await conn.send("Page.navigate", timeout=0.05)
        assert not conn._pending  # sin futures huérfanos
        await conn.close()


class TestEvents:
    async def test_evento_global_y_por_sesion(self, monkeypatch):
        ws = FakeWebSocket()
        conn = make_connection(monkeypatch, ws)
        await conn.connect()

        seen = []
        conn.on_event("Page.loadEventFired", lambda p: seen.append(("any", p)))
        conn.on_event("Page.loadEventFired", lambda p: seen.append(("S1", p)),
                      session_id="S1")

        await ws.incoming.put(json.dumps({
            "method": "Page.loadEventFired", "sessionId": "S1",
            "params": {"timestamp": 123},
        }))
        await asyncio.sleep(0.05)
        assert ("any", {"timestamp": 123}) in seen
        assert ("S1", {"timestamp": 123}) in seen
        await conn.close()

    async def test_perdida_de_conexion_falla_pendientes(self, monkeypatch):
        ws = FakeWebSocket()
        conn = make_connection(monkeypatch, ws)
        await conn.connect()

        task = asyncio.create_task(conn.send("Page.navigate"))
        await asyncio.sleep(0)
        await ws.incoming.put(None)  # simular cierre del socket
        with pytest.raises(CDPError, match="perdida|cerrada"):
            await task
