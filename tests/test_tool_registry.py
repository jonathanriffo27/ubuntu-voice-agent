import pytest
from typing import Dict, Any
from src.tools.registry import ToolRegistry
from src.tools.base import BaseTool, ToolContext, ToolResult


class FakeTool(BaseTool):
    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=True, content="ok")


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = FakeTool()
        reg.register(tool)
        assert reg.get_tool("fake_tool") is tool

    def test_duplicate_raises(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        with pytest.raises(ValueError):
            reg.register(FakeTool())

    def test_get_nonexistent_returns_none(self):
        reg = ToolRegistry()
        assert reg.get_tool("non_existent") is None

    def test_get_all_tools(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        assert len(reg.get_all_tools()) == 1
