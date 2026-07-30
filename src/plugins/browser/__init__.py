from src.tools.registry import ToolRegistry
from .tools import BuscarEnInternetTool

def setup(registry: ToolRegistry, dependencies: dict):
    registry.register(BuscarEnInternetTool())
