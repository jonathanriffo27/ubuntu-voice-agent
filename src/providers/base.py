from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any, List, Dict
from dataclasses import dataclass, field
from src.tools.base import BaseTool


# ==========================================
# Eventos Normalizados de Streaming (Recepción)
# ==========================================

@dataclass
class AudioChunk:
    """Fragmento de audio PCM recibido del modelo."""
    data: bytes


@dataclass
class TextChunk:
    """Fragmento de texto o transcripción recibido del modelo."""
    text: str


@dataclass
class ToolCallItem:
    """Llamada individual a una herramienta solicitada por el modelo."""
    id: str
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRequest:
    """Conjunto de llamadas a herramientas emitidas por el modelo en un turno."""
    calls: List[ToolCallItem]


@dataclass
class Interrupted:
    """Señal de que el modelo fue interrumpido (por voz o cancelación)."""
    pass


@dataclass
class TurnComplete:
    """Señal de que el modelo completó su turno de respuesta."""
    pass


@dataclass
class ToolResponseItem:
    """Respuesta formateada de una herramienta para enviar al modelo."""
    name: str
    id: str
    response: Dict[str, Any]


# ==========================================
# Interfaces Abstractas
# ==========================================

class ProviderSession(ABC):
    """
    Interfaz unificada para sesiones de streaming bidireccional con modelos LLM.
    Desacopla el core del asistente de los métodos y SDKs específicos de cada proveedor.
    """

    @abstractmethod
    async def send_audio(self, data: bytes, sample_rate: int = 16000) -> None:
        """Envía un chunk de audio PCM al modelo en tiempo real."""
        pass

    @abstractmethod
    async def send_video(self, data: bytes, mime_type: str = "image/jpeg") -> None:
        """Envía un frame de video/captura de pantalla al modelo."""
        pass

    @abstractmethod
    async def send_text(self, text: str, end_of_turn: bool = False) -> None:
        """Envía texto al modelo."""
        pass

    @abstractmethod
    async def end_turn(self) -> None:
        """Señala el fin de turno del usuario."""
        pass

    @abstractmethod
    async def send_tool_response(self, responses: List[ToolResponseItem]) -> None:
        """Envía respuestas de llamadas a herramientas (function calling)."""
        pass

    @abstractmethod
    def receive(self) -> AsyncGenerator[Any, None]:
        """
        Iterador asíncrono que emite eventos normalizados:
        AudioChunk, TextChunk, ToolCallRequest, Interrupted, TurnComplete.
        """
        pass


class BaseProvider(ABC):
    """
    Interfaz abstracta para los proveedores de modelos (Gemini, OpenAI, Claude, etc.).
    """

    @abstractmethod
    def connect(self, system_prompt: str, tools: List[BaseTool]) -> Any:
        """
        Inicia la conexión con el modelo y devuelve un manejador de sesión (context manager asíncrono)
        que produce un ProviderSession.
        """
        pass
