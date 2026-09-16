import asyncio
import os
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
async def test_prompt_repetido_en_ventana_corta_se_ignora():
    """Incidente: mismo prompt enviado 3 veces durante una reconexión."""
    mock_assistant = MagicMock()
    mock_assistant.send_text_message = AsyncMock()
    manager = TerminalInteractionManager(assistant=mock_assistant)

    for _ in range(3):
        manager._buffer = list("haz click en ensayo clinico")
        await manager._handle_enter()

    # Solo el primer envío llega a Gemini; los repetidos se ignoran
    mock_assistant.send_text_message.assert_called_once_with("haz click en ensayo clinico")
    # El prompt SÍ queda registrado una sola vez en el historial
    assert manager._history == ["haz click en ensayo clinico"]


@pytest.mark.asyncio
async def test_prompt_repetido_tras_la_ventana_si_se_envia():
    mock_assistant = MagicMock()
    mock_assistant.send_text_message = AsyncMock()
    manager = TerminalInteractionManager(assistant=mock_assistant)
    manager._resend_debounce_s = 0.05

    manager._buffer = list("repetir esto")
    await manager._handle_enter()
    await asyncio.sleep(0.1)  # deja expirar la ventana de debounce
    manager._buffer = list("repetir esto")
    await manager._handle_enter()

    assert mock_assistant.send_text_message.call_count == 2


@pytest.mark.asyncio
async def test_prompts_distintos_no_se_bloquean():
    mock_assistant = MagicMock()
    mock_assistant.send_text_message = AsyncMock()
    manager = TerminalInteractionManager(assistant=mock_assistant)

    manager._buffer = list("prompt uno")
    await manager._handle_enter()
    manager._buffer = list("prompt dos")
    await manager._handle_enter()

    assert mock_assistant.send_text_message.call_count == 2


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


@pytest.mark.asyncio
async def test_process_chunk_arrows_and_keys():
    manager = TerminalInteractionManager(assistant=MagicMock())

    # Escribir 'hola'
    await manager._process_chunk(b"hola")
    assert "".join(manager._buffer) == "hola"
    assert manager._cursor_pos == 4

    # Flecha izquierda dos veces (←)
    await manager._process_chunk(b"\x1b[D")
    await manager._process_chunk(b"\x1b[D")
    assert manager._cursor_pos == 2

    # Insertar 'XX' en la posición 2
    await manager._process_chunk(b"XX")
    assert "".join(manager._buffer) == "hoXXla"
    assert manager._cursor_pos == 4

    # Backspace una vez
    await manager._process_chunk(b"\x7f")
    assert "".join(manager._buffer) == "hoXla"
    assert manager._cursor_pos == 3

    # Flecha derecha (→)
    await manager._process_chunk(b"\x1b[C")
    assert manager._cursor_pos == 4


@pytest.mark.asyncio
async def test_multiline_wrapped_input_and_backspace(monkeypatch):
    import shutil
    # Forzar ancho de terminal a 80 columnas para la prueba
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=(80, 24): os.terminal_size((80, 24)))

    manager = TerminalInteractionManager(assistant=MagicMock())

    # Escribir prompt largo de 120 caracteres (excede las 80 columnas)
    long_text = "crea un informe con los casos de usos mas utiles para un asistente por voz para un sistema operativo con ubuntu y pegalo en un nuevo docuemt"
    await manager._process_chunk(long_text.encode("utf-8"))

    assert len(manager._buffer) == len(long_text)
    # Visible: 2 (prefix) + 140 = 142 chars -> row 1 en 80 cols
    assert manager._last_rendered_rows == 2
    assert manager._last_cursor_row == 1

    # Backspace 8 veces para corregir " docuemt" (el caso exacto del reporte de usuario)
    for _ in range(8):
        await manager._process_chunk(b"\x7f")

    assert "".join(manager._buffer) == long_text[:-8]
    assert manager._cursor_pos == len(long_text) - 8
    assert manager._last_rendered_rows == 2
    assert manager._last_cursor_row == 1

    # Limpiar el área antes de Enter
    manager._clear_input_area()
    assert manager._last_rendered_rows == 1
    assert manager._last_cursor_row == 0


