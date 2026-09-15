import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from src.events.bus import EventBus
from src.events.base import (
    Event, TaskDelegated, TaskCompleted, ToolStarted, ToolSucceeded,
    ToolFailed, SpeechRecognized, ResponseGenerated, ApprovalRequested,
    ApprovalResolved, SessionStarted, SessionEnded
)
from src.utils.logging import get_logger

logger = get_logger("brain.trajectory")


@dataclass
class TrajectoryStep:
    """Representa un paso atómico en la trayectoria de ejecución de Atlas."""
    id: str
    timestamp: float
    iso_time: str
    event_type: str
    role: str  # user, model, subagent, tool, system, approval
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    thinking: Optional[str] = None


class TrajectoryManager:
    """
    Gestor de Trayectoria y Memoria de Sesión de Atlas (Inspirado en el Trajectory Log de DeepSeek Harness).
    Registra de forma inmutable cada prompt, decisión, pensamiento (thinking),
    llamada a herramienta y delegación a subagentes.
    Permite:
    1. Persistir el contexto entre reinicios en 'atlas_trajectory.json'.
    2. Inyectar un resumen de la sesión reciente en el System Prompt de Atlas.
    3. Servir la trayectoria completa al Dashboard Web HUD para su visualización interactiva.
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        file_path: str = "atlas_trajectory.json",
        max_steps: int = 100
    ):
        self.event_bus = event_bus
        self.file_path = file_path
        self.max_steps = max_steps
        self._steps: List[TrajectoryStep] = []
        # Throttle de escritura a disco: los eventos llegan en ráfagas (streaming
        # de texto, audio, tools) y escribir en cada uno congela el event loop.
        self._save_interval: float = 2.0
        self._last_save_time: float = 0.0
        self._pending_save: bool = False
        self._load()

        if self.event_bus:
            self.event_bus.subscribe_all(self._on_event)

    def _load(self) -> None:
        """Carga los pasos de la trayectoria desde disco si existen."""
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("steps", []):
                    self._steps.append(TrajectoryStep(**item))
            logger.debug(f"Trayectoria cargada: {len(self._steps)} pasos históricos.")
        except Exception as e:
            logger.warning(f"No se pudo cargar trayectoria previa: {e}")
            self._steps = []

    def _save(self) -> None:
        """Guarda los pasos en disco de forma atómica."""
        tmp_path = f"{self.file_path}.tmp"
        try:
            payload = {
                "version": 1,
                "updated_at": time.time(),
                "steps": [asdict(s) for s in self._steps[-self.max_steps:]]
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            logger.error(f"Error guardando trayectoria atómica: {e}")

    def add_step(
        self,
        event_type: str,
        role: str,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
        thinking: Optional[str] = None
    ) -> TrajectoryStep:
        """Añade un nuevo paso a la trayectoria en memoria y disco."""
        step = TrajectoryStep(
            id=str(uuid.uuid4())[:8],
            timestamp=time.time(),
            iso_time=time.strftime("%H:%M:%S", time.localtime()),
            event_type=event_type,
            role=role,
            summary=summary,
            details=details or {},
            thinking=thinking
        )
        self._steps.append(step)
        if len(self._steps) > self.max_steps:
            self._steps = self._steps[-self.max_steps:]

        # Guardado con throttle: como máximo una escritura a disco cada _save_interval
        now = time.time()
        if now - self._last_save_time >= self._save_interval:
            self._save()
            self._last_save_time = now
            self._pending_save = False
        else:
            self._pending_save = True
        return step

    def _on_event(self, event: Event) -> None:
        """Manejador de eventos para registrar la trayectoria en tiempo real."""
        try:
            if isinstance(event, SpeechRecognized):
                self.add_step(
                    event_type="SpeechRecognized",
                    role="user",
                    summary=f"Usuario dijo: '{event.text}'",
                    details={"text": event.text}
                )
            elif isinstance(event, ResponseGenerated):
                self.add_step(
                    event_type="ResponseGenerated",
                    role="model",
                    summary=f"Atlas respondió: '{event.text[:80]}...'" if len(event.text) > 80 else f"Atlas respondió: '{event.text}'",
                    details={"text": event.text}
                )
            elif isinstance(event, ToolStarted):
                self.add_step(
                    event_type="ToolStarted",
                    role="tool",
                    summary=f"Ejecutando herramienta '{event.tool_name}'",
                    details={"tool": event.tool_name, "args": event.arguments}
                )
            elif isinstance(event, ToolSucceeded):
                self.add_step(
                    event_type="ToolSucceeded",
                    role="tool",
                    summary=f"Herramienta '{event.tool_name}' completada con éxito",
                    details={"tool": event.tool_name, "result": str(event.result)[:300]}
                )
            elif isinstance(event, ToolFailed):
                self.add_step(
                    event_type="ToolFailed",
                    role="tool",
                    summary=f"Herramienta '{event.tool_name}' falló: {event.error}",
                    details={"tool": event.tool_name, "error": event.error}
                )
            elif isinstance(event, TaskDelegated):
                self.add_step(
                    event_type="TaskDelegated",
                    role="subagent",
                    summary=f"Subagente [{event.task_id}] iniciado: '{event.instruction}'",
                    details={"task_id": event.task_id, "instruction": event.instruction, "model": event.model}
                )
            elif isinstance(event, TaskCompleted):
                status = "éxito" if event.success else "fallo"
                self.add_step(
                    event_type="TaskCompleted",
                    role="subagent",
                    summary=f"Subagente [{event.task_id}] finalizó ({status})",
                    details={"task_id": event.task_id, "success": event.success, "result": event.result[:300]}
                )
            elif isinstance(event, ApprovalRequested):
                self.add_step(
                    event_type="ApprovalRequested",
                    role="approval",
                    summary=f"Solicitud HITL [{event.request_id}] ({event.action_type}): {event.description}",
                    details={"request_id": event.request_id, "action": event.action_type, "payload": event.payload[:200]}
                )
            elif isinstance(event, ApprovalResolved):
                res = "APROBADA" if event.approved else "RECHAZADA"
                self.add_step(
                    event_type="ApprovalResolved",
                    role="approval",
                    summary=f"Solicitud HITL [{event.request_id}] {res} por {event.resolver}",
                    details={"request_id": event.request_id, "approved": event.approved, "resolver": event.resolver}
                )
        except Exception as e:
            logger.debug(f"Error procesando evento en TrajectoryManager: {e}")
        finally:
            # Flush garantizado al cerrar la sesión para no perder los últimos eventos
            if isinstance(event, SessionEnded) and self._pending_save:
                self._save()
                self._pending_save = False

    def get_steps(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Devuelve los pasos más recientes en formato serializable."""
        return [asdict(s) for s in self._steps[-limit:]]

    def get_recent_summary(self, max_items: int = 4) -> str:
        """
        Genera un resumen textual conciso de las últimas interacciones
        para inyectar en el System Prompt y mantener continuidad entre sesiones.
        """
        if not self._steps:
            return ""

        meaningful = [
            s for s in self._steps
            if s.role in ("user", "subagent", "tool")
        ]
        recent = meaningful[-max_items:]
        if not recent:
            return ""

        lines = []
        for s in recent:
            lines.append(f"- [{s.iso_time}] {s.summary}")
        return "\n".join(lines)
