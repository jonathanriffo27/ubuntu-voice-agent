import pytest
from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.events.bus import EventBus
from src.providers.base import BaseProvider


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
