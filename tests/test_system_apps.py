import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.tools.base import ToolContext
from src.plugins.system.tools import (
    AbrirAplicacionTool,
    resolve_desktop_app,
    launch_desktop_app,
    scan_installed_desktop_apps
)


@pytest.mark.asyncio
async def test_abrir_aplicacion_metadata():
    tool = AbrirAplicacionTool()
    assert tool.name == "abrir_aplicacion"
    assert "nombre" in tool.parameters["properties"]
    assert "Prioriza SIEMPRE" in tool.description


@pytest.mark.asyncio
async def test_resolve_desktop_app_whatsapp_variations():
    fake_apps = [
        {
            "id": "brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default",
            "name": "WhatsApp Web",
            "generic_name": "",
            "path": "/home/user/.local/share/applications/brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default.desktop",
            "exec": "brave --app-id=hnpfjngllnobngcgfapefoaidbinmjnm"
        },
        {
            "id": "brave-mail.google.com__mail_-Default",
            "name": "Gmail",
            "generic_name": "",
            "path": "/home/user/.local/share/applications/brave-mail.google.com__mail_-Default.desktop",
            "exec": "brave --app=https://mail.google.com"
        },
        {
            "id": "org.gnome.Calculator",
            "name": "Calculator",
            "generic_name": "",
            "path": "/usr/share/applications/org.gnome.Calculator.desktop",
            "exec": "gnome-calculator"
        }
    ]

    for query in ["whatsapp", "whatsapp web", "whatsapp-pwa", "whatsapp pwa", "pwa whatsapp", "whatsappweb"]:
        match = resolve_desktop_app(query, fake_apps)
        assert match is not None, f"Failed to resolve {query}"
        assert match["name"] == "WhatsApp Web"

    for query in ["gmail", "gmail-pwa", "correo"]:
        match = resolve_desktop_app(query, fake_apps)
        assert match is not None, f"Failed to resolve {query}"
        assert match["name"] == "Gmail"

    for query in ["calculadora", "calc", "calculator"]:
        match = resolve_desktop_app(query, fake_apps)
        assert match is not None, f"Failed to resolve {query}"
        assert match["name"] == "Calculator"


@pytest.mark.asyncio
async def test_abrir_aplicacion_whatsapp_pwa_success():
    tool = AbrirAplicacionTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.whatsapp.tools._find_whatsapp_desktop", return_value="/fake/path.desktop"), \
         patch("src.plugins.whatsapp.tools._launch_or_focus_whatsapp", new_callable=AsyncMock) as mock_launch:
        
        for name in ["whatsapp", "whatsapp web", "whatsapp-pwa", "WhatsApp PWA"]:
            res = await tool.execute(ctx, nombre=name)
            assert res.success
            assert "WhatsApp Web (PWA)" in res.content
        
        assert mock_launch.call_count == 4


@pytest.mark.asyncio
async def test_abrir_aplicacion_installed_desktop_success():
    tool = AbrirAplicacionTool()
    ctx = ToolContext(config=None)

    fake_app = {
        "id": "spotify",
        "name": "Spotify",
        "path": "/usr/share/applications/spotify.desktop",
        "exec": "spotify"
    }

    with patch("src.plugins.system.tools.resolve_desktop_app", return_value=fake_app), \
         patch("src.plugins.system.tools.launch_desktop_app", return_value=True) as mock_launch:
        
        res = await tool.execute(ctx, nombre="spotify")
        assert res.success
        assert "Spotify" in res.content
        mock_launch.assert_called_once_with(fake_app)


@pytest.mark.asyncio
async def test_abrir_aplicacion_not_installed_refuses_browser():
    tool = AbrirAplicacionTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.system.tools.resolve_desktop_app", return_value=None), \
         patch("shutil.which", return_value=None), \
         patch("subprocess.Popen") as mock_popen:
        
        # Una app desconocida como 'notion' no debe abrir el navegador silenciosamente
        res = await tool.execute(ctx, nombre="notion")
        assert not res.success
        assert "no encontré la aplicación local" in res.content.lower()
        assert "para evitar abrirla en el navegador" in res.content.lower()
        mock_popen.assert_not_called()


@pytest.mark.asyncio
async def test_abrir_aplicacion_pure_web_service():
    tool = AbrirAplicacionTool()
    ctx = ToolContext(config=None)

    with patch("subprocess.Popen") as mock_popen:
        res = await tool.execute(ctx, nombre="youtube")
        assert res.success
        assert "navegador" in res.content.lower()
        mock_popen.assert_called_once()


