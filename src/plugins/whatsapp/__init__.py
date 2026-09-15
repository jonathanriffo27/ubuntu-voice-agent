from src.tools.registry import ToolRegistry
from .tools import EnviarWhatsAppTool, AbrirWhatsAppTool, CerrarWhatsAppTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    registry.register(EnviarWhatsAppTool())
    registry.register(AbrirWhatsAppTool())
    registry.register(CerrarWhatsAppTool())

