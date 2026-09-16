"""
Credential broker (Fase 6, COMPUTER_USE_PLAN.md).

Principio: **ningún secreto entra jamás al contexto del LLM ni al entorno de
un subproceso que no lo necesite.**

- Las herramientas internas piden el secreto por nombre: `broker.get("TAVILY_API_KEY")`.
- Los subprocesos del subagente reciben `scrub_env()`: solo variables de una
  allowlist funcional (PATH, LANG, ...), nunca *KEY*/*TOKEN*/*SECRET*.
- Todo texto que vuelve al LLM puede pasarse por `redact()`: si un secreto
  aparece en una salida (p.ej. un stack trace con el header Authorization),
  se sustituye por [REDACTADO:NOMBRE] antes de llegar al contexto del modelo.

Uso: `from src.security.credentials import get_broker` (singleton perezoso,
se captura os.environ en el primer uso — dotenv ya cargada en arranque).
"""
import re
from typing import Dict, Optional

from src.utils.logging import get_logger

logger = get_logger("security.credentials")

# Nombres de variables que se consideran secretas por convención.
_SECRET_NAME_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|PRIVATE)", re.IGNORECASE)

# Variables que un subproceso legítimo puede necesitar y NO son secretas.
_ENV_ALLOWLIST = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "COLORTERM",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "USER", "LOGNAME", "SHELL", "HOME", "VIRTUAL_ENV",
)

_MIN_REDACTABLE_LEN = 8  # evita falsos positivos con valores triviales


class CredentialBroker:
    """Custodio central de secretos en memoria."""

    def __init__(self, environ: Optional[Dict[str, str]] = None):
        import os
        self._env = dict(os.environ if environ is None else environ)
        self._secrets: Dict[str, str] = {
            k: v for k, v in self._env.items()
            if _SECRET_NAME_RE.search(k) and v and len(v) >= _MIN_REDACTABLE_LEN
        }

    # ------------------------------------------------------------------
    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Recupera un secreto por nombre (solo código interno, nunca el LLM)."""
        return self._env.get(name, default)

    def is_secret_name(self, name: str) -> bool:
        return bool(_SECRET_NAME_RE.search(name))

    # ------------------------------------------------------------------
    def scrub_env(self, extra_allow: tuple = ()) -> Dict[str, str]:
        """
        Entorno para subprocesos: solo la allowlist funcional + extras pedidos
        explícitamente por nombre. Jamás incluye variables secretas.
        """
        allowed = set(_ENV_ALLOWLIST) | set(extra_allow)
        clean = {}
        for key in allowed:
            if key in self._env and not self.is_secret_name(key):
                clean[key] = self._env[key]
        clean.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        return clean

    def redact(self, text: str) -> str:
        """Sustituye cualquier valor secreto que aparezca en `text`."""
        if not text:
            return text
        for name, value in self._secrets.items():
            if value and value in text:
                text = text.replace(value, f"[REDACTADO:{name}]")
        return text

    def secret_names(self):
        """Nombres (no valores) de los secretos custodiados — para diagnóstico."""
        return sorted(self._secrets.keys())


# ---------------------------------------------------------------------------
_BROKER: Optional[CredentialBroker] = None


def get_broker() -> CredentialBroker:
    """Singleton perezoso: captura os.environ en el primer uso (dotenv ya cargado)."""
    global _BROKER
    if _BROKER is None:
        _BROKER = CredentialBroker()
        logger.info(
            f"🔐 CredentialBroker activo: {len(_BROKER.secret_names())} secretos custodiados."
        )
    return _BROKER
