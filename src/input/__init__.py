"""
Subsistema de entrada/salida GUI de Atlas (Computer Use en Linux).

Componentes:
- health:    diagnóstico y auto-recuperación de la cadena de input (ydotoold, portal, wl-copy).
- backends:  inyección de bajo nivel (YdotoolBackend, RemoteDesktopPortalBackend) con InputRouter.
- resolver:  ElementResolver híbrido AT-SPI2 -> Set-of-Marks -> coordenadas crudas.

Jerarquía de acción (de mayor a menor determinismo):
  1. Acción semántica AT-SPI2 (Atspi.Action.do_action / EditableText) - sin robar foco.
  2. Coordenadas exactas de AT-SPI2 (get_element_bounds) inyectadas por el backend activo.
  3. Set-of-Marks visual (etiqueta numerada sobre screenshot).
  4. Coordenadas crudas estimadas por el LLM (ultimo recurso).
"""
from src.input.health import InputHealth, InputHealthReport
from src.input.backends import InputBackend, YdotoolBackend, RemoteDesktopPortalBackend, InputRouter

__all__ = [
    "InputHealth",
    "InputHealthReport",
    "InputBackend",
    "YdotoolBackend",
    "RemoteDesktopPortalBackend",
    "InputRouter",
]
