"""
ElementResolver: el corazón del computer-use híbrido de Atlas.

Cascada de resolución de un objetivo expresado en lenguaje natural
("el botón Enviar", "el campo de búsqueda", "Aceptar"):
  1. AT-SPI2: nombre/rol/descripción del nodo accesible + bounds exactos.
  2. Set-of-Marks (SoM): etiquetas numeradas sobre el screenshot para que el
     LLM elija SOLO el número, jamás coordenadas a ciegas.
  3. Coordenadas crudas (último recurso, decisión del orquestador).

Principio: cada paso que se resuelve sin LLM es un paso que no puede
alucinar coordenadas (el fallo #1 documentado en los agentes visuales).
"""
import difflib
import io
import unicodedata
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger("input.resolver")


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados — para matching robusto."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.lower().split())


@dataclass
class GuiElement:
    """Un elemento interactivo de la GUI con su posición física en pantalla."""
    index: int
    name: str
    role: str
    bounds: Optional[Tuple[int, int, int, int]]  # (x, y, w, h) absolutos
    is_editable: bool
    is_focusable: bool
    actions: List[str] = field(default_factory=list)
    app_name: str = ""
    node: Any = None  # Nodo AT-SPI2 crudo (para acciones semánticas)

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        if not self.bounds:
            return None
        x, y, w, h = self.bounds
        return x + w // 2, y + h // 2

    @property
    def can_click_semantic(self) -> bool:
        """True si el elemento expone acciones AT-SPI2 (click sin robar foco)."""
        return bool(self.actions)

    def describe(self) -> str:
        b = f" en {self.bounds}" if self.bounds else ""
        acts = f" acciones={self.actions}" if self.actions else ""
        return f"[{self.index}] {self.role} '{self.name}' ({self.app_name}){b}{acts}"


