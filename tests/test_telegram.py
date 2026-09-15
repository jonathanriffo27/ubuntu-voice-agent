import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.tools.base import ToolContext
from src.plugins.telegram.tools import EnviarTelegramTool, _find_telegram_desktop


@pytest.mark.asyncio
async def test_enviar_telegram_tool_metadata():
    tool = EnviarTelegramTool()
    assert tool.name == "enviar_telegram"
    assert "Telegram" in tool.description
    assert "mensaje" in tool.parameters["properties"]
    assert "destino" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_enviar_telegram_empty_message():
    tool = EnviarTelegramTool()
    ctx = ToolContext(config=None)
    result = await tool.execute(ctx, mensaje="")
    assert not result.success
    assert "Falta el mensaje" in result.content


def test_find_telegram_desktop_detection():
    # En este sistema existe el binario o desktop de Telegram
    path = _find_telegram_desktop()
    assert path is not None
    assert "telegram" in path.lower()


@pytest.mark.asyncio
async def test_enviar_telegram_contact_mocked():
    tool = EnviarTelegramTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.telegram.tools.shutil.which", return_value="/usr/bin/ydotool"), \
         patch("src.plugins.telegram.tools._find_telegram_desktop", return_value="/home/jonathan/Applications/Telegram/Telegram"), \
         patch("src.plugins.telegram.tools._launch_or_focus_telegram", new_callable=AsyncMock) as mock_launch, \
         patch("src.plugins.telegram.tools._emit_keys", new_callable=AsyncMock) as mock_keys, \
         patch("src.plugins.telegram.tools._type_text", new_callable=AsyncMock) as mock_type, \
         patch("src.plugins.telegram.tools._copy_to_clipboard", new_callable=AsyncMock) as mock_clip, \
         patch.object(tool.a11y_sensor, "wait_for_telegram_ready", return_value=(True, "Telegram listo")):

        result = await tool.execute(ctx, mensaje="Hola YISH, mensaje de prueba", destino="YISH")

        assert result.success
        assert "YISH" in result.content
        assert mock_launch.called
        assert mock_keys.called
        assert mock_type.called
        assert mock_clip.called
