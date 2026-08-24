import pytest
from unittest.mock import AsyncMock, MagicMock
from src.mcp.client import MCPClient
from src.mcp.adapter import MCPToolAdapter
from src.mcp.manager import MCPServerManager
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry
from src.config.models import AtlasConfig


@pytest.mark.asyncio
async def test_mcp_tool_adapter_schema_and_name():
    mock_client = MagicMock(spec=MCPClient)
    mock_client.name = "test_srv"
    mock_client.call_tool = AsyncMock(return_value={"content": [{"type": "text", "text": "resultado mcp"}]})

    tool_info = {
        "name": "leer_archivo",
        "description": "Lee el contenido de un archivo",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ruta": {"type": "string", "description": "Ruta al archivo"}
            },
            "required": ["ruta"]
        }
    }

    adapter = MCPToolAdapter(mock_client, tool_info, prefix="test_srv")
    assert adapter.name == "mcp_test_srv_leer_archivo"
    assert adapter.description == "Lee el contenido de un archivo"
    assert "ruta" in adapter.parameters["properties"]

    ctx = ToolContext(config=AtlasConfig())
    res = await adapter.execute(ctx, ruta="/tmp/demo.txt")

    assert res.success is True
    assert res.content == "resultado mcp"
    mock_client.call_tool.assert_called_once_with("leer_archivo", {"ruta": "/tmp/demo.txt"})


@pytest.mark.asyncio
async def test_mcp_tool_adapter_error_handling():
    mock_client = MagicMock(spec=MCPClient)
    mock_client.name = "failing_srv"
    mock_client.call_tool = AsyncMock(side_effect=RuntimeError("Connection closed"))

    adapter = MCPToolAdapter(mock_client, {"name": "fail_tool"}, prefix="failing")
    ctx = ToolContext(config=AtlasConfig())
    res = await adapter.execute(ctx)

    assert res.success is False
    assert "Error en servidor MCP" in res.content


@pytest.mark.asyncio
async def test_mcp_manager_empty_config():
    manager = MCPServerManager()
    registry = ToolRegistry()
    tools = await manager.load_servers({}, registry)
    assert len(tools) == 0
    await manager.shutdown_all()
