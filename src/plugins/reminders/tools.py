from typing import Dict, Any
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.reminders.scheduler import AsyncReminderScheduler


class CrearRecordatorioTool(BaseTool):
    """Herramienta para programar recordatorios, temporizadores o alarmas."""

    def __init__(self, scheduler: AsyncReminderScheduler):
        self.scheduler = scheduler

    @property
    def name(self) -> str:
        return "crear_recordatorio"

    @property
    def description(self) -> str:
        return (
            "Programa un recordatorio o temporizador en el sistema. "
            "Úsalo cuando el usuario te pida avisarle, recordarle algo o poner una alarma en X segundos/minutos/horas."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "segundos": {
                    "type": "INTEGER",
                    "description": "Tiempo en segundos desde ahora para disparar la alarma (ej. 300 para 5 minutos, 3600 para 1 hora)."
                },
                "mensaje": {
                    "type": "STRING",
                    "description": "El texto o motivo del recordatorio (ej. 'Sacar la pizza del horno', 'Revisar logs del servidor')."
                }
            },
            "required": ["segundos", "mensaje"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        segundos = float(kwargs.get("segundos", 60))
        mensaje = kwargs.get("mensaje", "Recordatorio").strip()

        if segundos <= 0:
            return ToolResult(success=False, content="El tiempo debe ser mayor a 0 segundos.")

        reminder = self.scheduler.add_reminder(segundos, mensaje)
        minutos = int(segundos // 60)
        segs_restantes = int(segundos % 60)

        tiempo_str = ""
        if minutos > 0:
            tiempo_str += f"{minutos} minuto(s) "
        if segs_restantes > 0 or minutos == 0:
            tiempo_str += f"{segs_restantes} segundo(s)"

        return ToolResult(
            success=True,
            content=f"Recordatorio programado para dentro de {tiempo_str.strip()} ({reminder.human_trigger_time}): '{mensaje}' (ID: {reminder.id})."
        )


class ListarRecordatoriosTool(BaseTool):
    """Herramienta para consultar los recordatorios activos pendientes."""

    def __init__(self, scheduler: AsyncReminderScheduler):
        self.scheduler = scheduler

    @property
    def name(self) -> str:
        return "listar_recordatorios"

    @property
    def description(self) -> str:
        return "Lista todos los recordatorios y alarmas activas pendientes en el sistema."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "OBJECT", "properties": {}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        pending = self.scheduler.list_pending()
        if not pending:
            return ToolResult(success=True, content="No tienes ningún recordatorio pendiente.")

        lines = ["Recordatorios pendientes:"]
        for r in pending:
            secs = int(r.seconds_remaining)
            mins = secs // 60
            lines.append(f"- ID {r.id}: '{r.message}' en {mins}m {secs%60}s ({r.human_trigger_time})")

        return ToolResult(success=True, content="\n".join(lines))


class CancelarRecordatorioTool(BaseTool):
    """Herramienta para cancelar un recordatorio por su ID."""

    def __init__(self, scheduler: AsyncReminderScheduler):
        self.scheduler = scheduler

    @property
    def name(self) -> str:
        return "cancelar_recordatorio"

    @property
    def description(self) -> str:
        return "Cancela un recordatorio existente usando su ID."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "reminder_id": {
                    "type": "STRING",
                    "description": "El identificador del recordatorio a cancelar."
                }
            },
            "required": ["reminder_id"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        r_id = kwargs.get("reminder_id", "").strip()
        if not r_id:
            return ToolResult(success=False, content="Debes especificar el ID del recordatorio.")

        ok = self.scheduler.cancel_reminder(r_id)
        if ok:
            return ToolResult(success=True, content=f"Recordatorio {r_id} cancelado exitosamente.")
        return ToolResult(success=False, content=f"No se encontró ningún recordatorio activo con el ID {r_id}.")
