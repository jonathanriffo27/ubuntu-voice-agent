import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.events.bus import EventBus
from src.events.base import SessionStarted, SessionReconnected, ConversationContext
from src.providers.base import BaseProvider, ProviderSession


class MockProvider(BaseProvider):
    def __init__(self):
        self.connect_calls = 0

    def connect(self, system_prompt: str, tools: list):
        self.connect_calls += 1
        raise ConnectionError("Mock network drop")


def test_assistant_reconnect_parameters():
    provider = MockProvider()
    registry = ToolRegistry()
    event_bus = EventBus()

    assistant = Assistant(
        provider=provider,
        registry=registry,
        event_bus=event_bus,
        max_reconnect_attempts=3,
        reconnect_initial_backoff=0.01,
        reconnect_max_backoff=0.05
    )

    assert assistant.max_reconnect_attempts == 3
    assert assistant.reconnect_initial_backoff == 0.01
    assert assistant.reconnect_max_backoff == 0.05


class FakeMockSession(ProviderSession):
    def __init__(self):
        self.sent_texts = []

    async def send_audio(self, data: bytes, sample_rate: int = 16000) -> None:
        pass

    async def send_video(self, data: bytes, mime_type: str = "image/jpeg") -> None:
        pass

    async def send_text(self, text: str, end_of_turn: bool = False) -> None:
        self.sent_texts.append((text, end_of_turn))

    async def end_turn(self) -> None:
        pass

    async def send_tool_response(self, responses) -> None:
        pass

    async def receive(self):
        # Simular desconexión 1008
        raise ConnectionResetError("1008 None. The operation was aborted.")
        yield


class MultiConnectMockProvider(BaseProvider):
    def __init__(self):
        self.sessions = []
        self.call_count = 0

    @asynccontextmanager
    async def connect(self, system_prompt: str, tools: list):
        self.call_count += 1
        sess = FakeMockSession()
        self.sessions.append(sess)
        yield sess


@pytest.mark.asyncio
async def test_assistant_silent_reconnection_events_and_greeting():
    provider = MultiConnectMockProvider()
    registry = ToolRegistry()
    event_bus = EventBus()

    events_received = []
    event_bus.subscribe_all(lambda e: events_received.append(e))

    assistant = Assistant(
        provider=provider,
        registry=registry,
        event_bus=event_bus,
        max_reconnect_attempts=2,
        reconnect_initial_backoff=0.01,
        reconnect_max_backoff=0.02
    )

    # Mock audio streams y hardware
    assistant.in_stream = MagicMock()
    assistant.out_stream = MagicMock()
    assistant.p = MagicMock()
    assistant.recorder = MagicMock()
    assistant.recorder.listen = AsyncMock()
    assistant.player = MagicMock()
    assistant.player.play = AsyncMock()

    with patch("pyaudio.PyAudio"), \
         patch("src.brain.assistant.play_sound") as mock_sound, \
         patch("src.voice.recorder.AudioRecorder.load_wake_word", new_callable=AsyncMock), \
         patch("src.voice.recorder.AudioRecorder.calibrate", new_callable=AsyncMock):

        await assistant.run_async()

    # Verificar que el proveedor fue llamado 2 veces
    assert provider.call_count == 2
    assert len(provider.sessions) == 2

    # Primer intento: debe enviar la directiva inicial y reproducir sonido ready
    session1 = provider.sessions[0]
    assert len(session1.sent_texts) == 1
    assert "DIRECTIVA INICIAL" in session1.sent_texts[0][0]
    assert mock_sound.call_count == 1

    # Segundo intento (reconexión): NO debe enviar directiva inicial ni reproducir sonido
    session2 = provider.sessions[1]
    assert len(session2.sent_texts) == 0

    # Eventos: Debe haber un SessionStarted y luego un SessionReconnected
    started_events = [e for e in events_received if isinstance(e, SessionStarted)]
    reconnected_events = [e for e in events_received if isinstance(e, SessionReconnected)]

    assert len(started_events) == 1
    assert len(reconnected_events) == 1
    assert reconnected_events[0].attempt == 2


class FlappingReconnectProvider(BaseProvider):
    """1ª conexión vive y muere por idle (1008); la reconexión falla rápido
    (simula el handle de session resumption caducado: bug real — tras ~15 min
    idle Atlas moría con 'Se excedió el número máximo de reconexiones')."""

    def __init__(self):
        self.reset_calls = 0
        self.connect_calls = 0

    def reset_session_handle(self):
        self.reset_calls += 1

    @asynccontextmanager
    async def connect(self, system_prompt: str, tools: list):
        self.connect_calls += 1
        if self.connect_calls == 1:
            yield FakeMockSession()  # muere de inmediato con 1008
        elif self.connect_calls == 2:
            raise ConnectionError("1008 policy violation: expired session handle")
        else:
            raise KeyboardInterrupt  # salida limpia para acabar el bucle


@pytest.mark.asyncio
async def test_reconnect_descarta_handle_caducado_y_no_muere():
    provider = FlappingReconnectProvider()
    assistant = Assistant(
        provider=provider,
        registry=ToolRegistry(),
        event_bus=EventBus(),
        max_reconnect_attempts=None,     # producción: ilimitado
        reconnect_initial_backoff=0.01,
        reconnect_max_backoff=0.02,
    )
    assistant.in_stream = MagicMock()
    assistant.out_stream = MagicMock()
    assistant.p = MagicMock()
    assistant.recorder = MagicMock()
    assistant.recorder.listen = AsyncMock()
    assistant.player = MagicMock()
    assistant.player.play = AsyncMock()

    with patch("pyaudio.PyAudio"), \
         patch("src.brain.assistant.play_sound"), \
         patch("src.voice.recorder.AudioRecorder.load_wake_word", new_callable=AsyncMock), \
         patch("src.voice.recorder.AudioRecorder.calibrate", new_callable=AsyncMock):
        await assistant.run_async()

    assert provider.reset_calls >= 1     # el handle vencido se descartó
    assert provider.connect_calls >= 3   # y Atlas siguió intentando en vez de morir
