"""
Monitor de anomalías de comportamiento (Fase 6, COMPUTER_USE_PLAN.md;
patrón "monitor model" del Lockdown de OpenAI).

Vigila la SECUENCIA de acciones del sistema (no el contenido de una sola) y
levanta una alerta cuando el patrón se sale de lo razonable:

1. **Ráfaga total**: demasiadas acciones en la ventana deslizante
   (comportamiento de bucle o agente "entusiasmado").
2. **Ráfaga de riesgo**: muchas acciones Tier>=2 en la ventana (escrituras,
   clicks, comandos). Los humanos no operan a ese ritmo ni el buen flujo de
   voz debería necesitarlo.
3. **Repetición**: la misma acción con el mismo payload N veces seguidas
   (bucle de reintento estéril / prompt-injection induciendo repetición).

Cuando dispara, la acción en curso se PAUSA y pasa a HITL: el usuario decide
si el comportamiento era legítimo (reset del monitor) o no (bloquea el turno).

Fail-open documentado: sin historial suficiente no hay falsos positivos; las
reglas solo disparan con evidencia acumulada en la ventana.
"""
import hashlib
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from src.security.policy import SecurityPolicy, RiskTier


@dataclass
class AnomalyAlert:
    reason: str
    rule: str          # "burst_total" | "burst_risky" | "repeat"
    evidence: str      # descripción compacta de la evidencia


def monitor_action_name(tool_name: str, args: Optional[dict]) -> str:
    """
    Nombre de acción para el monitor: incluye la sub-acción cuando existe
    (navegador_web + {'accion':'elementos'} -> 'navegador_web.elementos').

    Las herramientas clasifican riesgo así internamente; si el monitor solo ve
    el nombre pelado, lecturas inocuas cuentan como 'risky' y una ráfaga
    legítima (ej: leer Gmail) dispara falsos positivos de burst_risky.
    """
    if isinstance(args, dict):
        sub = args.get("accion")
        if isinstance(sub, str) and sub.strip():
            return f"{tool_name}.{sub.strip().lower()}"
    return tool_name


class ActionMonitor:
    """Ventana deslizante de acciones con reglas de anomalía."""

    def __init__(self, policy: Optional[SecurityPolicy] = None,
                 window_seconds: float = 60.0,
                 burst_total: int = 10,
                 burst_risky: int = 5,
                 repeat_threshold: int = 3,
                 clock=time.time):
        self.policy = policy or SecurityPolicy()
        self.window = window_seconds
        self.burst_total = burst_total
        self.burst_risky = burst_risky
        self.repeat_threshold = repeat_threshold
        self._clock = clock
        self._events: Deque[Tuple[float, str, int, str]] = deque()  # (ts, accion, tier, hash)
        self._alerted = False  # una sola alerta exige reset explícito (anti-spam HITL)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Limpia la ventana y desarma la alerta (tras visto bueno humano)."""
        self._events.clear()
        self._alerted = False

    @property
    def alerted(self) -> bool:
        return self._alerted

    @staticmethod
    def _payload_hash(payload: str) -> str:
        return hashlib.sha256(payload.encode()).hexdigest()[:8]

    # ------------------------------------------------------------------
    def record(self, action: str, payload: str = "") -> Optional[AnomalyAlert]:
        """
        Registra una acción y devuelve AnomalyAlert si dispara una regla.
        Una vez disparada, sigue devolviendo la alerta hasta reset().
        """
        now = self._clock()
        tier = int(self.policy.classify(action, payload))
        phash = self._payload_hash(payload)
        self._events.append((now, f"{action}|{phash}", tier, phash))

        # Purgar fuera de ventana
        while self._events and self._events[0][0] < now - self.window:
            self._events.popleft()

        if self._alerted:
            return AnomalyAlert(
                reason="Monitor en pausa: alerta previa sin resolver.",
                rule="pause",
                evidence=f"{len(self._events)} acciones en ventana",
            )

        # Regla 3 — repetición consecutiva
        tail = [ev for ev in list(self._events)[-self.repeat_threshold:]]
        if (len(tail) == self.repeat_threshold
                and len({ev[1] for ev in tail}) == 1):
            self._alerted = True
            name = tail[0][1].split("|", 1)[0]
            return AnomalyAlert(
                reason=f"Acción '{name}' repetida {self.repeat_threshold} veces seguidas "
                       "con el mismo payload (posible bucle).",
                rule="repeat",
                evidence=name,
            )

        # Regla 1 — ráfaga total
        if len(self._events) >= self.burst_total:
            self._alerted = True
            return AnomalyAlert(
                reason=f"{len(self._events)} acciones en {int(self.window)}s "
                       "(ritmo anómalo para operación por voz).",
                rule="burst_total",
                evidence=f"window={len(self._events)}",
            )

        # Regla 2 — ráfaga de riesgo
        risky = [ev for ev in self._events if ev[2] >= RiskTier.LOCAL_WRITE]
        if len(risky) >= self.burst_risky:
            self._alerted = True
            return AnomalyAlert(
                reason=f"{len(risky)} acciones de escritura/riesgo en {int(self.window)}s.",
                rule="burst_risky",
                evidence=f"risky={len(risky)}/{len(self._events)}",
            )

        return None
