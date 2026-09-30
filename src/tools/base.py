from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass
from src.config.models import AtlasConfig
from src.events.base import ConversationContext
from src.events.bus import EventBus

@dataclass
class ToolContext:
    config: AtlasConfig
    event_bus: EventBus = None
    conversation_context: ConversationContext = None
    # Último mensaje del usuario (voz transcrita o texto de HUD/terminal) y su
    # timestamp. Lo usa la confirmación dura de comandos destructivos: la
    # barrera no puede depender solo de lo que el modelo crea haber entendido.
    last_user_utterance: str = ""
    last_user_utterance_time: float = 0.0

@dataclass
class ToolResult:
    success: bool
    content: str
    metadata: Dict[str, Any] = None

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
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Lógica de ejecución asíncrona de la herramienta."""
        pass
