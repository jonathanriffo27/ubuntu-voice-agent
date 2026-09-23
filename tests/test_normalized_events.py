import pytest
from src.providers.base import (
    AudioChunk, TextChunk, UserTextChunk, ToolCallItem, ToolCallRequest,
    Interrupted, TurnComplete, ToolResponseItem
)
from src.providers.gemini_session import GeminiSession


class MockGeminiPart:
    def __init__(self, text=None, inline_data=None):
        self.text = text
        self.inline_data = inline_data


class MockInlineData:
    def __init__(self, data=b"fake_pcm"):
        self.data = data


class MockServerContent:
    def __init__(self, interrupted=False, model_turn=None, turn_complete=False):
        self.interrupted = interrupted
        self.model_turn = model_turn
        self.turn_complete = turn_complete


class MockModelTurn:
    def __init__(self, parts):
        self.parts = parts


class MockFunctionCall:
    def __init__(self, id, name, args):
        self.id = id
        self.name = name
        self.args = args


class MockToolCall:
    def __init__(self, function_calls):
        self.function_calls = function_calls


class MockGeminiMsg:
    def __init__(self, server_content=None, tool_call=None):
        self.server_content = server_content
        self.tool_call = tool_call


class MockNativeGeminiSession:
    def __init__(self, messages):
        self.messages = messages
        self.sent_responses = []

    async def receive(self):
        for msg in self.messages:
            yield msg

    async def send_tool_response(self, function_responses):
        self.sent_responses.extend(function_responses)


@pytest.mark.asyncio
async def test_gemini_session_yields_normalized_audio_and_text():
    messages = [
        MockGeminiMsg(
            server_content=MockServerContent(
                model_turn=MockModelTurn(parts=[
                    MockGeminiPart(text="Hola mundo"),
                    MockGeminiPart(inline_data=MockInlineData(b"audio_bytes_123"))
                ]),
                turn_complete=True
            )
        )
    ]
    native = MockNativeGeminiSession(messages)
    session = GeminiSession(native)

    events = []
    async for event in session.receive():
        events.append(event)

    assert len(events) == 3
    assert isinstance(events[0], TextChunk)
    assert events[0].text == "Hola mundo"

    assert isinstance(events[1], AudioChunk)
    assert events[1].data == b"audio_bytes_123"

    assert isinstance(events[2], TurnComplete)


class MockTranscription:
    def __init__(self, text="Transcripción de voz en tiempo real"):
        self.text = text


@pytest.mark.asyncio
async def test_gemini_session_yields_output_transcription_text():
    messages = [
        MockGeminiMsg(
            server_content=MockServerContent(
                model_turn=MockModelTurn(parts=[
                    MockGeminiPart(inline_data=MockInlineData(b"pcm_chunk_data"))
                ]),
                turn_complete=False
            )
        ),
        MockGeminiMsg(
            server_content=MockServerContent(
                model_turn=None,
                turn_complete=True
            )
        )
    ]
    # Inyectar output_transcription en el primer mensaje
    messages[0].server_content.output_transcription = MockTranscription("Hola Jonathan, ¿en qué puedo ayudarte?")
    
    native = MockNativeGeminiSession(messages)
    session = GeminiSession(native)

    events = []
    async for event in session.receive():
        events.append(event)

    assert len(events) == 3
    assert isinstance(events[0], TextChunk)
    assert events[0].text == "Hola Jonathan, ¿en qué puedo ayudarte?"
    assert isinstance(events[1], AudioChunk)
    assert events[1].data == b"pcm_chunk_data"
    assert isinstance(events[2], TurnComplete)


@pytest.mark.asyncio
async def test_gemini_session_yields_input_transcription_text():
    messages = [
        MockGeminiMsg(
            server_content=MockServerContent(
                model_turn=None,
                turn_complete=False
            )
        )
    ]
    messages[0].server_content.input_transcription = MockTranscription("hola atlas como estas")

    native = MockNativeGeminiSession(messages)
    session = GeminiSession(native)

    events = []
    async for event in session.receive():
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], UserTextChunk)
    assert events[0].text == "hola atlas como estas"


