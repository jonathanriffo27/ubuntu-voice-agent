"""Tests del PageController: snapshot determinista, click, texto, claves."""
import base64

import pytest

from src.cdp.client import CDPError
from src.cdp.page import PageController, InteractiveElement


class FakeCDPConnection:
    """Simula las respuestas CDP según la expresión JS recibida."""

    def __init__(self, snapshot_payload=None, active_value=""):
        self.snapshot_payload = snapshot_payload or []
        self.active_value = active_value
        self.calls = []  # (method, params, session_id)
        self.js_handlers = []  # lista de (substring, callable|value)

    def when_js(self, substring, value):
        self.js_handlers.append((substring, value))

    async def send(self, method, params=None, session_id=None, timeout=20.0):
        params = params or {}
        self.calls.append((method, params, session_id))
        if method == "Runtime.evaluate":
            expr = params.get("expression", "")
            for substring, value in self.js_handlers:
                if substring in expr:
                    if callable(value):
                        value = value(expr)
                    return {"result": {"value": value}}
            if "data-atlas-idx" in expr and "for (const el of" in expr:
                return {"result": {"value": self.snapshot_payload}}
            if "document.readyState" in expr:
                return {"result": {"value": "complete"}}
            if "document.activeElement" in expr:
                return {"result": {"value": self.active_value}}
            if "location.href" in expr and "querySelectorAll" in expr:
                return {"result": {"value": "https://x|t|3"}}
            if "querySelector('article')" in expr:
                return {"result": {"value": {"title": "T", "url": "https://x",
                                             "text": "contenido legible"}}}
            if "document.title" in expr and "location.href" in expr:
                return {"result": {"value": {"title": "T", "url": "https://x"}}}
            return {"result": {"value": None}}
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(b"jpegdata").decode()}
        if method == "Page.navigate":
            return {"frameId": "F1"}
        return {}


SNAPSHOT = [
    {"idx": 1, "tag": "button", "role": "button", "name": "Enviar", "editable": False,
     "disabled": False, "x": 100, "y": 50, "w": 80, "h": 30},
    {"idx": 2, "tag": "input", "role": "textbox", "name": "Buscar", "editable": True,
     "disabled": False, "x": 10, "y": 10, "w": 200, "h": 30},
    {"idx": 3, "tag": "button", "role": "button", "name": "Deshabilitado", "editable": False,
     "disabled": True, "x": 0, "y": 0, "w": 50, "h": 20},
]


def make_page(**kwargs) -> PageController:
    conn = FakeCDPConnection(**kwargs)
    return PageController(conn=conn, session_id="S1", target_id="T1")


class TestSnapshot:
    async def test_snapshot_parsea_elementos(self):
        page = make_page(snapshot_payload=SNAPSHOT)
        elements = await page.snapshot()
        assert len(elements) == 3
        assert elements[0].name == "Enviar"
        assert elements[1].editable is True
        assert elements[2].disabled is True
        assert elements[0].center == (100, 50)

    async def test_find_exacto_prefijo_contiene(self):
        els = [
            InteractiveElement(idx=1, tag="button", role="button", name="Enviar mensaje"),
            InteractiveElement(idx=2, tag="a", role="link", name="Configuración avanzada"),
        ]
        assert PageController.find(els, "enviar mensaje").idx == 1       # exacto (case-insens)
        assert PageController.find(els, "Enviar").idx == 1               # prefijo
        assert PageController.find(els, "avanzada").idx == 2             # contiene
        assert PageController.find(els, "no existe") is None

    async def test_find_ignora_deshabilitados(self):
        page = make_page(snapshot_payload=SNAPSHOT)
        elements = await page.snapshot()
        assert PageController.find(elements, "Deshabilitado") is None


class TestClick:
    async def test_click_por_indice(self):
        page = make_page()
        page._conn.when_js('[data-atlas-idx="1"]',
                           {"found": True, "tag": "button", "name": "Enviar", "x": 5, "y": 6})
        result = await page.click(1)
        assert result["found"] is True
        exprs = [p.get("expression", "") for m, p, _ in page._conn.calls
                 if m == "Runtime.evaluate"]
        assert any('data-atlas-idx="1"' in e for e in exprs)

    async def test_click_elemento_desaparecido(self):
        page = make_page()
        page._conn.when_js('[data-atlas-idx="9"]', {"found": False})
        result = await page.click(9)
        assert result["found"] is False

    async def test_click_at_envia_eventos_sinteticos(self):
        page = make_page()
        await page.click_at(120, 80)
        mouse = [c for c in page._conn.calls if c[0] == "Input.dispatchMouseEvent"]
        assert [m[1]["type"] for m in mouse] == ["mousePressed", "mouseReleased"]
        assert mouse[0][1]["x"] == 120 and mouse[0][1]["y"] == 80


