import asyncio
from typing import Any, Dict, Callable
from .base import BaseTool, ToolContext, ToolResult
import tools

class LegacyToolWrapper(BaseTool):
    """
    Envoltura temporal para migrar las herramientas antiguas al nuevo sistema de plugins.
    Recibe la función original de `tools.py` como dependencia.
    """
    def __init__(self, name: str, description: str, parameters: Dict[str, Any], func: Callable):
        self._name = name
        self._description = description
        self._parameters = parameters
        self._func = func

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> Dict[str, Any]:
        return self._parameters

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        # tools.py usa funciones síncronas, así que las ejecutamos en un thread.
        res = await asyncio.to_thread(self._func, **kwargs)
        # Convertir a ToolResult
        if isinstance(res, dict):
            return ToolResult(success=True, content=str(res.get("result", res)), metadata=res)
        return ToolResult(success=True, content=str(res))

def get_legacy_tools() -> list[BaseTool]:
    """Retorna la lista de herramientas envueltas listas para el registro."""
    return [
        LegacyToolWrapper(
            name="obtener_estado_sistema",
            description="Obtiene la hora actual.",
            parameters=None,
            func=tools.obtener_estado_sistema
        ),
        LegacyToolWrapper(
            name="buscar_en_internet",
            description="Realiza una búsqueda en internet y devuelve un resumen de los resultados.",
            parameters={"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "La consulta a buscar en internet"}}, "required": ["query"]},
            func=tools.buscar_en_internet
        ),
        LegacyToolWrapper(
            name="imprimir_en_consola",
            description="Imprime un texto, código, tabla o información detallada en la terminal para que el usuario pueda leerlo. Útil cuando la respuesta es muy larga o contiene formato que se pierde al hablar.",
            parameters={"type": "OBJECT", "properties": {"texto": {"type": "STRING", "description": "El texto a imprimir"}}, "required": ["texto"]},
            func=tools.imprimir_en_consola
        ),
        LegacyToolWrapper(
            name="abrir_aplicacion",
            description="Abre una aplicación instalada en el sistema (ej. calculadora, terminal, code) o abre páginas web conocidas en el navegador (ej. gmail, youtube, whatsapp).",
            parameters={"type": "OBJECT", "properties": {"nombre": {"type": "STRING", "description": "El nombre de la aplicación o sitio web a abrir"}}, "required": ["nombre"]},
            func=tools.abrir_aplicacion
        )
    ]
