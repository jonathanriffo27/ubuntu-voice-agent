import asyncio
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.security.approval import ApprovalManager
from src.agents.client import CLIProxyClient
from src.agents.tools import AgentCodeTools
from src.agents.developer_agent import DeveloperAgent
from src.events.bus import EventBus
from src.tools.registry import ToolRegistry
from src.plugins.loader import reload_plugins, discover_and_register_plugins
from src.plugins.developer.tools import DelegarTareaDesarrolloTool, AprobarAccionTool, RecargarPluginsTool
from src.tools.base import ToolContext
from src.config.models import AtlasConfig


@pytest.mark.asyncio
async def test_approval_manager_resolve_approved():
    bus = EventBus()
    mgr = ApprovalManager(event_bus=bus)

    async def _approve_later():
        await asyncio.sleep(0.05)
        pending = mgr.list_pending()
        assert len(pending) == 1
        mgr.resolve(pending[0].id, True, resolver="test")

    asyncio.create_task(_approve_later())
    approved = await mgr.request_approval("shell_command", "Test command", "echo hi", timeout=1.0)
    assert approved is True


@pytest.mark.asyncio
async def test_approval_manager_resolve_rejected():
    bus = EventBus()
    mgr = ApprovalManager(event_bus=bus)

    async def _reject_later():
        await asyncio.sleep(0.05)
        mgr.resolve_latest(False, resolver="voice")

    asyncio.create_task(_reject_later())
    approved = await mgr.request_approval("file_write", "Test file", "code", timeout=1.0)
    assert approved is False


@pytest.mark.asyncio
async def test_approval_manager_timeout():
    bus = EventBus()
    mgr = ApprovalManager(event_bus=bus)
    approved = await mgr.request_approval("file_write", "Test timeout", "code", timeout=0.1)
    assert approved is False
    assert len(mgr.list_pending()) == 0


@pytest.mark.asyncio
async def test_agent_code_tools_read_and_write(tmp_path):
    mgr = ApprovalManager()
    tools = AgentCodeTools(approval_manager=mgr)

    # Test read nonexistent
    res = await tools.execute_tool("leer_archivo", {"ruta_relativa": "nonexistent_file_xyz.py"})
    assert "Error" in res

    # Test write with rejection
    with patch.object(mgr, "request_approval", AsyncMock(return_value=False)):
        res = await tools.execute_tool("escribir_archivo", {"ruta_relativa": "test_tmp.py", "contenido": "print(1)"})
        assert "rechazada" in res

    # Test write with approval
    test_rel_path = "tests/test_scratch_agent.py"
    with patch.object(mgr, "request_approval", AsyncMock(return_value=True)):
        res = await tools.execute_tool("escribir_archivo", {"ruta_relativa": test_rel_path, "contenido": "# tmp code\n"})
        assert "exitosamente" in res
        # Cleanup
        full_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", test_rel_path))
        if os.path.exists(full_path):
            os.remove(full_path)


@pytest.mark.asyncio
async def test_developer_agent_run_task():
    client = CLIProxyClient()
    mgr = ApprovalManager()
    tools = AgentCodeTools(approval_manager=mgr)
    bus = EventBus()

    agent = DeveloperAgent(client=client, code_tools=tools, event_bus=bus, model="gemini-3.8-flash-high")

    mock_response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "He terminado la tarea con éxito.",
                "tool_calls": None,
                "reasoning_content": "Reasoning steps..."
            }
        }]
    }

    with patch.object(client, "chat_completion", AsyncMock(return_value=mock_response)):
        result = await agent.run_task("Crea un script de prueba")
        assert "éxito" in result


@pytest.mark.asyncio
async def test_hot_reload_plugins():
    registry = ToolRegistry()
    dependencies = {}
    count = reload_plugins(registry, dependencies)
    assert count > 0
    assert registry.get_tool("controlar_musica") is not None


@pytest.mark.asyncio
async def test_developer_voice_tools():
    mgr = ApprovalManager()
    client = CLIProxyClient()
    agent = DeveloperAgent(client=client, code_tools=AgentCodeTools(mgr))
    ctx = ToolContext(config=AtlasConfig())

    delegar_tool = DelegarTareaDesarrolloTool(agent)
    res_delegar = await delegar_tool.execute(ctx, instruccion="Crear plugin de clima")
    assert res_delegar.success is True
    assert "asignada" in res_delegar.content

    aprobar_tool = AprobarAccionTool(mgr)
    res_aprobar_vacio = await aprobar_tool.execute(ctx, aprobar=True)
    assert res_aprobar_vacio.success is False

    recargar_tool = RecargarPluginsTool(reload_callback=lambda: 10)
    res_recargar = await recargar_tool.execute(ctx)
    assert res_recargar.success is True
    assert "10 herramientas" in res_recargar.content
