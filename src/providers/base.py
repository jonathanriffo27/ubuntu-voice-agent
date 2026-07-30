from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any, Dict, List

class BaseProvider(ABC):
    """
    Interfaz abstracta para los proveedores de modelos (Gemini, OpenAI, Claude, etc.).
    """
    
    @abstractmethod
    def connect(self, system_prompt: str, tools: List[Dict]) -> Any:
        """
        Inicia la conexión con el modelo y devuelve un manejador de sesión (context manager asíncrono).
        En futuras iteraciones (Hito 5), la sesión devuelta también deberá implementar 
        una interfaz común independientemente del modelo subyacente.
        """
        pass
