"""
Controlador de una pestaña concreta vía CDP (sesión flatten).

Filosofía heredada de la Fase 1 (ElementResolver AT-SPI2): antes que pedirle al
LLM coordenadas visuales, se extrae un SNAPSHOT DETERMINISTA de los elementos
interactivos:

    [1] button   «Enviar»
    [2] input    «Buscar…»   (editable)
    [3] a        «Configuración»

Cada elemento queda marcado en el DOM con un atributo temporal
`data-atlas-idx` y las acciones (click/escribir) se ejecutan localizando ese
atributo. El LLM solo elige un índice o un nombre; jamás coordenadas crudas.

Fallbacks:
- click: el.click() en JS; si la página ignora clicks sintéticos de JS, la tool
  puede reintentar con `click_at(x, y)` (Input.dispatchMouseEvent = evento
  trusted) usando las coords del snapshot.
- escribir: Input.insertText (evento trusted tipo IME, compatible con
  React/contenteditable); si el valor no quedó, setter nativo + evento input.
"""
import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logging import get_logger
from .client import CDPConnection, CDPError

logger = get_logger("cdp.page")


@dataclass
class InteractiveElement:
    idx: int
    tag: str
    role: str
    name: str
    editable: bool = False
    disabled: bool = False
    center: Tuple[int, int] = (0, 0)
    size: Tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------------
# JavaScript embebido (se evalúa con Runtime.evaluate, returnByValue)
# ---------------------------------------------------------------------------

_JS_SNAPSHOT = """
(() => {
  document.querySelectorAll('[data-atlas-idx]').forEach(e => e.removeAttribute('data-atlas-idx'));
  const SEL = 'a[href],button,input,textarea,select,[role],[contenteditable="true"],summary,label[for]';
  const out = [];
  let idx = 0;
  for (const el of document.querySelectorAll(SEL)) {
    if (idx >= %d) break;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    idx += 1;
    el.setAttribute('data-atlas-idx', String(idx));
    const tag = el.tagName.toLowerCase();
    const editable = tag === 'input' || tag === 'textarea' || el.isContentEditable;
    let name = (el.getAttribute('aria-label') || el.innerText || el.value ||
                el.placeholder || el.title || el.name || '').trim();
    name = name.replace(/\\s+/g, ' ').slice(0, 80);
    out.push({idx, tag, role: el.getAttribute('role') || tag, name, editable,
              disabled: !!el.disabled,
              x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
              w: Math.round(r.width), h: Math.round(r.height)});
  }
  return out;
})()
"""

_JS_CLICK = """
(() => {
  const el = document.querySelector('[data-atlas-idx="%d"]');
  if (!el) return {found: false};
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  el.focus();
  el.click();
  return {found: true, tag: el.tagName.toLowerCase(),
          name: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 80),
          x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
})()
"""

_JS_FOCUS_CLEAR = """
(() => {
  const el = document.querySelector('[data-atlas-idx="%d"]');
  if (!el) return {found: false, editable: false};
  el.scrollIntoView({block: 'center', inline: 'center'});
  el.focus();
  el.click();
  const tag = el.tagName.toLowerCase();
  const editable = tag === 'input' || tag === 'textarea' || el.isContentEditable;
  if (editable) {
    if (tag === 'input' || tag === 'textarea') { el.value = ''; } else { el.innerText = ''; }
    el.dispatchEvent(new Event('input', {bubbles: true}));
  }
  return {found: true, editable};
})()
"""

_JS_ACTIVE_VALUE = """
(() => {
  const el = document.activeElement;
  if (!el) return '';
  return el.value !== undefined ? String(el.value) : String(el.innerText || '');
})()
"""

_JS_NATIVE_SETTER = """
(() => {
  const el = document.activeElement;
  if (!el) return false;
  const text = %s;
  const tag = el.tagName.toLowerCase();
  try {
    if (tag === 'input' || tag === 'textarea') {
      const proto = tag === 'input' ? window.HTMLInputElement.prototype
                                    : window.HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
    } else {
      el.innerText = text;
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  } catch (e) { return false; }
})()
"""

_JS_READ_TEXT = """
(() => {
  const pick = document.querySelector('article') || document.querySelector('main') || document.body;
  const text = ((pick && pick.innerText) || '').trim();
  return {title: document.title, url: location.href, text: text.slice(0, %d)};
})()
"""

_JS_SCROLL = """
(() => {
  window.scrollBy({left: 0, top: %d, behavior: 'instant'});
  return Math.round(window.scrollY);
})()
"""

_JS_READY_STATE = "document.readyState"

_JS_INTERACTIVE_COUNT = (
    "document.querySelectorAll('a,button,input,select,textarea,[role],"
    "[contenteditable=\"true\"]').length"
)

