"""Plugin de control de sesión de voz: cierre explícito de conversación."""
from src.tools.registry import ToolRegistry
from .tools import EntrarEnEsperaTool


def setup(registry: ToolRegistry, dependencies: dict):
    registry.register(EntrarEnEsperaTool())
