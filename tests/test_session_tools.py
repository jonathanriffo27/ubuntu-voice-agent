import pytest
from unittest.mock import MagicMock

from src.plugins.session_control.tools import EntrarEnEsperaTool
from src.plugins.session_control import setup
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry


def _ctx():
    return ToolContext(config=MagicMock())


@pytest.mark.asyncio
async def test_entrar_en_espera_confirma_cierre():
    tool = EntrarEnEsperaTool()
    result = await tool.execute(_ctx())
    assert result.success
    assert "espera" in result.content


def test_entrar_en_espera_sin_parametros():
    tool = EntrarEnEsperaTool()
    assert tool.name == "entrar_en_espera"
    assert tool.parameters is None  # convención: sin parámetros (como obtener_estado_sistema)


def test_setup_registra_la_tool():
    registry = ToolRegistry()
    setup(registry, {})
    assert registry.get_tool("entrar_en_espera") is not None
