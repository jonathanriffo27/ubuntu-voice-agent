from src.events.bus import EventBus
from src.events.base import ConversationContext, SessionStarted, ToolStarted


class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe(SessionStarted, lambda e: received.append(e))

        ctx = ConversationContext()
        bus.publish(SessionStarted(ctx))

        assert len(received) == 1
        assert isinstance(received[0], SessionStarted)

    def test_subscribe_all(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e))

        ctx = ConversationContext()
        bus.publish(SessionStarted(ctx))
        bus.publish(ToolStarted(ctx, tool_name="test", arguments={}))

        assert len(received) == 2

    def test_listener_exception_does_not_break_others(self):
        bus = EventBus()
        received = []

        def bad_listener(e):
            raise ValueError("boom")

        bus.subscribe(SessionStarted, bad_listener)
        bus.subscribe(SessionStarted, lambda e: received.append(e))

        ctx = ConversationContext()
        bus.publish(SessionStarted(ctx))

        assert len(received) == 1
