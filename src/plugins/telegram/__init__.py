from src.tools.registry import ToolRegistry
from .tools import EnviarTelegramTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    registry.register(EnviarTelegramTool())
