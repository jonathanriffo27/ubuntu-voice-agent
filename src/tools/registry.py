from typing import Dict, List, Optional
from .base import BaseTool


class ToolRegistry:
    """
    Registro central de herramientas.
    El cerebro y los proveedores consultan este registro para saber qué herramientas existen.
    Soporta registro, reemplazo y desregistro dinámico para recarga en caliente.
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"La herramienta '{tool.name}' ya está registrada.")
        self._tools[tool.name] = tool

    def register_or_replace(self, tool: BaseTool) -> None:
        """Registra o reemplaza una herramienta existente."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Elimina una herramienta del registro."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def clear(self) -> None:
        """Limpia todas las herramientas registradas."""
        self._tools.clear()

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def get_all_tools(self) -> List[BaseTool]:
        return list(self._tools.values())
