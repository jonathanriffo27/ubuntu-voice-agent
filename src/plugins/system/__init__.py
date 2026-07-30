from src.tools.registry import ToolRegistry
from .tools import EstadoSistemaTool, ImprimirConsolaTool, AbrirAplicacionTool

def setup(registry: ToolRegistry, dependencies: dict):
    registry.register(EstadoSistemaTool())
    registry.register(ImprimirConsolaTool())
    registry.register(AbrirAplicacionTool())
