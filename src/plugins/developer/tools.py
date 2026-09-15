from typing import Dict, Any, Optional
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.agents.developer_agent import DeveloperAgent
from src.security.approval import ApprovalManager


class DelegarTareaDesarrolloTool(BaseTool):
    """Herramienta para delegar tareas complejas de desarrollo al subagente en segundo plano."""

    def __init__(self, developer_agent: DeveloperAgent):
        self.developer_agent = developer_agent

    @property
    def name(self) -> str:
        return "delegar_tarea_desarrollo"

    @property
    def description(self) -> str:
        return (
            "Delega una tarea de programación, desarrollo de nuevos plugins, análisis de código o "
            "resolución de problemas complejos a un subagente especializado (Gemini 3.7 Flash) en segundo plano. "
            "Úsalo cuando el usuario te pida crear una nueva función, modificar código o programar algo."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "instruccion": {
                    "type": "STRING",
                    "description": "La descripción detallada de la tarea a programar o desarrollar."
                }
            },
            "required": ["instruccion"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        instruccion = kwargs.get("instruccion", "").strip()
        if not instruccion:
            return ToolResult(success=False, content="Falta la instrucción de la tarea a delegar.")

        task_id = self.developer_agent.start_background_task(instruccion)
        return ToolResult(
            success=True,
            content=(
                f"Tarea de desarrollo asignada al subagente (ID: {task_id}). "
                "Está trabajando en segundo plano con Gemini 3.7 Flash y solicitará tu aprobación "
                "si necesita crear archivos o ejecutar comandos."
            )
        )


class AprobarAccionTool(BaseTool):
    """Permite aprobar o rechazar acciones críticas solicitadas por el subagente."""

    def __init__(self, approval_manager: ApprovalManager):
        self.approval_manager = approval_manager

    @property
    def name(self) -> str:
        return "aprobar_accion"

    @property
    def description(self) -> str:
        return (
            "Aprueba o rechaza una acción de código o comando shell solicitada por el subagente en segundo plano. "
            "Úsalo cuando el usuario te diga 'apruebo', 'sí, confirma', 'no apruebo', 'rechaza el cambio'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "aprobar": {
                    "type": "BOOLEAN",
                    "description": "True para autorizar y ejecutar la acción, False para cancelarla/rechazarla."
                },
                "request_id": {
                    "type": "STRING",
                    "description": "Identificador de la solicitud (opcional; si no se indica, resuelve la más reciente)."
                }
            },
            "required": ["aprobar"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        val = kwargs.get("aprobar", True)
        if isinstance(val, str):
            aprobar = val.lower().strip() not in ("false", "0", "no", "rechazar", "cancelar")
        else:
            aprobar = bool(val)
        req_id = kwargs.get("request_id")
        estado = "aprobada" if aprobar else "rechazada"

        # 1. Si se proporcionó un request_id, intentar resolverlo directamente
        if req_id:
            ok = self.approval_manager.resolve(req_id, aprobar, resolver="voice")
            if ok:
                return ToolResult(success=True, content=f"Solicitud {req_id} ha sido {estado}.")
            # 2. El ID proporcionado no coincide: NUNCA resolver otra solicitud a
            #    ciegas (riesgo de aprobar algo distinto a lo que el usuario quiso).
            #    Devolver los IDs pendientes para que el modelo rectifique.
            pending = self.approval_manager.list_pending()
            if pending:
                detalle = "\n".join(
                    f"- [{r.id}] ({r.action_type}) {r.description}" for r in pending
                )
                return ToolResult(
                    success=False,
                    content=(
                        f"El ID '{req_id}' no corresponde a ninguna solicitud pendiente. "
                        f"NO se resolvió nada. Solicitudes pendientes:\n{detalle}\n"
                        f"Vuelve a llamar a esta herramienta con el request_id correcto."
                    )
                )
            return ToolResult(
                success=False,
                content=f"No se encontró solicitud pendiente con ID {req_id} ni hay otras solicitudes pendientes."
            )

        # 3. Sin request_id: resolver la más reciente directamente
        resolved_id = self.approval_manager.resolve_latest(aprobar, resolver="voice")
        if resolved_id:
            return ToolResult(success=True, content=f"La acción pendiente [{resolved_id}] ha sido {estado}.")
        return ToolResult(success=False, content="No hay ninguna solicitud de confirmación pendiente en este momento.")


class RecargarPluginsTool(BaseTool):
    """Herramienta para recargar en caliente los plugins de Atlas."""

    def __init__(self, reload_callback):
        self.reload_callback = reload_callback

    @property
    def name(self) -> str:
        return "recargar_plugins"

    @property
    def description(self) -> str:
        return "Recarga en caliente todos los plugins y herramientas del sistema sin reiniciar Atlas."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "OBJECT", "properties": {}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if self.reload_callback:
            count = self.reload_callback()
            return ToolResult(success=True, content=f"Plugins recargados exitosamente. {count} herramientas activas en memoria.")
        return ToolResult(success=True, content="Plugins recargados.")


class ConsultarEstadoTareaTool(BaseTool):
    """
    Permite consultar el estado real de tareas delegadas al subagente de desarrollo
    y de solicitudes de aprobación HITL pendientes.
    Evita que Atlas invente o alucine el progreso de tareas en segundo plano.
    """

    def __init__(self, developer_agent: DeveloperAgent, approval_manager: ApprovalManager):
        self.developer_agent = developer_agent
        self.approval_manager = approval_manager

    @property
    def name(self) -> str:
        return "consultar_estado_tarea"

    @property
    def description(self) -> str:
        return (
            "Consulta el estado REAL de las tareas de desarrollo en segundo plano y las solicitudes de aprobación pendientes. "
            "OBLIGATORIO usarlo SIEMPRE que el usuario pregunte por el avance, estado o progreso de una tarea delegada. "
            "NUNCA respondas sobre el estado de una tarea sin antes usar esta herramienta."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "task_id": {
                    "type": "STRING",
                    "description": "ID de la tarea a consultar (opcional; si no se indica, devuelve todas las activas)."
                }
            }
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        import json
        task_id = kwargs.get("task_id")

        # Estado de las tareas del subagente
        task_status = self.developer_agent.get_task_status(task_id)

        # Estado de las aprobaciones HITL pendientes
        pending_approvals = self.approval_manager.list_pending()
        approvals_info = []
        for req in pending_approvals:
            import time
            elapsed = time.time() - req.created_at
            approvals_info.append({
                "request_id": req.id,
                "action_type": req.action_type,
                "description": req.description,
                "status": req.status,
                "elapsed_seconds": round(elapsed, 1),
                "timeout_seconds": req.timeout_seconds,
                "remaining_seconds": round(max(0, req.timeout_seconds - elapsed), 1)
            })

        result = {
            "tareas_subagente": task_status,
            "aprobaciones_pendientes": approvals_info if approvals_info else "Ninguna",
            "nota": (
                "Si hay aprobaciones pendientes, la tarea del subagente está BLOQUEADA "
                "esperando que el usuario apruebe. Infórmale al usuario que debe aprobar la acción."
            ) if approvals_info else "No hay bloqueos. Si la tarea está 'running', sigue trabajando normalmente."
        }

        return ToolResult(success=True, content=json.dumps(result, ensure_ascii=False, indent=2))