_JS_SIGNATURE = """
(() => location.href + '|' + document.title + '|' +
 document.querySelectorAll('a,button,input,select,textarea,[role]').length + '|' +
 (document.body ? document.body.innerText.length : 0))()
"""

_KEYMAP: Dict[str, Dict[str, Any]] = {
    "enter":     {"windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter", "text": "\r"},
    "tab":       {"windowsVirtualKeyCode": 9,  "key": "Tab", "code": "Tab"},
    "escape":    {"windowsVirtualKeyCode": 27, "key": "Escape", "code": "Escape"},
    "backspace": {"windowsVirtualKeyCode": 8,  "key": "Backspace", "code": "Backspace"},
    "delete":    {"windowsVirtualKeyCode": 46, "key": "Delete", "code": "Delete"},
    "arriba":    {"windowsVirtualKeyCode": 38, "key": "ArrowUp", "code": "ArrowUp"},
    "abajo":     {"windowsVirtualKeyCode": 40, "key": "ArrowDown", "code": "ArrowDown"},
    "izquierda": {"windowsVirtualKeyCode": 37, "key": "ArrowLeft", "code": "ArrowLeft"},
    "derecha":   {"windowsVirtualKeyCode": 39, "key": "ArrowRight", "code": "ArrowRight"},
    "inicio":    {"windowsVirtualKeyCode": 36, "key": "Home", "code": "Home"},
    "fin":       {"windowsVirtualKeyCode": 35, "key": "End", "code": "End"},
}


