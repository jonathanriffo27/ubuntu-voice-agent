import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.tools.base import ToolContext
from src.plugins.email.tools import EnviarCorreoTool, _find_gmail_desktop


@pytest.mark.asyncio
async def test_enviar_correo_tool_metadata():
    tool = EnviarCorreoTool()
    assert tool.name == "enviar_correo"
    assert "correo" in tool.description.lower() or "gmail" in tool.description.lower()
    assert "destinatario" in tool.parameters["properties"]
    assert "asunto" in tool.parameters["properties"]
    assert "cuerpo" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_enviar_correo_empty_destinatario():
    tool = EnviarCorreoTool()
    ctx = ToolContext(config=None)
    
    res = await tool.execute(ctx, destinatario="", asunto="Hola", cuerpo="Cuerpo de prueba")
    assert not res.success
    assert "destinatario" in res.content.lower()


@pytest.mark.asyncio
async def test_find_gmail_desktop_detection():
    path = _find_gmail_desktop()
    assert path is not None
    assert "gmail" in path.lower() or "mail.google.com" in path.lower()


@pytest.mark.asyncio
async def test_enviar_correo_success_mocked():
    tool = EnviarCorreoTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.email.tools.shutil.which", return_value="/usr/bin/ydotool"), \
         patch.object(tool.a11y_sensor, "wait_for_gmail_ready", return_value=(True, "Listo")), \
         patch.object(tool.a11y_sensor, "verify_email_sent", return_value=True), \
         patch("src.plugins.email.tools._emit_keys", new_callable=AsyncMock) as mock_keys, \
         patch("src.plugins.email.tools._type_text", new_callable=AsyncMock) as mock_type, \
         patch("src.plugins.email.tools._launch_or_focus_gmail_compose", new_callable=AsyncMock) as mock_launch:
        
        res = await tool.execute(
            ctx,
            destinatario="jonathan.riffo7@gmail.com",
            asunto="Correo de Prueba",
            cuerpo="Este es un correo de prueba enviado desde Atlas."
        )

        assert res.success
        assert "jonathan.riffo7@gmail.com" in res.content
        assert mock_launch.called
        assert mock_keys.called
