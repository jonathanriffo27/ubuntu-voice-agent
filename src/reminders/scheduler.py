import asyncio
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Optional
from src.events.bus import EventBus
from src.events.base import ConversationContext, ReminderCreated, ReminderTriggered
from src.utils.logging import get_logger
from src.voice.player import play_sound

logger = get_logger("reminders.scheduler")


@dataclass
class Reminder:
    id: str
    message: str
    trigger_at: float  # Unix timestamp
    created_at: float  # Unix timestamp
    status: str = "pending"  # pending, triggered, cancelled

    @property
    def human_trigger_time(self) -> str:
        return datetime.fromtimestamp(self.trigger_at).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.trigger_at - time.time())


class AsyncReminderScheduler:
    """
    Gestor y planificador asíncrono de recordatorios y temporizadores con persistencia en disco.
    Emite notificaciones de escritorio de Linux (notify-send), sonidos de sistema y eventos EventBus.
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        storage_file: str = "atlas_reminders.json"
    ):
        self.event_bus = event_bus or EventBus()
        self.storage_file = storage_file
        self.reminders: Dict[str, Reminder] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._load_storage()

    def _load_storage(self) -> None:
        """Carga recordatorios persistidos desde el archivo JSON."""
        if not os.path.exists(self.storage_file):
            return

        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    r = Reminder(**item)
                    if r.status == "pending" and r.trigger_at > time.time() - 3600:
                        self.reminders[r.id] = r
            logger.info(f"Cargados {len(self.reminders)} recordatorios activos desde {self.storage_file}.")
        except Exception as e:
            logger.error(f"Error cargando recordatorios: {e}")

    def _save_storage(self) -> None:
        """Guarda atómicamente el estado de los recordatorios."""
        tmp_file = f"{self.storage_file}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in self.reminders.values()], f, indent=2)
            os.replace(tmp_file, self.storage_file)
        except Exception as e:
            logger.error(f"Error guardando recordatorios: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except Exception:
                    pass

    def add_reminder(self, seconds_from_now: float, message: str) -> Reminder:
        """Programa un nuevo recordatorio relativo al momento actual."""
        now = time.time()
        trigger_time = now + seconds_from_now
        reminder_id = str(uuid.uuid4())[:8]

        reminder = Reminder(
            id=reminder_id,
            message=message,
            trigger_at=trigger_time,
            created_at=now,
            status="pending"
        )
        self.reminders[reminder_id] = reminder
        self._save_storage()

        logger.info(f"⏰ Recordatorio creado [{reminder_id}]: '{message}' en {int(seconds_from_now)}s ({reminder.human_trigger_time})")

        self.event_bus.publish(
            ReminderCreated(
                ConversationContext(),
                reminder_id=reminder_id,
                message=message,
                trigger_at=reminder.human_trigger_time
            )
        )
        return reminder

    def list_pending(self) -> List[Reminder]:
        """Devuelve todos los recordatorios pendientes ordenados cronológicamente."""
        return sorted(
            [r for r in self.reminders.values() if r.status == "pending"],
            key=lambda r: r.trigger_at
        )

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancela un recordatorio por su ID."""
        if reminder_id in self.reminders and self.reminders[reminder_id].status == "pending":
            self.reminders[reminder_id].status = "cancelled"
            self._save_storage()
            logger.info(f"Recordatorio [{reminder_id}] cancelado.")
            return True
        return False

    async def _worker_loop(self):
        """Bucle en segundo plano que evalúa cada segundo los recordatorios cumplidos."""
        logger.info("Motor de recordatorios y cron iniciado.")
        while self._is_running:
            try:
                now = time.time()
                for reminder in list(self.reminders.values()):
                    if reminder.status == "pending" and now >= reminder.trigger_at:
                        await self._trigger_reminder(reminder)
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en worker de recordatorios: {e}")
                await asyncio.sleep(2.0)

    async def _trigger_reminder(self, reminder: Reminder) -> None:
        """Dispara la alarma, la notificación de escritorio y el evento."""
        reminder.status = "triggered"
        self._save_storage()

        logger.info(f"🔔 ¡ALARMA DE RECORDATORIO! [{reminder.id}]: {reminder.message}")

        # 1. Notificación nativa de escritorio de Ubuntu (notify-send)
        try:
            subprocess.Popen([
                "notify-send",
                "-a", "Atlas Assistant",
                "-u", "critical",
                "-i", "appointment-soon",
                "🔔 Recordatorio de Atlas",
                reminder.message
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # 2. Sonido del sistema
        play_sound("resume")

        # 3. Notificar al EventBus para actualizar Dashboard y Consola
        self.event_bus.publish(
            ReminderTriggered(
                ConversationContext(),
                reminder_id=reminder.id,
                message=reminder.message
            )
        )

    def start(self) -> None:
        """Inicia el scheduler en el loop de eventos actual."""
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    def stop(self) -> None:
        """Detiene el scheduler."""
        self._is_running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        self._worker_task = None
