import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.ui.terminal_input import TerminalInteractionManager
from src.security.approval import ApprovalManager


@pytest.mark.asyncio
async def test_terminal_prompt_sending():
    mock_assistant = MagicMock()
    mock_assistant.send_text_message = AsyncMock()

    manager = TerminalInteractionManager(assistant=mock_assistant)
    manager._buffer = list("¿A cuánto está el dólar en Chile?")

    await manager._handle_enter()

    mock_assistant.send_text_message.assert_called_once_with("¿A cuánto está el dólar en Chile?")
    assert len(manager._buffer) == 0


@pytest.mark.asyncio
async def test_terminal_approval_resolution():
    mock_assistant = MagicMock()
    mock_approval = MagicMock()
    mock_approval.list_pending.return_value = ["req123"]
    mock_approval.resolve_latest = MagicMock(return_value="req123")

    manager = TerminalInteractionManager(assistant=mock_assistant, approval_manager=mock_approval)

    # 1. Enter con línea vacía -> Aprueba
    manager._buffer = []
    await manager._handle_enter()
    mock_approval.resolve_latest.assert_called_with(True, resolver="terminal")

    # 2. 'n' -> Rechaza
    mock_approval.resolve_latest.reset_mock()
    manager._buffer = list("no")
    await manager._handle_enter()
    mock_approval.resolve_latest.assert_called_with(False, resolver="terminal")


@pytest.mark.asyncio
async def test_terminal_slash_commands_and_mute():
    mock_assistant = MagicMock()
    mock_recorder = MagicMock()
    mock_recorder.is_paused = False
    mock_recorder.silence_threshold = 10000

    manager = TerminalInteractionManager(assistant=mock_assistant, recorder=mock_recorder)

    # 1. /mute
    manager._buffer = list("/mute")
    await manager._handle_enter()
    assert mock_recorder.is_paused is True

    # 2. /sensibilidad +
    manager._buffer = list("/sensibilidad +")
    await manager._handle_enter()
    assert mock_recorder.silence_threshold == 10500


def test_toggle_mute_and_sensitivity_methods():
    mock_recorder = MagicMock()
    mock_recorder.is_paused = False
    mock_recorder.silence_threshold = 8000

    manager = TerminalInteractionManager(assistant=MagicMock(), recorder=mock_recorder)

    manager._toggle_mute()
    assert mock_recorder.is_paused is True

    manager._adjust_sensitivity(500)
    assert mock_recorder.silence_threshold == 8500

    manager._adjust_sensitivity(-1000)
    assert mock_recorder.silence_threshold == 7500


def test_cursor_movement_and_inline_editing():
    manager = TerminalInteractionManager(assistant=MagicMock())
    manager._buffer = list("hola atlas")
    manager._cursor_pos = len(manager._buffer)

    # Simular mover cursor 5 posiciones a la izquierda (hasta después de 'hola ')
    manager._cursor_pos = 5

    # Insertar 'amigo ' en la posición 5
    for c in "amigo ":
        manager._buffer.insert(manager._cursor_pos, c)
        manager._cursor_pos += 1

    assert "".join(manager._buffer) == "hola amigo atlas"
    assert manager._cursor_pos == 11

    # Backspace (eliminar el espacio antes de atlas)
    manager._buffer.pop(manager._cursor_pos - 1)
    manager._cursor_pos -= 1

    assert "".join(manager._buffer) == "hola amigoatlas"


@pytest.mark.asyncio
async def test_prompt_history_tracking():
    mock_assistant = MagicMock()
    mock_assistant.send_text_message = AsyncMock()
    manager = TerminalInteractionManager(assistant=mock_assistant)

    manager._buffer = list("primer prompt")
    await manager._handle_enter()

    manager._buffer = list("segundo prompt")
    await manager._handle_enter()

    assert manager._history == ["primer prompt", "segundo prompt"]

