"""
Tests de regresión para las mejoras del provider Gemini Live y resiliencia:
- Session resumption (captura de handle, reconexión con contexto)
- GoAway → reconexión proactiva
- tool_call_cancellation
- end_turn vía audio_stream_end (no send_client_content)
- send_text vía send_client_content (session.send deprecado)
- Deduplicación de tools por turno (con reset en TurnComplete)
- Office: _resolver_ruta_docx sin doble extensión ni sobrescritura
- AprobarAccionTool: sin fallback a ciegas con request_id inválido
"""
import pytest
from unittest.mock import AsyncMock

from src.providers.base import (
    GoAway, ToolCallsCancelled, SessionReconnectRequested
)
from src.providers.gemini_session import GeminiSession
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant


# ---------------------------------------------------------------------------
# Mocks de la sesión nativa del SDK
# ---------------------------------------------------------------------------

class MockMsg:
    """Mensaje flexible estilo LiveServerMessage."""
    def __init__(self, **kwargs):
        # Atributos que GeminiSession.receive() consulta siempre
        defaults = {
            "server_content": None, "tool_call": None,
            "session_resumption_update": None, "go_away": None,
            "tool_call_cancellation": None, "usage_metadata": None,
        }
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


class MockNativeSession:
    def __init__(self, messages):
        self._messages = messages
        self.realtime_calls = []
        self.client_content_calls = []

    async def receive(self):
        for m in self._messages:
            yield m

    async def send_realtime_input(self, **kwargs):
        self.realtime_calls.append(kwargs)

    async def send_client_content(self, **kwargs):
        self.client_content_calls.append(kwargs)


class MockResumptionUpdate:
    def __init__(self, handle, resumable=True):
        self.new_handle = handle
        self.resumable = resumable


class MockGoAway:
    def __init__(self, time_left="30s"):
        self.time_left = time_left


class MockToolCallCancellation:
    def __init__(self, ids):
        self.ids = ids


async def collect(session):
    events = []
    try:
        async for ev in session.receive():
            events.append(ev)
    except SessionReconnectRequested:
        pass
    return events


# ---------------------------------------------------------------------------
# 1. Session resumption
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resumption_handle_se_captura_via_callback():
    handles = []
    native = MockNativeSession([
        MockMsg(session_resumption_update=MockResumptionUpdate("handle-abc-123"))
    ])
    session = GeminiSession(native, on_session_handle=handles.append)
    await collect(session)
    assert handles == ["handle-abc-123"]


@pytest.mark.asyncio
async def test_resumption_handle_no_resumable_se_ignora():
    handles = []
    native = MockNativeSession([
        MockMsg(session_resumption_update=MockResumptionUpdate("h1", resumable=False))
    ])
    session = GeminiSession(native, on_session_handle=handles.append)
    await collect(session)
    assert handles == []


def test_provider_persiste_handle_entre_reconexiones():
    provider = GeminiProvider.__new__(GeminiProvider)  # sin conectar a la API
    provider._session_handle = None
    provider._save_session_handle("handle-xyz")
    assert provider._session_handle == "handle-xyz"


def test_session_resumption_no_usa_transparent_en_developer_api(monkeypatch):
    """Regresión: transparent=True hace que la Developer API RECHACE la conexión
    ('transparent parameter is only supported in Gemini Enterprise...')."""
    captured = {}

    class FakeSessionResumptionConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "src.providers.gemini.types.SessionResumptionConfig",
        FakeSessionResumptionConfig
    )
    # Evitar conexión real: simular el context manager
    class FakeCM:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False

    class FakeLive:
        def connect(self, model, config): return FakeCM()

    class FakeAio:
        live = FakeLive()

    class FakeClient:
        aio = FakeAio()

    import asyncio
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.client = FakeClient()
    provider.model_name = "x"
    provider.voice_name = "y"
    provider.server_vad = False
    provider.affective_dialog = False
    provider._session_handle = "handle-xyz"

    async def go():
        async with provider.connect(system_prompt="s", tools=[]):
            pass

    asyncio.run(go())
    assert captured.get("handle") == "handle-xyz"
    assert "transparent" not in captured, "transparent no debe enviarse a la Developer API"


# ---------------------------------------------------------------------------
# 2. GoAway → reconexión proactiva
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goaway_yield_evento_y_lanza_reconnect():
    native = MockNativeSession([MockMsg(go_away=MockGoAway(time_left="45s"))])
    session = GeminiSession(native)
    events = []
    with pytest.raises(SessionReconnectRequested):
        async for ev in session.receive():
            events.append(ev)
    assert len(events) == 1
    assert isinstance(events[0], GoAway)
    assert events[0].time_left == "45s"


