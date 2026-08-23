import pytest
from src.providers.base import (
    AudioChunk, TextChunk, ToolCallItem, ToolCallRequest,
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
