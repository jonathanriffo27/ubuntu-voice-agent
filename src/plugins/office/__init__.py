from src.tools.registry import ToolRegistry
from .tools import CrearDocumentoOfficeTool, EscribirTextoOfficeTool, ControlarTecladoOfficeTool, EditarDocumentoInteligenteTool

def setup(registry: ToolRegistry, dependencies: dict):
    registry.register(CrearDocumentoOfficeTool())
    registry.register(EscribirTextoOfficeTool())
    registry.register(ControlarTecladoOfficeTool())
    registry.register(EditarDocumentoInteligenteTool())
