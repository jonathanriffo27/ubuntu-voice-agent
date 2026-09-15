from src.tools.registry import ToolRegistry
from .tools import NavegadorWebTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    approval = dependencies.get("approval_manager")
    registry.register(NavegadorWebTool(approval_manager=approval))