class PageController:
    """Opera una pestaña adjuntada (flatten session)."""

    def __init__(self, conn: CDPConnection, session_id: str, target_id: str):
        self._conn = conn
        self.session_id = session_id
        self.target_id = target_id
        self._last_snapshot_url: Optional[str] = None

    # ------------------------------------------------------------------
    # Base
    # ------------------------------------------------------------------
    async def enable(self) -> None:
        """Activa los dominios mínimos de la sesión."""
        await self._conn.send("Page.enable", session_id=self.session_id)
        await self._conn.send("Runtime.enable", session_id=self.session_id)

    async def evaluate(self, expression: str, timeout: float = 15.0) -> Any:
        result = await self._conn.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            session_id=self.session_id,
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text", "error JS")
            raise CDPError(f"Excepción JS: {text}")
        return (result.get("result") or {}).get("value")

    async def navigate(self, url: str, timeout: float = 15.0) -> str:
        """Navega y espera a que el documento esté interactivo/completo."""
        from urllib.parse import urlsplit
        if not urlsplit(url).scheme:
            url = "https://" + url
        result = await self._conn.send("Page.navigate", {"url": url},
                                       session_id=self.session_id, timeout=timeout)
        if result.get("errorText"):
            raise CDPError(f"Navegación fallida: {result['errorText']}")
        await self._wait_ready(timeout)
        await self._wait_dom_stable()
        self._last_snapshot_url = None  # invalidar snapshot previo
        return url

    async def _wait_ready(self, timeout: float) -> None:
        import asyncio
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                state = await self.evaluate(_JS_READY_STATE, timeout=3.0)
            except CDPError:
                state = None
            if state in ("interactive", "complete"):
                return
            await asyncio.sleep(0.25)

    async def _wait_dom_stable(self, max_wait: float = 4.0,
                               required_polls: int = 2,
                               interval: float = 0.35) -> None:
        """
        Espera a que el DOM deje de crecer (SPAs modernos: readyState completa
        mucho antes de que la app renderice sus controles — p.ej. Gmail muestra
        solo la pantalla de carga). Se considera estable cuando el conteo de
        elementos interactivos se repite en `required_polls` sondeos seguidos.
        Si el JS no devuelve un número (navegación extraña), no bloquea.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max_wait
        last = -1
        stable = 0
        while loop.time() < deadline:
            try:
                count = await self.evaluate(_JS_INTERACTIVE_COUNT, timeout=2.0)
            except CDPError:
                break
            if not isinstance(count, (int, float)):
                break
            if count == last:
                stable += 1
                if stable >= required_polls:
                    return
            else:
                stable = 0
                last = count
            await asyncio.sleep(interval)

    async def current_info(self) -> Dict[str, str]:
        try:
            info = await self.evaluate(
                "({title: document.title, url: location.href})", timeout=5.0)
            return info or {"title": "", "url": ""}
        except CDPError:
            return {"title": "", "url": ""}

    # ------------------------------------------------------------------
    # Snapshot determinista de elementos interactivos
    # ------------------------------------------------------------------
    async def snapshot(self, max_items: int = 60) -> List[InteractiveElement]:
        raw = await self.evaluate(_JS_SNAPSHOT % max_items) or []
        info = await self.current_info()
        self._last_snapshot_url = info.get("url", "")
        elements: List[InteractiveElement] = []
        for item in raw:
            elements.append(InteractiveElement(
                idx=int(item.get("idx", 0)),
                tag=item.get("tag", ""),
                role=item.get("role", ""),
                name=item.get("name", ""),
                editable=bool(item.get("editable")),
                disabled=bool(item.get("disabled")),
                center=(int(item.get("x", 0)), int(item.get("y", 0))),
                size=(int(item.get("w", 0)), int(item.get("h", 0))),
            ))
        return elements

    @staticmethod
    def find(elements: List[InteractiveElement], query: str) -> Optional[InteractiveElement]:
        """Búsqueda tolerante por nombre: exacto > empieza-por > contiene."""
        q = query.strip().lower()
        if not q:
            return None
        named = [e for e in elements if e.name]
        for pred in (
            lambda e: e.name.lower() == q,
            lambda e: e.name.lower().startswith(q),
            lambda e: q in e.name.lower(),
        ):
            for el in named:
                if pred(el) and not el.disabled:
                    return el
        return None

    async def signature(self) -> str:
        """Huella barata de la página para verificación post-acción."""
        try:
            raw = await self.evaluate(_JS_SIGNATURE, timeout=5.0) or ""
        except CDPError:
            return ""
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    async def click(self, idx: int) -> Dict[str, Any]:
        """Click determinista por índice del snapshot (el.click() en JS)."""
        result = await self.evaluate(_JS_CLICK % int(idx))
        return result or {"found": False}

    async def click_at(self, x: int, y: int) -> None:
        """Click con evento trusted (Input.dispatchMouseEvent) — fallback."""
        for etype, extra in (("mousePressed", {"clickCount": 1}),
                             ("mouseReleased", {"clickCount": 1})):
            await self._conn.send(
                "Input.dispatchMouseEvent",
                {"type": etype, "x": x, "y": y, "button": "left", **extra},
                session_id=self.session_id,
            )

    async def type_text(self, idx: int, text: str, clear: bool = True) -> Dict[str, Any]:
        """
        Escribe texto en el elemento `idx`: foco JS → Input.insertText (trusted)
        → verificación → fallback a setter nativo si el campo quedó vacío.
        """
        if not clear:
            raise NotImplementedError  # reservado; hoy siempre se limpia
        prep = await self.evaluate(_JS_FOCUS_CLEAR % int(idx))
        if not prep or not prep.get("found"):
            return {"ok": False, "reason": "not_found"}
        if not prep.get("editable"):
            return {"ok": False, "reason": "not_editable"}

        await self._conn.send("Input.insertText", {"text": text},
                              session_id=self.session_id)
        value = await self.evaluate(_JS_ACTIVE_VALUE) or ""
        via = "insertText"
        if text not in value:
            # Fallback: setter nativo del prototype (bypass de frameworks)
            import json as _json
            ok = await self.evaluate(_JS_NATIVE_SETTER % _json.dumps(text))
            if not ok:
                return {"ok": False, "reason": "insert_failed"}
            via = "native_setter"
        return {"ok": True, "via": via, "chars": len(text)}

    async def key(self, name: str) -> bool:
        spec = _KEYMAP.get(name.strip().lower())
        if not spec:
            return False
        for etype in ("rawKeyDown", "keyUp"):
            params = {"type": etype, **spec}
            if etype == "keyUp":
                params.pop("text", None)
            await self._conn.send("Input.dispatchKeyEvent", params,
                                  session_id=self.session_id)
        return True

    async def scroll(self, direction: str, amount_px: int = 600) -> int:
        sign = -1 if direction in ("arriba", "up") else 1
        return int(await self.evaluate(_JS_SCROLL % (sign * abs(amount_px))) or 0)

    async def back(self) -> None:
        await self.evaluate("history.back(); true")
        await self._wait_ready(10.0)
        await self._wait_dom_stable()
        self._last_snapshot_url = None

    # ------------------------------------------------------------------
    # Lectura y captura
    # ------------------------------------------------------------------
    async def read_text(self, max_chars: int = 4000) -> Dict[str, str]:
        result = await self.evaluate(_JS_READ_TEXT % int(max_chars))
        return result or {"title": "", "url": "", "text": ""}

    async def screenshot(self, quality: int = 70) -> bytes:
        result = await self._conn.send(
            "Page.captureScreenshot", {"format": "jpeg", "quality": quality},
            session_id=self.session_id, timeout=20.0,
        )
        return base64.b64decode(result.get("data", ""))
