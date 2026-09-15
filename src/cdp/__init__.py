"""
Integración CDP (Chrome DevTools Protocol) de Atlas — Fase 4 del plan computer-use.

Permite a Atlas operar el navegador Chromium/Brave con las sesiones reales del
usuario, sin Playwright ni drivers externos:

- `CDPConnection`: websocket único al endpoint del navegador con multiplexado de
  pestañas vía `sessionId` (flatten mode).
- `BrowserManager`: descubrimiento (`/json/version`), lanzamiento de una
  instancia dedicada con perfil propio persistente (requisito desde Chrome 136:
  `--remote-debugging-port` es ignorado con el user-data-dir por defecto) y
  modo headless desechable para trabajo paralelo sin valor de sesión.
- `PageController`: navegación, snapshot de elementos interactivos con índices
  `[n]` determinista (equivalente web al ElementResolver AT-SPI2 de Fase 1),
  click/escritura/scroll y captura de pantalla.

Verificación externa (sept 2026, ver DEVELOPMENT_NOTES.md):
- Chrome/Brave >= 136 ignoran `--remote-debugging-port` con el perfil default.
- Clientes websocket no-Chrome necesitan `--remote-allow-origins=*` (Origin check).
- Endpoints `/json/version` y `/json/list` siguen vigentes (loopback + token GUID).
"""
from .client import CDPConnection, CDPError, CDPTimeoutError
from .manager import BrowserManager, BrowserNotFoundError
from .page import PageController, InteractiveElement

__all__ = [
    "CDPConnection",
    "CDPError",
    "CDPTimeoutError",
    "BrowserManager",
    "BrowserNotFoundError",
    "PageController",
    "InteractiveElement",
]