class TestTypeText:
    async def test_insert_text_ok(self):
        page = make_page(active_value="hola mundo")
        page._conn.when_js('[data-atlas-idx="2"]', {"found": True, "editable": True})
        result = await page.type_text(2, "hola mundo")
        assert result["ok"] is True and result["via"] == "insertText"
        insert = [c for c in page._conn.calls if c[0] == "Input.insertText"]
        assert insert and insert[0][1]["text"] == "hola mundo"

    async def test_fallback_setter_nativo(self):
        page = make_page(active_value="")  # insertText no dejó nada
        page._conn.when_js('[data-atlas-idx="2"]', {"found": True, "editable": True})
        page._conn.when_js("Object.getOwnPropertyDescriptor", True)
        result = await page.type_text(2, "texto rebelde")
        assert result["ok"] is True and result["via"] == "native_setter"

    async def test_campo_no_editable(self):
        page = make_page()
        page._conn.when_js('[data-atlas-idx="1"]', {"found": True, "editable": False})
        result = await page.type_text(1, "x")
        assert result["ok"] is False and result["reason"] == "not_editable"


class TestKeysAndNav:
    async def test_tecla_enter(self):
        page = make_page()
        assert await page.key("enter") is True
        keys = [c for c in page._conn.calls if c[0] == "Input.dispatchKeyEvent"]
        assert [k[1]["type"] for k in keys] == ["rawKeyDown", "keyUp"]

    async def test_tecla_desconocida(self):
        page = make_page()
        assert await page.key("f13") is False

    async def test_navigate_rechaza_error(self):
        class FailingConn(FakeCDPConnection):
            async def send(self, method, params=None, session_id=None, timeout=20.0):
                if method == "Page.navigate":
                    return {"errorText": "net::ERR_NAME_NOT_RESOLVED"}
                return await super().send(method, params, session_id, timeout)

        page = PageController(conn=FailingConn(), session_id="S", target_id="T")
        with pytest.raises(CDPError, match="ERR_NAME_NOT_RESOLVED"):
            await page.navigate("https://dominio-que-no-existe.x")

    async def test_navigate_espera_dom_estable(self):
        """Reproduce el bug visto en Gmail: readyState completa antes de que el
        SPA renderice. navigate() debe esperar a que el conteo de elementos
        interactivos deje de crecer (2 sondeos iguales)."""
        conn = FakeCDPConnection()
        counts = iter([1, 3, 7, 7, 7])  # crece, crece, se estabiliza
        conn.when_js("contenteditable", lambda _expr: next(counts))
        page = PageController(conn=conn, session_id="S", target_id="T")

        import time
        t0 = time.monotonic()
        await page.navigate("https://ejemplo.test", timeout=5.0)
        elapsed = time.monotonic() - t0

        count_evals = [
            c for c in conn.calls
            if c[0] == "Runtime.evaluate" and "contenteditable" in c[1].get("expression", "")
        ]
        assert len(count_evals) >= 4, "debe sondear hasta estabilidad"
        assert elapsed >= 0.9, f"demasiado rápido ({elapsed:.2f}s), no esperó estabilidad"

    async def test_navigate_no_bloquea_si_js_anomalo(self):
        """Si el conteo no devuelve número, navigate no debe colgarse."""
        conn = FakeCDPConnection()  # sin handler: el count cae al default None
        page = PageController(conn=conn, session_id="S", target_id="T")

        import asyncio, time
        t0 = time.monotonic()
        await page.navigate("https://ejemplo.test", timeout=5.0)
        assert time.monotonic() - t0 < 2.0

    async def test_screenshot_decodifica_base64(self):
        page = make_page()
        data = await page.screenshot()
        assert data == b"jpegdata"


class TestReadTextYScrollSpa:
    async def test_read_text_prefiere_role_main(self):
        """SPAs tipo Gmail exponen el contenido en <div role="main">; sin ese
        fallback el 'leer' devolvía todo el <body> con la bandeja enterrada."""
        page = make_page()
        await page.read_text()
        exprs = [p.get("expression", "") for m, p, _ in page._conn.calls
                 if m == "Runtime.evaluate"]
        assert any('[role="main"]' in e for e in exprs)

    async def test_scroll_tiene_fallback_a_contenedor_interno(self):
        """En Gmail window.scrollBy no hace nada (posición 0px tras el scroll):
        el JS debe buscar el contenedor scrollable interno más grande."""
        page = make_page()
        page._conn.when_js("scrollBy", 640)
        y = await page.scroll("abajo")
        assert y == 640
        from src.cdp.page import _JS_SCROLL
        assert "scrollHeight" in _JS_SCROLL and "scrollTop" in _JS_SCROLL