# ---------------------------------------------------------------------------
# 3. tool_call_cancellation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_cancellation_se_normaliza():
    native = MockNativeSession([
        MockMsg(tool_call_cancellation=MockToolCallCancellation(ids=["id-1", "id-2"]))
    ])
    session = GeminiSession(native)
    events = await collect(session)
    cancelled = [e for e in events if isinstance(e, ToolCallsCancelled)]
    assert len(cancelled) == 1
    assert cancelled[0].ids == ["id-1", "id-2"]


# ---------------------------------------------------------------------------
# 4 / 5. Señalización moderna del SDK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_turn_usa_audio_stream_end():
    native = MockNativeSession([])
    session = GeminiSession(native)
    await session.end_turn()
    assert native.realtime_calls == [{"audio_stream_end": True}]
    assert native.client_content_calls == []


@pytest.mark.asyncio
async def test_send_text_usa_send_client_content():
    native = MockNativeSession([])
    session = GeminiSession(native)
    await session.send_text("hola", end_of_turn=True)
    assert len(native.client_content_calls) == 1
    call = native.client_content_calls[0]
    assert call["turn_complete"] is True
    assert call["turns"].role == "user"


# ---------------------------------------------------------------------------
# 6. Deduplicación de tools por turno
# ---------------------------------------------------------------------------

def test_dedup_tools_mismo_turno():
    a = Assistant(provider=None, registry=None)
    assert a._is_duplicate_call("reproducir_musica", {"q": "Queen"}) is False  # 1ª vez: ejecutar
    assert a._is_duplicate_call("reproducir_musica", {"q": "Queen"}) is True   # duplicada
    assert a._is_duplicate_call("reproducir_musica", {"q": "AC/DC"}) is False  # args distintos


def test_dedup_se_resetea_entre_turnos():
    a = Assistant(provider=None, registry=None)
    a._is_duplicate_call("reproducir_musica", {"q": "Queen"})
    a._duplicate_guard.clear()  # lo que hace TurnComplete
    # Turno siguiente con la misma petición: debe ejecutarse, no ser ignorada
    assert a._is_duplicate_call("reproducir_musica", {"q": "Queen"}) is False


# ---------------------------------------------------------------------------
# 7. Office: _resolver_ruta_docx
# ---------------------------------------------------------------------------

def test_resolver_ruta_docx_sin_doble_extension(tmp_path):
    from src.plugins.office.tools import _resolver_ruta_docx
    ruta = _resolver_ruta_docx("informe.docx", str(tmp_path))
    assert ruta.endswith("informe.docx")
    assert not ruta.endswith(".docx.docx")


def test_resolver_ruta_docx_no_sobrescribe(tmp_path):
    from src.plugins.office.tools import _resolver_ruta_docx
    (tmp_path / "informe.docx").write_text("existente")
    ruta = _resolver_ruta_docx("informe", str(tmp_path))
    assert ruta.endswith("informe_2.docx")
    assert (tmp_path / "informe.docx").read_text() == "existente"


# ---------------------------------------------------------------------------
# 8. AprobarAccionTool: sin resolución a ciegas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aprobar_accion_id_invalido_no_resuelve_otra():
    import asyncio
    from src.security.approval import ApprovalManager
    from src.plugins.developer.tools import AprobarAccionTool
    from src.tools.base import ToolContext

    bus = None
    mgr = ApprovalManager(event_bus=None)
    # Crear una solicitud pendiente real
    future = asyncio.get_running_loop().create_future()
    from src.security.approval import ApprovalRequest
    import time
    req = ApprovalRequest(
        id="abc123", action_type="file_write", description="Crear X",
        payload="", created_at=time.time(), timeout_seconds=60, future=future
    )
    mgr._pending_requests["abc123"] = req

    tool = AprobarAccionTool(approval_manager=mgr)
    ctx = ToolContext(config=None, event_bus=None, conversation_context=None)

    # ID equivocado (ej: el modelo pasó el task_id): debe fallar SIN resolver nada
    result = await tool.execute(ctx, aprobar=True, request_id="zz9999")
    assert result.success is False
    assert req.status == "pending"
    assert not future.done()
    assert "abc123" in result.content  # ofrece el ID correcto para rectificar

    # Sin request_id: sí resuelve la más reciente (flujo por voz)
    result2 = await tool.execute(ctx, aprobar=True)
    assert result2.success is True
    assert req.status == "approved"
