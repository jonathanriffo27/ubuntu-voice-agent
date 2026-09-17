"""
interactuar_gui: la herramienta de computer-use de Atlas para el LLM de voz.

Principios de diseño (ver COMPUTER_USE_PLAN.md):
- Resuelve el objetivo con AT-SPI2 (determinista) antes de tocar coordenadas.
- Las acciones semánticas (do_action / set_text) no roban el foco del usuario.
- Toda acción Tier-3 (pagos, enviar con contenido sensible, borrado) pasa por
  ApprovalManager multi-canal antes de ejecutarse.
- Tras cada acción verifica con frame-diff si la pantalla reaccionó.
- Cuando no encuentra el elemento, devuelve los candidatos visibles para que
  el LLM reintente con el nombre exacto (autocorrección en vez de fallo).
"""
import asyncio
import time
from typing import Any, Dict, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.input.backends import InputRouter
from src.input.resolver import ElementResolver
from src.security.policy import SecurityPolicy, RiskTier
from src.utils.logging import get_logger

logger = get_logger("plugins.gui_actions")


class InteractuarGuiTool(BaseTool):
    """Herramienta de interacción GUI híbrida AT-SPI2 + input por backend."""

    # Marcadores (en minúsculas) en el contenido de un resultado que indican
    # que la acción NO produjo progreso visible, aunque haya "tenido éxito".
    _NO_PROGRESS_MARKERS = (
        "no cambió", "no encontr", "no hay elementos", "no expone elementos",
        "no se pudo", "no tiene geometría", "ningún backend",
    )
    # Cuántos intentos sin progreso seguidos activan el circuit breaker.
    _NO_PROGRESS_LIMIT = 3

    def __init__(self, approval_manager=None, resolver: Optional[ElementResolver] = None,
                 router: Optional[InputRouter] = None, screen_service=None,
                 policy: Optional[SecurityPolicy] = None):
        self._approval = approval_manager
        self._resolver = resolver or ElementResolver()
        self._router = router or InputRouter()
        self._screen = screen_service  # lazy: OptimizedScreenCaptureService
        self._policy = policy or SecurityPolicy.load()
        self._no_progress_streak = 0

    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "interactuar_gui"

    @property
    def description(self) -> str:
        return (
            "Interactúa con la interfaz gráfica del escritorio (click en un botón, escribir en un "
            "campo, pulsar teclas, leer los elementos visibles de una app). Úsala SOLO cuando el "
            "usuario pida explícitamente interactuar con una aplicación gráfica ('haz click en "
            "Enviar', 'escribe X en el buscador de Telegram', 'pulsa Ctrl+L'). NUNCA pagues, "
            "borres ni confirmes diálogos destructivos sin que el usuario lo haya pedido en su "
            "mensaje. Para acciones de mayor nivel (enviar WhatsApp, correo, abrir app), usa las "
            "herramientas específicas que ya existen para eso."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "accion": {
                    "type": "STRING",
                    "enum": ["click", "doble_click", "escribir", "tecla", "leer"],
                    "description": "La acción a realizar."
                },
                "objetivo": {
                    "type": "STRING",
                    "description": (
                        "Para click/escribir: el nombre visible del elemento (ej. 'Enviar', "
                        "'Buscar un chat', 'Aceptar'). Para tecla: la combinación ('enter', "
                        "'ctrl+l', 'super'). Para leer: la app o ventana a inspeccionar."
                    )
                },
                "texto": {
                    "type": "STRING",
                    "description": "Solo para accion='escribir': el texto a escribir en el campo."
                },
                "app": {
                    "type": "STRING",
                    "description": "Nombre de la aplicación a la que limitar la búsqueda (opcional)."
                }
            },
            "required": ["accion", "objetivo"]
        }

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        accion = (kwargs.get("accion") or "").strip().lower()
        objetivo = (kwargs.get("objetivo") or "").strip()
        texto = kwargs.get("texto") or ""
        app = (kwargs.get("app") or "").strip() or None

        if not objetivo:
            return ToolResult(success=False, content="Falta el parámetro 'objetivo'.")

        # --- Política de riesgo: contenido sensible => HITL obligatorio ---
        payload = f"{objetivo} {texto}".strip()
        tier = self._policy.classify("interactuar_gui", payload)
        if tier >= RiskTier.IRREVERSIBLE:
            if not self._approval:
                return ToolResult(
                    success=False,
                    content="Acción clasificada como riesgo alto y no hay gestor de aprobación disponible. Pide confirmación verbal explícita al usuario primero."
                )
            approved = await self._approval.request_approval(
                action_type="gui_interaction",
                description=f"Interacción GUI de riesgo: {accion} sobre '{objetivo}'" + (f" con texto ({len(texto)} chars)" if texto else ""),
                payload=payload,
                timeout=120.0,
            )
            if not approved:
                return ToolResult(success=False, content=f"El usuario rechazó la acción '{accion}' sobre '{objetivo}'.")

        try:
            result = await asyncio.wait_for(
                self._dispatch(accion, objetivo, texto, app),
                timeout=30.0,
            )
            return self._apply_progress_guard(result)
        except asyncio.TimeoutError:
            result = ToolResult(success=False, content=f"La acción '{accion}' sobre '{objetivo}' excedió 30s y fue cancelada.")
            return self._apply_progress_guard(result)
        except Exception as e:
            logger.error(f"interactuar_gui error: {e}")
            return ToolResult(success=False, content=f"Error ejecutando '{accion}' sobre '{objetivo}': {e}")

    def _apply_progress_guard(self, result: ToolResult) -> ToolResult:
        """Circuit breaker anti-bucle: cuenta acciones consecutivas sin progreso
        visible y, al llegar al límite, ordena al LLM detenerse y explicar el
        bloqueo en vez de seguir clicando a ciegas (caso real: 2+ min de clicks
        al dock de GNOME intentando leer Gmail)."""
        text = (result.content or "").lower()
        progressed = result.success and not any(m in text for m in self._NO_PROGRESS_MARKERS)
        if progressed:
            self._no_progress_streak = 0
            return result

        self._no_progress_streak += 1
        if self._no_progress_streak < self._NO_PROGRESS_LIMIT:
            return result
        result.content = (result.content or "") + (
            f"\n⛔ DETÉN LA AUTOMATIZACIÓN GUI: llevas {self._no_progress_streak} "
            "acciones consecutivas sin progreso visible. NO sigas haciendo clicks ni "
            "lecturas a ciegas. Explica al usuario en una sola frase qué lo bloquea "
            "(app sin accesibilidad útil, navegador caído, ventana que no abre) y "
            "propón UNA alternativa concreta (por ejemplo 'navegador_web' con la URL "
            "del servicio) o pídele que lo haga manualmente."
        )
        return result

    async def _dispatch(self, accion: str, objetivo: str, texto: str, app: Optional[str]) -> ToolResult:
        if accion == "tecla":
            return await asyncio.to_thread(self._do_key, objetivo)
        if accion == "leer":
            return await asyncio.to_thread(self._do_read, objetivo, app)
        if accion in ("click", "doble_click"):
            return await asyncio.to_thread(self._do_click, objetivo, app, accion == "doble_click")
        if accion == "escribir":
            return await asyncio.to_thread(self._do_type, objetivo, texto, app)
        return ToolResult(success=False, content=f"Acción desconocida: '{accion}'.")

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def _do_key(self, combo: str) -> ToolResult:
        ok, via = self._router.press_key(combo)
        if ok:
            return ToolResult(success=True, content=f"Tecla/combinación '{combo}' enviada (backend {via}).")
        return ToolResult(success=False, content=f"No se pudo enviar '{combo}' (ningún backend disponible o tecla desconocida).")

    def _do_read(self, objetivo: str, app: Optional[str]) -> ToolResult:
        hint = app or objetivo
        elements = self._resolver.list_elements(app_hint=hint)
        if not elements:
            return ToolResult(
                success=False,
                content="No se encontraron elementos accesibles. La app puede no tener accesibilidad activa o no estar abierta."
            )
        # Ventana viva pero sin nombres accesibles (PWAs/webviews de Chromium
        # suelen exponer un árbol AT-SPI sin name): operarla a ciegas es inútil.
        if not any(el.name and el.name.strip() for el in elements):
            return ToolResult(
                success=False,
                content=(
                    f"La ventana de {hint} está abierta pero no expone elementos con "
                    "nombres accesibles (árbol AT-SPI vacío, típico de PWAs/webviews). "
                    "NO reintentes 'leer' esta app ni hagas clicks a ciegas sobre ella: "
                    "usa la herramienta 'navegador_web' con la URL del servicio."
                ),
            )
        lines = [f"Elementos interactivos visibles{f' en {hint}' if hint else ''}:"]
        for el in elements[:25]:
            tags = []
            if el.is_editable:
                tags.append("campo de texto")
            if el.actions:
                tags.append("clicable")
            tag = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"  [{el.index}] {el.role}: '{el.name}'{tag}")
        return ToolResult(success=True, content="\n".join(lines))

    def _do_click(self, objetivo: str, app: Optional[str], double: bool) -> ToolResult:
        elements = self._resolver.list_elements(app_hint=app)
        element = self._resolver.find(objetivo, elements)
        if not element:
            return self._not_found(objetivo, elements)

        # 1) Acción semántica AT-SPI2 (sin mover el puntero ni robar foco)
        if element.can_click_semantic and not double:
            if self._resolver.sensor.click_element_action(element.node):
                verify = self._verify_change()
                return ToolResult(
                    success=True,
                    content=f"Acción ejecutada sobre '{element.name}' vía AT-SPI2 (semántico, sin robar foco).{verify}"
                )

        # 2) Click por coordenadas exactas de bounds (vía backend disponible)
        center = element.center
        if not center:
            return ToolResult(success=False, content=f"El elemento '{element.name}' no tiene geometría accesible para hacer click.")
        ok, via = self._router.click(center[0], center[1], double=double)
        if ok:
            verify = self._verify_change()
            return ToolResult(
                success=True,
                content=f"Click en '{element.name}' ({center[0]},{center[1]}) vía {via}.{verify}"
            )
        return ToolResult(success=False, content=f"Falló el click sobre '{element.name}': ningún backend de input operativo.")

    def _do_type(self, objetivo: str, texto: str, app: Optional[str]) -> ToolResult:
        if not texto:
            return ToolResult(success=False, content="accion='escribir' requiere el parámetro 'texto'.")
        elements = self._resolver.list_elements(app_hint=app)
        element = self._resolver.find(objetivo, elements)
        if not element:
            return self._not_found(objetivo, elements)

        # 1) Inyección semántica de texto (sin teclear, sin robar foco)
        if element.is_editable:
            self._resolver.sensor.grab_element_focus(element.node)
            if self._resolver.sensor.set_element_text(element.node, texto):
                return ToolResult(
                    success=True,
                    content=f"Texto escrito en '{element.name}' vía AT-SPI2 ({len(texto)} caracteres)."
                )

        # 2) Click para enfocar + pegado por portapapeles
        center = element.center
        if center:
            ok, _ = self._router.click(center[0], center[1])
            if ok:
                time.sleep(0.15)
                ok, via = self._router.type_text(texto)
                if ok:
                    return ToolResult(success=True, content=f"Texto escrito en '{element.name}' vía portapapeles+pegado (backend {via}).")
        return ToolResult(success=False, content=f"No se pudo escribir en '{element.name}'.")

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _not_found(self, objetivo: str, elements) -> ToolResult:
        candidatos = [el for el in elements if el.name][:10]
        if candidatos:
            lista = ", ".join(f"'{el.name}' ({el.role})" for el in candidatos)
            return ToolResult(
                success=False,
                content=f"No encontré '{objetivo}'. Elementos visibles con nombre: {lista}. Reintenta con el nombre exacto."
            )
        return ToolResult(
            success=False,
            content=f"No encontré '{objetivo}' y no hay elementos con nombre accesibles. La app puede no tener accesibilidad activa."
        )

    def _get_screen(self):
        if self._screen is None:
            from src.vision.service import OptimizedScreenCaptureService
            self._screen = OptimizedScreenCaptureService()
        return self._screen

    def _verify_change(self) -> str:
        """Verificación barata post-acción: ¿cambió la pantalla?"""
        try:
            _, meta = self._get_screen().capture_screen_with_metadata(max_dim=960, quality=60)
            if meta.get("unchanged"):
                return " (aviso: la pantalla no cambió tras la acción; puede que no tuviera efecto visible)"
            return f" (pantalla cambió {meta.get('change_ratio', 0):.0%})"
        except Exception:
            return ""
