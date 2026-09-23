"""
operar_gui_tarea: herramienta de voz que lanza el orquestador OODA (Fase 2).

A diferencia de `interactuar_gui` (una acción puntual), esta herramienta ejecuta
una tarea GUI de varios pasos de forma ASÍNCRONA, como delegar_tarea_desarrollo:
la voz queda libre y el resultado se narra al terminar vía eventos.
"""
from typing import Any, Dict, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.utils.logging import get_logger

logger = get_logger("plugins.gui_tasks")


class OperarGuiTareaTool(BaseTool):
    """Lanza tareas GUI multi-paso en segundo plano (bucle OODA + LLM planificador)."""

    def __init__(self, orchestrator=None, client=None, gui_tool=None, model: str = "gemini-3.8-flash-high"):
        # Orquestación perezosa: se construye al primer uso para no tocar D-Bus
        # ni el escritorio en el arranque de Atlas.
        self._orchestrator = orchestrator
        self._client = client
        self._gui_tool = gui_tool
        self._model = model

    def _get_orchestrator(self):
        if self._orchestrator is None:
            if self._client is None or self._gui_tool is None:
                return None
            from src.agents.computer_use import ComputerUseOrchestrator
            self._orchestrator = ComputerUseOrchestrator(
                client=self._client, gui_tool=self._gui_tool, model=self._model,
            )
        return self._orchestrator

    @property
    def name(self) -> str:
        return "operar_gui_tarea"

    @property
    def description(self) -> str:
        return (
            "Ejecuta una TAREA GRÁFICA DE VARIOS PASOS en segundo plano (ej: 'en Telegram busca a mamá "
            "y dile que ya salgo', 'en OnlyOffice crea un documento y guárdalo'). Úsala SOLO cuando el "
            "usuario pida explícitamente operar una aplicación con varios pasos encadenados; para una "
            "acción puntual (un click, una tecla o escribir en un campo) usa interactuar_gui. "
            "Nunca la uses para pagos, compras, borrados ni envíos que el usuario no haya dictado."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "objetivo": {
                    "type": "STRING",
                    "description": "La tarea completa descrita en lenguaje natural, con el criterio de éxito claro."
                },
                "app": {
                    "type": "STRING",
                    "description": "Aplicación sobre la que actuar (opcional, mejora la precisión)."
                }
            },
            "required": ["objetivo"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        objetivo = (kwargs.get("objetivo") or "").strip()
        app = (kwargs.get("app") or "").strip() or None
        if not objetivo:
            return ToolResult(success=False, content="Falta el objetivo de la tarea GUI.")

        orch = self._get_orchestrator()
        if orch is None:
            return ToolResult(
                success=False,
                content="El orquestador de tareas GUI no está disponible "
                        "(falta el subagente desarrollador / modelo de razonamiento)."
            )

        task_id = orch.start(objetivo, app)
        return ToolResult(
            success=True,
            content=(
                f"Tarea GUI iniciada en segundo plano (ID {task_id}). Estoy operando la interfaz: "
                "te aviso cuando termine o si algo bloquea el progreso."
            ),
            metadata={"task_id": task_id},
        )