@pytest.mark.asyncio
async def test_cerrar_aplicacion_metadata():
    from src.plugins.system.tools import CerrarAplicacionTool
    tool = CerrarAplicacionTool()
    assert tool.name == "cerrar_aplicacion"
    assert "nombre" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_cerrar_aplicacion_protected_process():
    from src.plugins.system.tools import CerrarAplicacionTool
    tool = CerrarAplicacionTool()
    ctx = ToolContext(config=None)

    res = await tool.execute(ctx, nombre="systemd")
    assert not res.success
    assert "seguridad" in res.content.lower()


@pytest.mark.asyncio
async def test_cerrar_aplicacion_spotify_success():
    from src.plugins.system.tools import CerrarAplicacionTool
    tool = CerrarAplicacionTool()
    ctx = ToolContext(config=None)

    mock_proc = MagicMock()
    mock_proc.info = {"pid": 1234, "name": "spotify", "cmdline": ["/usr/bin/spotify"]}

    # find se llama 2 veces: antes para localizar y después para VERIFICAR
    # (post-cierre no debe quedar nada).
    with patch("src.plugins.system.tools.find_processes_for_app",
               side_effect=[[mock_proc], []]), \
         patch("src.plugins.system.tools.terminate_processes", return_value=1):

        res = await tool.execute(ctx, nombre="spotify")
        assert res.success
        assert "cerrado 'spotify'" in res.content.lower()


@pytest.mark.asyncio
async def test_cerrar_aplicacion_reporta_si_sobreviven_procesos():
    """Bug real: se anunciaba 'cerrada' habiendo matado solo un satélite."""
    from src.plugins.system.tools import CerrarAplicacionTool
    tool = CerrarAplicacionTool()
    ctx = ToolContext(config=None)

    mock_proc = MagicMock()

    with patch("src.plugins.system.tools.find_processes_for_app",
               side_effect=[[mock_proc], [mock_proc]]), \
         patch("src.plugins.system.tools.terminate_processes", return_value=1):
        res = await tool.execute(ctx, nombre="spotify")
        assert not res.success
        assert "quedan" in res.content.lower()


def test_find_processes_no_excluye_apps_hijas_de_atlas():
    """Bug real: Atlas lanzó Spotify (plugin de música) → todo el árbol de
    Spotify quedaba excluido por 'protección' y era imposible cerrarlo.
    La protección solo debe cubrir Atlas, sus ancestros e hijos de
    infraestructura (túnel SSH)."""
    from src.plugins.system import tools as system_tools
    import psutil

    fake_spotify = MagicMock()
    fake_spotify.info = {"pid": 9999, "name": "spotify",
                         "cmdline": ["/usr/share/spotify/spotify"]}

    fake_child = MagicMock()
    fake_child.pid = 9999
    fake_child.cmdline.return_value = ["/usr/share/spotify/spotify"]

    fake_self = MagicMock()
    fake_self.parents.return_value = []
    fake_self.children.return_value = [fake_child]

    with patch.object(psutil, "Process", return_value=fake_self), \
         patch.object(psutil, "process_iter", return_value=[fake_spotify]):
        matches = system_tools.find_processes_for_app("spotify")

    assert [p.info["pid"] for p in matches] == [9999]


@pytest.mark.asyncio
async def test_cerrar_aplicacion_not_running():
    from src.plugins.system.tools import CerrarAplicacionTool
    tool = CerrarAplicacionTool()
    ctx = ToolContext(config=None)

    with patch("src.plugins.system.tools.find_processes_for_app", return_value=[]):
        res = await tool.execute(ctx, nombre="calculadora")
        assert res.success
        assert "no está en ejecución" in res.content.lower()


@pytest.mark.asyncio
async def test_enfocar_aplicacion_tool():
    from src.plugins.system.tools import EnfocarAplicacionTool
    tool = EnfocarAplicacionTool()
    ctx = ToolContext(config=None)

    # Caso sin nombre
    res_vacio = await tool.execute(ctx, nombre="")
    assert not res_vacio.success

    # Caso exitoso mockeando subprocess
    mock_proc = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=0)
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        res = await tool.execute(ctx, nombre="terminal")
        assert res.success
        assert "terminal" in res.content


