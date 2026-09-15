"""Tests de la herramienta interactuar_gui (Fase 1): cascada de resolución,
acciones semánticas vs coordenadas, verificación y política de riesgo."""
import pytest

from src.input.resolver import GuiElement
from src.plugins.gui_actions.tools import InteractuarGuiTool
from src.security.policy import SecurityPolicy
from src.tools.base import ToolContext


class FakeSensor:
    """Sensor AT-SPI2 falso con acciones semánticas registrables."""

    def __init__(self):
        self.clicked = []
        self.focused = []
        self.typed = []
        self.click_ok = True
        self.set_text_ok = True

    def click_element_action(self, node, action_index=0):
        if self.click_ok:
            self.clicked.append(node)
            return True
        return False

    def grab_element_focus(self, node):
        self.focused.append(node)
        return True

    def set_element_text(self, node, text):
        if self.set_text_ok:
            self.typed.append((node, text))
            return True
        return False


class FakeRouter:
    def __init__(self, works=True):
        self.works = works
        self.clicks = []
        self.keys = []
        self.texts = []

    def click(self, x, y, button="left", double=False):
        self.clicks.append((x, y, button, double))
        return self.works, "fake"

    def press_key(self, combo):
        self.keys.append(combo)
        return self.works, "fake"

    def type_text(self, text):
        self.texts.append(text)
        return self.works, "fake"


class FakeScreen:
    def capture_screen_with_metadata(self, max_dim=960, quality=60):
        return b"\xff\xd8fakejpeg", {"unchanged": False, "change_ratio": 0.34}


class FakeScreenNoChange(FakeScreen):
    def capture_screen_with_metadata(self, max_dim=960, quality=60):
        return b"\xff\xd8fakejpeg", {"unchanged": True, "change_ratio": 0.001}


class FakeResolver:
    def __init__(self, elements, found=None):
        self._elements = elements
        self._found = found
        self.sensor = FakeSensor()

    def list_elements(self, app_hint=None):
        return self._elements

    def find(self, query, elements, role_hint=None):
        return self._found


class FakeApproval:
    def __init__(self, approved=True):
        self.approved = approved
        self.requests = []

    async def request_approval(self, action_type, description, payload="", timeout=60.0):
        self.requests.append((action_type, description, payload))
        return self.approved


def _element(name="Enviar", role="push_button", bounds=(100, 100, 80, 30),
             editable=False, actions=("click",), node="NODO"):
    return GuiElement(
        index=1, name=name, role=role, bounds=bounds, is_editable=editable,
        is_focusable=True, actions=list(actions), app_name="brave", node=node,
    )


def _tool(element=None, *, router=None, screen=None, approval=None, resolver_elements=None):
    resolver = FakeResolver(resolver_elements or [], found=element)
    return InteractuarGuiTool(
        approval_manager=approval,
        resolver=resolver,
        router=router or FakeRouter(),
        screen_service=screen or FakeScreen(),
        policy=SecurityPolicy(),
    ), resolver


async def test_click_semantico_sin_robar_foco():
    el = _element(actions=["click"])
    router = FakeRouter()
    tool, _ = _tool(element=el, router=router)
    res = await tool.execute(ToolContext(config=None), accion="click", objetivo="Enviar")
    assert res.success
    assert "semántico" in res.content
    assert router.clicks == []  # nunca pasó por coordenadas


async def test_click_por_coordenadas_si_no_hay_accion_semantica():
    el = _element(actions=[])  # sin acciones AT-SPI2
    router = FakeRouter()
    tool, _ = _tool(element=el, router=router)
    res = await tool.execute(ToolContext(config=None), accion="click", objetivo="Enviar")
    assert res.success
    assert router.clicks == [(140, 115, "left", False)]  # centro exacto del bounds
    assert "pantalla cambió 34%" in res.content


async def test_click_avisa_si_pantalla_no_cambia():
    el = _element(actions=["click"])
    tool, _ = _tool(element=el, screen=FakeScreenNoChange())
    res = await tool.execute(ToolContext(config=None), accion="click", objetivo="Enviar")
    assert res.success
    assert "no cambió" in res.content


