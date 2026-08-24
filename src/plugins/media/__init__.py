from src.tools.registry import ToolRegistry
from .tools import ControlarMusicaTool, ReproducirMusicaTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    registry.register(ControlarMusicaTool())
    registry.register(ReproducirMusicaTool())
