from src.tools.registry import ToolRegistry
from .tools import ListarArchivosProyectoTool, LeerArchivoProyectoTool


def setup(registry: ToolRegistry, dependencies: dict):
    """Plugin workspace: lectura read-only del propio proyecto (sin dependencias)."""
    registry.register(ListarArchivosProyectoTool())
    registry.register(LeerArchivoProyectoTool())
