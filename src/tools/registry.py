from typing import Dict, List, Optional
from .base import BaseTool

class ToolRegistry:
    """
    Registro central de herramientas.
    El cerebro y los proveedores consultan este registro para saber qué herramientas existen.
    """
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"La herramienta '{tool.name}' ya está registrada.")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def get_all_tools(self) -> List[BaseTool]:
        return list(self._tools.values())