async def test_doble_click_va_por_backend():
    el = _element(actions=["click"])
    router = FakeRouter()
    tool, _ = _tool(element=el, router=router)
    res = await tool.execute(ToolContext(config=None), accion="doble_click", objetivo="Enviar")
    assert res.success
    assert router.clicks[0][3] is True  # double=True


async def test_escribir_campo_editable_semantico():
    el = _element(name="Escribe un mensaje", role="entry", editable=True, actions=[])
    router = FakeRouter()
    tool, resolver = _tool(element=el, router=router)
    res = await tool.execute(ToolContext(config=None), accion="escribir",
                             objetivo="campo mensaje", texto="hola ¿qué tal? 🎉")
    assert res.success
    assert "AT-SPI2" in res.content
    assert resolver.sensor.typed == [("NODO", "hola ¿qué tal? 🎉")]
    assert router.texts == []  # no hizo falta portapapeles


async def test_escribir_fallback_portapapeles():
    el = _element(name="Caja", editable=False, actions=[])
    router = FakeRouter()
    tool, _ = _tool(element=el, router=router)
    res = await tool.execute(ToolContext(config=None), accion="escribir",
                             objetivo="caja", texto="texto")
    assert res.success
    assert router.clicks and router.texts == ["texto"]


async def test_elemento_no_encontrado_ofrece_candidatos():
    candidatos = [_element(name="Aceptar"), _element(name="Cancelar")]
    tool, _ = _tool(element=None, resolver_elements=candidatos)
    res = await tool.execute(ToolContext(config=None), accion="click", objetivo="Inexistente")
    assert not res.success
    assert "Aceptar" in res.content and "Cancelar" in res.content
    assert "No encontré" in res.content


async def test_tecla_directa_por_router():
    router = FakeRouter()
    tool, _ = _tool(router=router)
    res = await tool.execute(ToolContext(config=None), accion="tecla", objetivo="ctrl+l")
    assert res.success and router.keys == ["ctrl+l"]


async def test_leer_lista_elementos():
    elementos = [_element(name="Buscar", role="entry", editable=True, actions=[]),
                 _element(name="Enviar")]
    tool, _ = _tool(resolver_elements=elementos)
    res = await tool.execute(ToolContext(config=None), accion="leer", objetivo="whatsapp")
    assert res.success and "Buscar" in res.content and "Enviar" in res.content


async def test_accion_tier3_pide_aprobacion_y_se_rechaza():
    el = _element(name="Confirmar")
    approval = FakeApproval(approved=False)
    tool, _ = _tool(element=el, approval=approval)
    res = await tool.execute(ToolContext(config=None), accion="click",
                             objetivo="Confirmar compra ahora")
    assert not res.success
    assert "rechaz" in res.content.lower()
    assert len(approval.requests) == 1


async def test_accion_tier3_aprobada_ejecuta():
    el = _element(name="Confirmar", actions=["click"])
    approval = FakeApproval(approved=True)
    tool, resolver = _tool(element=el, approval=approval)
    res = await tool.execute(ToolContext(config=None), accion="click",
                             objetivo="Confirmar compra")
    assert res.success
    assert resolver.sensor.clicked  # la acción se ejecutó tras la aprobación


async def test_accion_inocua_no_pide_aprobacion():
    el = _element(name="Enviar mensaje")
    approval = FakeApproval(approved=True)
    tool, _ = _tool(element=el, approval=approval)
    res = await tool.execute(ToolContext(config=None), accion="click", objetivo="Enviar mensaje")
    assert res.success
    assert approval.requests == []  # tier 2: sin HITL


async def test_escribir_sin_texto_falla():
    tool, _ = _tool(element=_element(editable=True, role="entry"))
    res = await tool.execute(ToolContext(config=None), accion="escribir", objetivo="campo")
    assert not res.success
    assert "texto" in res.content.lower()


async def test_objetivo_vacio_falla():
    tool, _ = _tool()
    res = await tool.execute(ToolContext(config=None), accion="click", objetivo=" ")
    assert not res.success
