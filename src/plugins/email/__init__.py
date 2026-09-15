from src.tools.registry import ToolRegistry
from .tools import EnviarCorreoTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    registry.register(EnviarCorreoTool())
