import pytest
from unittest.mock import MagicMock
from src.events.base import (
    ConversationContext, SessionStarted, SessionEnded,
    AssistantTextChunk, TurnCompleted, SpeechRecognized,
    ToolExecuting, ToolSucceeded, ToolFailed, UserInterrupted
)
from src.events.bus import EventBus
from src.ui.cli import CLIInterface


def test_cli_interface_initialization():
    bus = EventBus()
    cli = CLIInterface(bus)
    assert cli.event_bus == bus
    assert not cli._in_text_stream


def test_cli_interface_assistant_text_stream(capsys):
    bus = EventBus()
    cli = CLIInterface(bus)
    ctx = ConversationContext()

    # Emitir fragmentos continuos
    bus.publish(AssistantTextChunk(ctx, text="Hola "))
    bus.publish(AssistantTextChunk(ctx, text="mundo, "))
    bus.publish(AssistantTextChunk(ctx, text="estoy en línea."))

    captured = capsys.readouterr().out
    assert "Atlas:" in captured
    assert "Hola mundo, estoy en línea." in captured
    assert cli._in_text_stream

    # Al recibir fin de turno, debe hacer flush
    bus.publish(TurnCompleted(ctx))
    assert not cli._in_text_stream


def test_cli_interface_speech_and_interruption(capsys):
    bus = EventBus()
    cli = CLIInterface(bus)
    ctx = ConversationContext()

    bus.publish(SpeechRecognized(ctx, text="¿Cuál es el clima?"))
    captured = capsys.readouterr().out
    assert "Tú (voz):" in captured
    assert "¿Cuál es el clima?" in captured

    # Iniciar stream y luego interrumpir
    bus.publish(AssistantTextChunk(ctx, text="El clima está..."))
    bus.publish(UserInterrupted(ctx))
    captured2 = capsys.readouterr().out
    assert "interrumpido" in captured2
    assert not cli._in_text_stream


def test_cli_interface_tool_lifecycle(capsys):
    bus = EventBus()
    cli = CLIInterface(bus)
    ctx = ConversationContext()

    bus.publish(ToolExecuting(ctx, tool_name="buscar_en_internet", arguments={"query": "noticias hoy"}))
    captured = capsys.readouterr().out
    assert "buscar_en_internet" in captured

    bus.publish(ToolSucceeded(ctx, tool_name="buscar_en_internet", result={"result": "Noticias del día..."}))
    captured2 = capsys.readouterr().out
    assert "Noticias del día..." in captured2


def test_cli_interface_session_reconnected(capsys):
    from src.events.base import SessionReconnected
    bus = EventBus()
    cli = CLIInterface(bus)
    ctx = ConversationContext()

    bus.publish(SessionReconnected(ctx, attempt=2))
    captured = capsys.readouterr().out
    assert "Reconectado" in captured
    assert "Conexión con Gemini restablecida" in captured
    # Verificar que NO imprime el banner gigante de inicio
    assert "ATLAS AI RUNTIME" not in captured


def test_cli_banner_muestra_modelo_y_respaldo(capsys):
    bus = EventBus()
    CLIInterface(bus, voice_model="gemini-3.8-live", fallback_model="gemini-3.1-flash-live-preview")
    bus.publish(SessionStarted(ConversationContext()))

    out = capsys.readouterr().out
    assert "gemini-3.8-live" in out
    assert "respaldo: gemini-3.1-flash-live-preview" in out
    assert "Live Preview" not in out


def test_cli_notificaciones_de_modelo_y_sistema(capsys):
    from src.events.base import SystemNotification
    bus = EventBus()
    CLIInterface(bus)
    ctx = ConversationContext()

    bus.publish(SystemNotification(ctx, message="usando modelo de respaldo", kind="model"))
    out = capsys.readouterr().out
    assert "[Modelo]" in out
    assert "usando modelo de respaldo" in out

    bus.publish(SystemNotification(ctx, message="Documento listo"))
    out2 = capsys.readouterr().out
    assert "[Sistema]" in out2
    assert "Documento listo" in out2