@pytest.mark.asyncio
async def test_gemini_session_yields_tool_call_request():
    messages = [
        MockGeminiMsg(
            tool_call=MockToolCall(function_calls=[
                MockFunctionCall(id="call_1", name="buscar_en_internet", args={"query": "clima hoy"})
            ])
        )
    ]
    native = MockNativeGeminiSession(messages)
    session = GeminiSession(native)

    events = []
    async for event in session.receive():
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], ToolCallRequest)
    assert len(events[0].calls) == 1
    assert events[0].calls[0].name == "buscar_en_internet"
    assert events[0].calls[0].args == {"query": "clima hoy"}


@pytest.mark.asyncio
async def test_gemini_session_send_tool_response_translation():
    native = MockNativeGeminiSession([])
    session = GeminiSession(native)

    responses = [
        ToolResponseItem(name="buscar_en_internet", id="call_1", response={"result": "Soleado 22C"})
    ]
    await session.send_tool_response(responses)

    assert len(native.sent_responses) == 1
    assert native.sent_responses[0].name == "buscar_en_internet"
    assert native.sent_responses[0].id == "call_1"
    assert native.sent_responses[0].response == {"result": "Soleado 22C"}


class MockServerContent38:
    """server_content del protocolo 3.8+: expone generation_complete, turn_complete
    e interaction_status (siempre enviada junto a turn_complete, según docstring del SDK)."""
    def __init__(self, generation_complete=False, turn_complete=False, interaction_status=None,
                 interrupted=False, model_turn=None):
        self.generation_complete = generation_complete
        self.turn_complete = turn_complete
        self.interaction_status = interaction_status
        self.interrupted = interrupted
        self.model_turn = model_turn


@pytest.mark.asyncio
async def test_gemini38_strict_mode_emite_un_solo_turncomplete_por_turno():
    """Bug real: la Live API 3.8 emite generation_complete Y turn_complete por cada
    turno (el segundo espera a que termine el playback). Mapear ambos duplicaba el
    evento y re-despertaba a Atlas (FOLLOW_UP) tras un sueño deliberado."""
    from google.genai import types
    messages = [
        # Fin de generación (el modelo sigue reproduciendo/terminando playback)
        MockGeminiMsg(server_content=MockServerContent38(
            generation_complete=True, interaction_status=None)),
        # Cierre real del turno: interaction_status=IDLE siempre acompaña a turn_complete
        MockGeminiMsg(server_content=MockServerContent38(
            turn_complete=True, interaction_status=types.InteractionStatus.IDLE)),
    ]
    session = GeminiSession(MockNativeGeminiSession(messages), strict_turn_end=True)

    events = [e async for e in session.receive()]

    turn_completes = [e for e in events if isinstance(e, TurnComplete)]
    assert len(turn_completes) == 1  # un solo cierre por turno, no dos


@pytest.mark.asyncio
async def test_gemini38_strict_mode_no_cierra_turno_mientras_in_progress():
    """Guía de migración 3.8: turn_complete ya no equivale a sesión ociosa; con
    interaction_status == IN_PROGRESS el servidor sigue procesando y el cliente
    NO debe considerar el turno cerrado (ni dormir, ni abrir follow-up)."""
    from google.genai import types
    messages = [
        MockGeminiMsg(server_content=MockServerContent38(
            turn_complete=True, interaction_status=types.InteractionStatus.IN_PROGRESS)),
    ]
    session = GeminiSession(MockNativeGeminiSession(messages), strict_turn_end=True)

    events = [e async for e in session.receive()]

    assert not any(isinstance(e, TurnComplete) for e in events)


@pytest.mark.asyncio
async def test_gemini_legacy_mode_conserva_cierre_por_generation_complete():
    """Modelos pre-3.8 (sin interaction_status estricto): generation_complete
    solo (sin turn_complete) debe seguir cerrando el turno como antes."""
    messages = [
        MockGeminiMsg(server_content=MockServerContent38(generation_complete=True)),
    ]
    session = GeminiSession(MockNativeGeminiSession(messages), strict_turn_end=False)

    events = [e async for e in session.receive()]

    assert sum(isinstance(e, TurnComplete) for e in events) == 1
