import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.tools.base import ToolContext
from src.plugins.whatsapp.tools import EnviarWhatsAppTool, _find_whatsapp_desktop


@pytest.mark.asyncio
async def test_enviar_whatsapp_tool_metadata():
    tool = EnviarWhatsAppTool()
    assert tool.name == "enviar_whatsapp"
    assert "WhatsApp" in tool.description
    assert "mensaje" in tool.parameters["properties"]
    assert "destinatario" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_enviar_whatsapp_empty_fields():
    tool = EnviarWhatsAppTool()
    ctx = ToolContext(config=None)
    
    # Falta mensaje
    res1 = await tool.execute(ctx, mensaje="", destinatario="+56 9 5790 9790")
    assert not res1.success
    assert "mensaje" in res1.content.lower()

    # Falta destinatario
    res2 = await tool.execute(ctx, mensaje="Hola", destinatario="")
    assert not res2.success
    assert "destinatario" in res2.content.lower()


@pytest.mark.asyncio
async def test_find_whatsapp_desktop_detection():
    # Debe encontrar el desktop en el sistema real
    path = _find_whatsapp_desktop()
    assert path is not None
    assert "whatsapp" in path.lower() or "hnpfjngllnobngcgfapefoaidbinmjnm" in path


@pytest.mark.asyncio
async def test_enviar_whatsapp_loading_timeout():
    tool = EnviarWhatsAppTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.whatsapp.tools._find_whatsapp_desktop", return_value="/fake/path.desktop"), \
         patch("src.plugins.whatsapp.tools.shutil.which", return_value="/usr/bin/ydotool"), \
         patch.object(tool.a11y_sensor, "wait_for_whatsapp_ready", return_value=(False, "Tiempo de espera agotado cargando chats")), \
         patch("src.plugins.whatsapp.tools._launch_or_focus_whatsapp", new_callable=AsyncMock):
        
        result = await tool.execute(ctx, mensaje="Hola", destinatario="+56 9 5790 9790")
        assert not result.success
        assert "no se pudo enviar" in result.content.lower()
        assert "cargando" in result.content.lower() or "tiempo de espera" in result.content.lower()


@pytest.mark.asyncio
async def test_enviar_whatsapp_mocked_success():
    tool = EnviarWhatsAppTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.whatsapp.tools._find_whatsapp_desktop", return_value="/fake/path.desktop"), \
         patch("src.plugins.whatsapp.tools.shutil.which", return_value="/usr/bin/ydotool"), \
         patch.object(tool.a11y_sensor, "wait_for_whatsapp_ready", return_value=(True, "Listo")), \
         patch.object(tool.a11y_sensor, "wait_for_chat_open", return_value=True), \
         patch.object(tool.a11y_sensor, "verify_message_sent", return_value=True), \
         patch("src.plugins.whatsapp.tools._emit_keys", new_callable=AsyncMock) as mock_keys, \
         patch("src.plugins.whatsapp.tools._type_text", new_callable=AsyncMock) as mock_type, \
         patch("src.plugins.whatsapp.tools._copy_to_clipboard", new_callable=AsyncMock) as mock_clip, \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        
        proc_mock = MagicMock()
        proc_mock.wait = AsyncMock()
        mock_exec.return_value = proc_mock

        result = await tool.execute(ctx, mensaje="Mensaje de prueba", destinatario="+56 9 5790 9790")
        
        assert result.success
        assert "+56 9 5790 9790" in result.content
        assert mock_keys.called
        assert mock_type.called
        assert mock_clip.called


@pytest.mark.asyncio
async def test_abrir_whatsapp_tool_success():
    from src.plugins.whatsapp.tools import AbrirWhatsAppTool
    tool = AbrirWhatsAppTool()
    assert tool.name == "abrir_whatsapp"
    assert "WhatsApp" in tool.description

    ctx = ToolContext(config=None)
    with patch("src.plugins.whatsapp.tools._find_whatsapp_desktop", return_value="/fake/path.desktop"), \
         patch("src.plugins.whatsapp.tools._launch_or_focus_whatsapp", new_callable=AsyncMock) as mock_launch:
        res = await tool.execute(ctx)
        assert res.success
        assert "abierto" in res.content.lower()
        mock_launch.assert_awaited_once_with("/fake/path.desktop")


@pytest.mark.asyncio
async def test_abrir_whatsapp_tool_not_found():
    from src.plugins.whatsapp.tools import AbrirWhatsAppTool
    tool = AbrirWhatsAppTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.whatsapp.tools._find_whatsapp_desktop", return_value=None):
        res = await tool.execute(ctx)
        assert not res.success
        assert "no se encontró" in res.content.lower()
        assert "pwa" in res.content.lower()


@pytest.mark.asyncio
async def test_cerrar_whatsapp_tool_success():
    from src.plugins.whatsapp.tools import CerrarWhatsAppTool
    tool = CerrarWhatsAppTool()
    assert tool.name == "cerrar_whatsapp"

    ctx = ToolContext(config=None)
    mock_proc = MagicMock()
    mock_proc.info = {"pid": 5555, "cmdline": ["brave", "--app-id=hnpfjngllnobngcgfapefoaidbinmjnm"]}

    with patch("psutil.process_iter", return_value=[mock_proc]), \
         patch("psutil.wait_procs", return_value=([mock_proc], [])):
        res = await tool.execute(ctx)
        assert res.success
        assert "cerrado" in res.content.lower()
        mock_proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_cerrar_whatsapp_tool_not_running():
    from src.plugins.whatsapp.tools import CerrarWhatsAppTool
    tool = CerrarWhatsAppTool()
    ctx = ToolContext(config=None)

    with patch("psutil.process_iter", return_value=[]):
        res = await tool.execute(ctx)
        assert res.success
        assert "no estaba en ejecución" in res.content.lower()


