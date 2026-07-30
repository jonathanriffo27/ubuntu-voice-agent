from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseTool(ABC):
    """
    Interfaz base para todas las herramientas del asistente.
    Define un contrato agnóstico al proveedor de LLM.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre de la herramienta (debe ser único, sin espacios)."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripción detallada de cuándo y cómo usar la herramienta."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """
        Esquema JSON de los parámetros que recibe la herramienta.
        Si no requiere parámetros, devolver None o un dict vacío.
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Lógica de ejecución asíncrona de la herramienta."""
        pass
