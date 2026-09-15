from src.tools.registry import ToolRegistry
from .tools import InteractuarGuiTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    registry.register(InteractuarGuiTool(
        approval_manager=dependencies.get("approval_manager"),
    ))
