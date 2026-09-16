"""
Política de seguridad por tiers de riesgo para acciones de Atlas.

Tier 1 (READ_ONLY):   lectura pasiva, sin efectos laterales. Se auto-aprueba.
Tier 2 (LOCAL_WRITE): modifica estado local reversible (clicks, escribir texto,
                      crear archivos, abrir apps). Aprobación según config.
Tier 3 (IRREVERSIBLE): enviar mensajes/correos, borrar, git push, pagos,
                      comandos shell arbitrarios. HITL obligatorio, siempre.

La política se define en config/security_policy.yaml. Si el archivo no existe,
se usan los defaults seguros (conservadores) definidos aquí.
"""
import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional

from src.utils.logging import get_logger

logger = get_logger("security.policy")

_POLICY_PATH = os.path.join(
    os.path.dirname(__file__), "../../config/security_policy.yaml"
)


class RiskTier(IntEnum):
    READ_ONLY = 1
    LOCAL_WRITE = 2
    IRREVERSIBLE = 3


# Defaults conservadores: clasifican por nombre de acción/herramienta.
_DEFAULT_RULES = {
    RiskTier.READ_ONLY: [
        "analizar_pantalla", "capturar_pantalla*", "leer_archivo", "listar_directorio",
        "buscar_*", "consultar_*", "que_suena", "listar_*", "leer_*", "status*",
        "obtener_*", "ver_*",
        "navegador_web.leer", "navegador_web.elementos", "navegador_web.pestanas",
        "navegador_web.captura",
    ],
    RiskTier.LOCAL_WRITE: [
        "interactuar_gui", "abrir_*", "cerrar_aplicacion", "reproducir_*",
        "controlar_musica", "escribir_archivo", "crear_*", "editar_*",
        "ajustar_*", "silenciar*", "imprimir_en_consola", "agendar_*",
        "crear_recordatorio",
        "navegador_web.abrir", "navegador_web.click", "navegador_web.escribir",
        "navegador_web.tecla", "navegador_web.scroll", "navegador_web.atras",
        "navegador_web.cerrar",
    ],
    RiskTier.IRREVERSIBLE: [
        "enviar_*", "*correo*", "ejecutar_comando*", "ejecutar_shell*",
        "proponer_comando", "git_push", "borrar_*", "eliminar_*", "pagar*",
    ],
}

# Patrones de texto (en payload/objetivo) que elevan cualquier acción a Tier 3.
_ESCALATION_PATTERNS = [
    r"\brm\s+-rf?\b",
    r"\bsudo\b",
    r"\bgit\s+push\b",
    r"\b(cvv|tarjeta|card\s*number|\d{13,16})\b",
    r"(confirmar\s+compra|realizar\s+pago|transferencia)",
    r"(eliminar\s+todo|borrar\s+todo|formatear)",
    # Material criptográfico y credenciales: claves privadas, keystores, .env...
    # (un 'leer_archivo' o 'cat' sobre estos rutas NUNCA es lectura inocua)
    r"(\.ssh/|\.gnupg/|\.aws/|id_rsa|id_ed25519|id_ecdsa|\.pem\b|\.key\b)",
    r"\.env\b",
    r"(credentials\.json|client_secret|api[_-]?key\s*=|access[_-]?token\s*=)",
]

_UNSET = object()


@dataclass
class SecurityPolicy:
    """Política de clasificación de riesgo cargada desde YAML o defaults."""
    default_tier: RiskTier = RiskTier.LOCAL_WRITE
    read_only: List[str] = field(default_factory=lambda: list(_DEFAULT_RULES[RiskTier.READ_ONLY]))
    local_write: List[str] = field(default_factory=lambda: list(_DEFAULT_RULES[RiskTier.LOCAL_WRITE]))
    irreversible: List[str] = field(default_factory=lambda: list(_DEFAULT_RULES[RiskTier.IRREVERSIBLE]))
    escalation_patterns: List[str] = field(default_factory=lambda: list(_ESCALATION_PATTERNS))

    @classmethod
    def load(cls, path: Optional[str] = None) -> "SecurityPolicy":
        """Carga la política desde YAML; si no existe o falla, usa defaults seguros."""
        policy_path = path or _POLICY_PATH
        if not os.path.exists(policy_path):
            logger.info("security_policy.yaml no encontrado; usando política por defecto (conservadora).")
            return cls()
        try:
            import yaml
            with open(policy_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            tiers = data.get("tiers", {}) or {}
            return cls(
                default_tier=RiskTier(int(data.get("default_tier", 2))),
                read_only=list(tiers.get("read_only", _DEFAULT_RULES[RiskTier.READ_ONLY])),
                local_write=list(tiers.get("local_write", _DEFAULT_RULES[RiskTier.LOCAL_WRITE])),
                irreversible=list(tiers.get("irreversible", _DEFAULT_RULES[RiskTier.IRREVERSIBLE])),
                escalation_patterns=list(data.get("escalation_patterns", _ESCALATION_PATTERNS)),
            )
        except Exception as e:
            logger.error(f"Error cargando security policy ({e}); usando defaults seguros.")
            return cls()

    def _matches(self, action: str, patterns: List[str]) -> bool:
        import fnmatch
        action_l = action.lower()
        return any(fnmatch.fnmatch(action_l, p.lower()) for p in patterns)

    def classify(self, action: str, payload_text: str = "") -> RiskTier:
        """
        Clasifica una acción por nombre y, opcionalmente, eleva el riesgo si el
        payload contiene patrones sensibles (pagos, borrado, sudo, push...).
        La escalación por contenido siempre gana sobre la clasificación por nombre.
        """
        base = self.default_tier
        if self._matches(action, self.irreversible):
            base = RiskTier.IRREVERSIBLE
        elif self._matches(action, self.read_only):
            base = RiskTier.READ_ONLY
        elif self._matches(action, self.local_write):
            base = RiskTier.LOCAL_WRITE

        if payload_text:
            text = payload_text.lower()
            for pattern in self.escalation_patterns:
                if re.search(pattern, text, flags=re.IGNORECASE):
                    return RiskTier.IRREVERSIBLE
        return base

    def requires_hitl(self, action: str, payload_text: str = "") -> bool:
        """True si la acción exige aprobación humana explícita (Tier 3)."""
        return self.classify(action, payload_text) >= RiskTier.IRREVERSIBLE