class ElementResolver:
    """
    Enumera y localiza elementos interactivos de la GUI usando el
    AccessibilitySensor (AT-SPI2) y, bajo demanda, renderiza Set-of-Marks.
    """

    # Roles que consideramos "objetivo de acción" y no simples contenedores
    MAX_ELEMENTS = 60

    def __init__(self, sensor=None, screen_service=None):
        # Lazy imports para no acoplar el import de a11y/vision al arranque
        self._sensor = sensor
        self._screen = screen_service

    @property
    def sensor(self):
        if self._sensor is None:
            from src.utils.a11y import AccessibilitySensor
            self._sensor = AccessibilitySensor()
        return self._sensor

    # ------------------------------------------------------------------
    # Enumeración de elementos
    # ------------------------------------------------------------------
    def list_elements(self, app_hint: Optional[str] = None,
                      max_elements: int = MAX_ELEMENTS) -> List[GuiElement]:
        """
        Recorre las aplicaciones registradas en AT-SPI2 (o solo las que
        coincidan con app_hint) y devuelve sus elementos interactivos con
        bounds absolutos. Orden: primero la app indicada por app_hint.
        """
        if not self.sensor.is_available():
            return []
        if not getattr(self.sensor, "_atspi_gobject_ready", False):
            logger.debug("AT-SPI2 GObject no disponible; no se pueden enumerar elementos.")
            return []

        from src.utils.a11y import Atspi  # reexportado por el módulo del sensor

        elements: List[GuiElement] = []
        try:
            desktop = Atspi.get_desktop(0)
        except Exception:
            return []
        if not desktop:
            return []

        # Reunir apps candidatas (con hint primero)
        apps = []
        for i in range(desktop.get_child_count()):
            try:
                app = desktop.get_child_at_index(i)
            except Exception:
                continue
            if not app:
                continue
            apps.append(app)
        if app_hint:
            hint = _normalize(app_hint)
            apps.sort(key=lambda a: 0 if hint in _normalize(a.get_name() or "") else 1)

        for app in apps:
            if len(elements) >= max_elements:
                break
            app_name = app.get_name() or ""
            try:
                n_children = app.get_child_count()
            except Exception:
                continue
            for j in range(min(n_children, 3)):  # máx. 3 ventanas por app
                try:
                    win = app.get_child_at_index(j)
                except Exception:
                    continue
                if not win:
                    continue
                try:
                    role = (win.get_role_name() or "").lower()
                except Exception:
                    role = ""
                if role not in ("frame", "window", "dialog", ""):
                    continue
                for info in self.sensor.find_interactive_nodes(win, max_depth=25):
                    bounds = info.get("bounds")
                    if not bounds or bounds[2] <= 0 or bounds[3] <= 0:
                        continue  # sin geometría no sirve para accionarlo
                    elements.append(GuiElement(
                        index=len(elements) + 1,
                        name=info.get("name", "") or "",
                        role=info.get("role", "") or "",
                        bounds=bounds,
                        is_editable=bool(info.get("is_editable")),
                        is_focusable=bool(info.get("is_focusable")),
                        actions=list(info.get("actions") or []),
                        app_name=app_name,
                        node=info.get("node"),
                    ))
                    if len(elements) >= max_elements:
                        break
        return elements

    # ------------------------------------------------------------------
    # Búsqueda por lenguaje natural
    # ------------------------------------------------------------------
    def _score(self, query_norm: str, el: GuiElement) -> float:
        name = _normalize(el.name)
        if not name:
            return 0.0
        if query_norm == name:
            return 100.0
        if name.startswith(query_norm):
            return 85.0
        if query_norm in name:
            return 65.0
        # Las consultas de voz rara vez son contiguas: "buscar chat" debe
        # matchear "Buscar un chat o empezar uno nuevo" (palabras presentes).
        q_words = set(query_norm.split())
        n_words = set(name.split())
        if q_words and q_words.issubset(n_words):
            cobertura = len(q_words & n_words) / max(1, len(n_words))
            return 55.0 + 20.0 * cobertura
        ratio = difflib.SequenceMatcher(None, query_norm, name).ratio()
        return ratio * 50.0 if ratio >= 0.6 else 0.0

    def find(self, query: str, elements: List[GuiElement],
             role_hint: Optional[str] = None) -> Optional[GuiElement]:
        """
        Localiza el mejor candidato para `query` entre `elements`.
        `role_hint` ("botón", "campo", "editable") desempata a favor del rol.
        """
        q = _normalize(query)
        if not q or not elements:
            return None

        # Sinónimos de rol en lenguaje natural → roles AT-SPI2
        role_map = {
            "boton": ("push_button", "button", "menu_item", "toggle_button"),
            "button": ("push_button", "button", "menu_item", "toggle_button"),
            "campo": ("entry", "text", "combo_box", "document_text"),
            "texto": ("entry", "text", "document_text"),
            "entrada": ("entry", "text"),
            "editable": ("entry", "text", "document_text"),
            "menu": ("menu", "menu_item", "popup_menu"),
            "enlace": ("link",),
            "link": ("link",),
            "pestaña": ("page_tab",),
            "tab": ("page_tab",),
        }
        wanted_roles: Tuple[str, ...] = ()
        if role_hint:
            wanted_roles = role_map.get(_normalize(role_hint), ())
        if not wanted_roles:
            for key, roles in role_map.items():
                if f" {key} " in f" {q} ":
                    wanted_roles = roles
                    break

        best, best_score = None, 0.0
        for el in elements:
            score = self._score(q, el)
            # Bonificación por rol pedido o por editabilidad si se busca "campo"
            if wanted_roles and el.role in wanted_roles:
                score += 25.0
            if "editable" in q or "campo" in q or "texto" in q:
                if el.is_editable:
                    score += 20.0
            if score > best_score:
                best, best_score = el, score
        if best and best_score >= 30.0:
            logger.info(f"🎯 Elemento resuelto por AT-SPI2 (score {best_score:.0f}): {best.describe()}")
            return best
        return None

    # ------------------------------------------------------------------
    # Set-of-Marks: etiquetado visual para elección por número
    # ------------------------------------------------------------------
    def render_som(self, image_bytes: bytes, elements: List[GuiElement],
                   scale_x: float = 1.0, scale_y: float = 1.0) -> Tuple[bytes, List[GuiElement]]:
        """
        Dibuja etiquetas numeradas sobre la captura en la posición de cada
        elemento (Set-of-Marks). Devuelve (jpeg_bytes, elementos_en_la_imagen).
        `scale_x/y` mapean coordenadas físicas -> coordenadas de la imagen.
        """
        from PIL import Image, ImageDraw

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)
        shown: List[GuiElement] = []

        for el in elements:
            if not el.bounds:
                continue
            x, y, w, h = el.bounds
            sx, sy = int(x / scale_x), int(y / scale_y)
            sw, sh = max(6, int(w / scale_x)), max(6, int(h / scale_y))
            # Recuadro del elemento
            draw.rectangle([sx, sy, sx + sw, sy + sh], outline=(255, 64, 64), width=2)
            # Etiqueta numerada en la esquina superior izquierda del bounds
            label = str(el.index)
            lw, lh = len(label) * 8 + 6, 16
            lx, ly = max(0, sx), max(0, sy - lh)
            draw.rectangle([lx, ly, lx + lw, ly + lh], fill=(255, 64, 64))
            draw.text((lx + 3, ly + 2), label, fill=(255, 255, 255))
            shown.append(el)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80, optimize=True)
        logger.info(f"🏷️ SoM renderizado con {len(shown)} marcas.")
        return buf.getvalue(), shown

    @staticmethod
    def som_legend(elements: List[GuiElement], max_items: int = 40) -> str:
        """Leyenda textual de las marcas (para acompañar la imagen en el prompt)."""
        lines = []
        for el in elements[:max_items]:
            flags = []
            if el.is_editable:
                flags.append("editable")
            if el.actions:
                flags.append("accionable")
            tail = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"[{el.index}] {el.role} '{el.name}' [{el.app_name}]{tail}")
        return "\n".join(lines)
