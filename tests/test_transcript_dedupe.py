"""Deduplicación de transcripciones del usuario repetidas por Gemini Live.

Gemini Live (3.8) a veces emite la misma input_transcription dos veces dentro
de un turno; el asistente no debe re-publicarla (la UI la imprimiría en dos
líneas "🎙️ Tú (voz): …" idénticas). En turnos DISTINTOS sí debe publicarse,
aunque el texto sea idéntico (el usuario puede repetir la pregunta).
"""
import asyncio

from src.brain.assistant import Assistant
from src.events.base import SpeechRecognized
from src.events.bus import EventBus
from src.providers.base import TurnComplete, UserTextChunk
from src.tools.registry import ToolRegistry


class FakeSession:
    """Sesión de proveedor mínima: emite los eventos dados y espera."""

    def __init__(self, events):
        self._events = events
        self.tool_responses = []

    async def receive(self):
        for event in self._events:
            yield event
        await asyncio.sleep(3600)  # hasta que el test cancele

    async def send_tool_response(self, responses):
        self.tool_responses.append(responses)


async def _collect(events):
    bus = EventBus()
    seen = []
    bus.subscribe_all(lambda e: seen.append(e))
    assistant = Assistant(provider=None, registry=ToolRegistry(), event_bus=bus)
    task = asyncio.create_task(
        assistant.receive_and_route(FakeSession(events), asyncio.Queue(), asyncio.Queue())
    )
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return seen


async def test_transcripcion_duplicada_en_el_mismo_turno_se_filtra():
    seen = await _collect([
        UserTextChunk(text="¿Cuál es mi último correo?"),
        UserTextChunk(text="¿Cuál es mi último correo?"),
        TurnComplete(),
    ])
    transcripts = [e for e in seen if isinstance(e, SpeechRecognized)]
    assert len(transcripts) == 1


async def test_misma_pregunta_en_turnos_distintos_si_se_publica():
    seen = await _collect([
        UserTextChunk(text="¿Cómo estás?"),
        TurnComplete(),
        UserTextChunk(text="¿Cómo estás?"),
        TurnComplete(),
    ])
    transcripts = [e for e in seen if isinstance(e, SpeechRecognized)]
    assert len(transcripts) == 2


async def test_refinado_incremental_se_publica_completo():
    seen = await _collect([
        UserTextChunk(text="¿Quién ganó"),
        UserTextChunk(text="¿Quién ganó el último Mundial?"),
        TurnComplete(),
    ])
    texts = [e.text for e in seen if isinstance(e, SpeechRecognized)]
    assert texts == ["¿Quién ganó", "¿Quién ganó el último Mundial?"]


class FakeSearchTool:
    """buscar_en_internet mínimo: devuelve éxito con contenido fijo."""
    name = "buscar_en_internet"
    description = "búsqueda fake"
    parameters = {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}}

    async def execute(self, context, **kwargs):
        from src.tools.base import ToolResult
        return ToolResult(success=True, content="España campeón del Mundial 2026 (fuente web en vivo).")


async def test_respuesta_de_busqueda_lleva_instruccion_de_override():
    """Anti 'parametric fallback': el FunctionResponse de buscar_en_internet
    debe incluir la instrucción crítica de responder solo con los datos."""
    from src.providers.base import ToolCallItem, ToolCallRequest

    bus = EventBus()
    bus.subscribe_all(lambda e: None)
    assistant = Assistant(provider=None, registry=ToolRegistry(), event_bus=bus)
    assistant.registry.register(FakeSearchTool())

    session = FakeSession([
        ToolCallRequest(calls=[ToolCallItem(id="fc1", name="buscar_en_internet",
                                            args={"query": "quién ganó el último mundial"})]),
        TurnComplete(),
    ])
    task = asyncio.create_task(
        assistant.receive_and_route(session, asyncio.Queue(), asyncio.Queue())
    )
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert session.tool_responses, "no se envió FunctionResponse"
    response_payload = session.tool_responses[0][0].response
    assert "datos_obtenidos_ahora_mismo_de_internet" in response_payload
    assert "España campeón" in response_payload["datos_obtenidos_ahora_mismo_de_internet"]
    assert "instruccion_critica" in response_payload
