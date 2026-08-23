from src.tools.registry import ToolRegistry
from .tools import EstadoSistemaTool, ImprimirConsolaTool, AbrirAplicacionTool, EnfocarAplicacionTool

def setup(registry: ToolRegistry, dependencies: dict):
    registry.register(EstadoSistemaTool())
    registry.register(ImprimirConsolaTool())
    registry.register(AbrirAplicacionTool())
    registry.register(EnfocarAplicacionTool())
