from src.tools.registry import ToolRegistry
from .tools import CrearDocumentoOfficeTool

def setup(registry: ToolRegistry, dependencies: dict):
    registry.register(CrearDocumentoOfficeTool())
