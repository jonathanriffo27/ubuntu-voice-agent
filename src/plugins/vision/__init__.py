from src.tools.registry import ToolRegistry
from .tools import AnalizarPantallaTool
from src.vision.service import MssScreenService

def setup(registry: ToolRegistry, dependencies: dict):
    # Inicializamos el servicio y la herramienta.
    # Podría inyectarse desde dependencias si el usuario quiere otro ScreenService (mock, etc)
    screen_service = dependencies.get("screen_service") or MssScreenService()
    registry.register(AnalizarPantallaTool(screen_service=screen_service))
