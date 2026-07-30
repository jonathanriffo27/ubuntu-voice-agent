from src.tools.registry import ToolRegistry
from .tools import GuardarNotaTool, BorrarNotaTool, GuardarPerfilTool

def setup(registry: ToolRegistry, dependencies: dict):
    manager = dependencies.get("knowledge_manager")
    if manager:
        registry.register(GuardarNotaTool(manager=manager))
        registry.register(BorrarNotaTool(manager=manager))
        registry.register(GuardarPerfilTool(manager=manager))
