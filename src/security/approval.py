import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from src.events.bus import EventBus
from src.events.base import ConversationContext, ApprovalRequested, ApprovalResolved
from src.utils.logging import get_logger

logger = get_logger("security.approval")


@dataclass
class ApprovalRequest:
    id: str
    action_type: str
    description: str
    payload: str
    created_at: float
    timeout_seconds: float
    future: asyncio.Future
    status: str = "pending"  # pending, approved, rejected, timeout


class ApprovalManager:
    """
    Compuerta de Aprobación Humana (Human-In-The-Loop).
    Gestiona solicitudes de confirmación para acciones del subagente desarrollador.
    Soporta resolución por voz, dashboard web HUD y terminal.
    """

    def __init__(self, event_bus: Optional[EventBus] = None):
        self.event_bus = event_bus or EventBus()
        self._pending_requests: Dict[str, ApprovalRequest] = {}

    async def request_approval(
        self,
        action_type: str,
        description: str,
        payload: str = "",
        timeout: float = 60.0
    ) -> bool:
        """
        Publica una solicitud de aprobación y bloquea hasta que el usuario responda o expire el timeout.
        """
        req_id = str(uuid.uuid4())[:6].lower()
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        req = ApprovalRequest(
            id=req_id,
            action_type=action_type,
            description=description,
            payload=payload,
            created_at=time.time(),
            timeout_seconds=timeout,
            future=future
        )
        self._pending_requests[req_id] = req

        logger.info(f"⚠️ [HITL] Aprobación solicitada [{req_id}] ({action_type}): {description}")

        self.event_bus.publish(
            ApprovalRequested(
                ConversationContext(),
                request_id=req_id,
                action_type=action_type,
                description=description,
                payload=payload,
                timeout_seconds=timeout
            )
        )

        try:
            approved = await asyncio.wait_for(future, timeout=timeout)
            return approved
        except asyncio.TimeoutError:
            logger.warning(f"⏰ [HITL] Aprobación [{req_id}] expiró por timeout ({timeout}s). Rechazada automáticamente.")
            req.status = "timeout"
            self.event_bus.publish(
                ApprovalResolved(
                    ConversationContext(),
                    request_id=req_id,
                    approved=False,
                    resolver="timeout"
                )
            )
            return False
        finally:
            self._pending_requests.pop(req_id, None)

    def resolve(self, request_id: str, approved: bool, resolver: str = "user") -> bool:
        """
        Resuelve una petición pendiente (aprobada o rechazada).
        """
        req = self._pending_requests.get(request_id)
        if not req or req.status != "pending":
            return False

        req.status = "approved" if approved else "rejected"
        if not req.future.done():
            req.future.set_result(approved)

        logger.info(f"✅ [HITL] Aprobación [{request_id}] {'APROBADA' if approved else 'RECHAZADA'} por {resolver}.")

        self.event_bus.publish(
            ApprovalResolved(
                ConversationContext(),
                request_id=request_id,
                approved=approved,
                resolver=resolver
            )
        )
        return True

    def resolve_latest(self, approved: bool, resolver: str = "voice") -> Optional[str]:
        """
        Resuelve la solicitud pendiente más reciente (útil cuando el usuario dice simplemente 'Atlas, apruebo').
        """
        pending = list(self._pending_requests.values())
        if not pending:
            return None

        latest = sorted(pending, key=lambda r: r.created_at, reverse=True)[0]
        if self.resolve(latest.id, approved, resolver=resolver):
            return latest.id
        return None

    def list_pending(self) -> List[ApprovalRequest]:
        """Devuelve la lista de solicitudes pendientes."""
        return [r for r in self._pending_requests.values() if r.status == "pending"]
