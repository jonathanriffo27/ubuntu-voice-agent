"""Tests del ComputerUseOrchestrator (Fase 2): bucle OODA completo.

Cadena bajo test: Decide(LLM falso) -> Act(interactuar_gui real con resolver/
router/screen falsos) -> Verify(firma de estado AT-SPI2 simulada).
"""
import asyncio
import json

import pytest

from src.agents.computer_use import ComputerUseOrchestrator, _extract_json
from src.events.base import TaskCompleted, TaskDelegated
from src.events.bus import EventBus
from src.input.resolver import GuiElement
from src.plugins.gui_actions.background import OperarGuiTareaTool
from src.plugins.gui_actions.tools import InteractuarGuiTool
from src.security.policy import SecurityPolicy
from src.tools.base import ToolContext


class FakeLLMClient:
    """Cliente LLM que devuelve decisiones JSON pre-scripted (o basura)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0
        self.last_user_message = ""

    async def chat_completion(self, messages, model, tools=None, temperature=0.1):
        self.calls += 1
        self.last_user_message = messages[-1]["content"]
        decision = self.decisions[min(self.calls - 1, len(self.decisions) - 1)]
        content = decision if isinstance(decision, str) else json.dumps(decision)
        return {"choices": [{"message": {"content": content}}]}


class FakeSensor:
    def click_element_action(self, node, action_index=0):
        return False

    def grab_element_focus(self, node):
        return True

    def set_element_text(self, node, text):
        return True


class FakeResolver:
    def __init__(self, elements):
        self._elements = elements
        self.sensor = FakeSensor()

    def list_elements(self, app_hint=None):
        return self._elements

    def find(self, query, elements, role_hint=None):
        return self._elements[0] if self._elements else None


class FakeRouter:
    def __init__(self):
        self.clicks = []
        self.keys = []
        self.texts = []

    def click(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        return True, "fake"

    def press_key(self, combo):
        self.keys.append(combo)
        return True, "fake"

    def type_text(self, text):
        self.texts.append(text)
        return True, "fake"


class FakeScreen:
    def capture_screen_with_metadata(self, max_dim=960, quality=60):
        return b"\xff\xd8fake", {"unchanged": False, "change_ratio": 0.3}


def _elementos():
    return [GuiElement(index=1, name="Enviar", role="push_button",
                       bounds=(100, 100, 80, 30), is_editable=False,
                       is_focusable=True, actions=[], app_name="brave", node="n")]


def _make_stack(decisions, elements=None, approval=None):
    """Construye la pila real: orchestrator + interactuar_gui con fakes dentro."""
    router = FakeRouter()
    resolver = FakeResolver(elements if elements is not None else _elementos())
    gui_tool = InteractuarGuiTool(
        approval_manager=approval,
        resolver=resolver, router=router, screen_service=FakeScreen(),
        policy=SecurityPolicy(),
    )
    bus = EventBus()
    client = FakeLLMClient(decisions)
    orq = ComputerUseOrchestrator(client=client, gui_tool=gui_tool, event_bus=bus,
                                  model="fake", max_steps=6, max_no_progress=2)
    return orq, router, client, bus


class TestExtractJson:
    def test_json_directo(self):
        assert _extract_json('{"accion": "click"}') == {"accion": "click"}

    def test_json_con_fences(self):
        assert _extract_json('```json\n{"accion": "esperar", "segundos": 1}\n```')["accion"] == "esperar"

    def test_json_embebido_en_texto(self):
        assert _extract_json('Claro, haré esto: {"accion": "tecla", "objetivo": "enter"} listo')["objetivo"] == "enter"

    def test_basura_devuelve_none(self):
        assert _extract_json("no es json") is None
        assert _extract_json("") is None
        assert _extract_json(None) is None


class TestOrchestrator:
    async def test_flujo_feliz_click_y_listo(self):
        orq, router, client, bus = _make_stack([
            {"accion": "click", "objetivo": "Enviar"},
            {"accion": "listo", "resultado": "Mensaje enviado correctamente."},
        ])
        completados = []
        bus.subscribe(TaskCompleted, lambda ev: completados.append(ev))

        resultado = await orq.run("envía el mensaje", None, "t1", asyncio.Event())

        assert "Mensaje enviado" in resultado
        assert router.clicks, "la acción debió ejecutarse por el backend"
        assert len(completados) == 1 and completados[0].success

    async def test_spotlighting_presente_en_prompt(self):
        """El contenido de pantalla entra al LLM envuelto como NO-CONFIABLE."""
        orq, router, client, bus = _make_stack([{"accion": "listo", "resultado": "ok"}])
        await orq.run("mira algo", None, "t2", asyncio.Event())
        assert "DATOS NO CONFIABLES" in client.last_user_message
        assert "FIN DEL CONTENIDO DE PANTALLA" in client.last_user_message

    async def test_aborta_si_no_hay_progreso(self):
        orq, router, client, bus = _make_stack([
            {"accion": "click", "objetivo": "Enviar"},
        ])
        completados = []
        bus.subscribe(TaskCompleted, lambda ev: completados.append(ev))

        resultado = await orq.run("tarea imposible", None, "t3", asyncio.Event())
        assert "no responde" in resultado.lower()
        assert completados and not completados[0].success

    async def test_json_invalido_se_recupera_y_aborta(self):
        orq, *_ = _make_stack(["esto no es json"])  # siempre basura
        async def run():
            return await orq.run("tarea", None, "t4", asyncio.Event())
        resultado = await asyncio.wait_for(run(), timeout=10)
        assert "no responde" in resultado.lower() or "no pude" in resultado.lower()

    async def test_cancelacion_cooperativa(self):
        orq, *_ = _make_stack([{"accion": "esperar", "segundos": 0.2}] * 10)
        task = asyncio.create_task(orq.run("tarea larga", None, "t5", orq._cancel_flags.setdefault("t5", asyncio.Event())))
        await asyncio.sleep(0.1)
        orq.cancel("t5")
        resultado = await task
        assert "cancelada" in resultado.lower()

    async def test_esperar_esta_acotado(self):
        orq, *_ = _make_stack([
            {"accion": "esperar", "segundos": 99},
            {"accion": "listo", "resultado": "hecho"},
        ])
        resultado = await orq.run("espera y acaba", None, "t6", asyncio.Event())
        assert resultado == "hecho"  # no se tardó 99s

    async def test_max_steps_respeta_limite(self):
        orq, *_ = _make_stack([
            {"accion": "click", "objetivo": "Enviar"},
        ])
        completados = []
        # Estado que SIEMPRE cambia para no abortar por falta de progreso
        class ResolverCambia(FakeResolver):
            def __init__(self):
                super().__init__(_elementos())
                self._n = 0
            def list_elements(self, app_hint=None):
                self._n += 1
                return [GuiElement(index=1, name=f"Enviar{self._n}", role="push_button",
                                   bounds=(100, 100, 80, 30), is_editable=False,
                                   is_focusable=True, actions=[], app_name="b", node="n")]
        orq.gui_tool = InteractuarGuiTool(
            approval_manager=None, resolver=ResolverCambia(),
            router=FakeRouter(), screen_service=FakeScreen(), policy=SecurityPolicy(),
        )
        resultado = await orq.run("click infinito", None, "t7", asyncio.Event())
        assert "Tiempo agotado" in resultado or "no responde" in resultado.lower()


class TestOperarGuiTareaTool:
    async def test_lanza_tarea_en_background(self):
        orq, *_ = _make_stack([{"accion": "listo", "resultado": "fin"}])
        tool = OperarGuiTareaTool(orchestrator=orq)
        res = await tool.execute(ToolContext(config=None), objetivo="haz la cosa", app="brave")
        assert res.success and res.metadata["task_id"]

    async def test_sin_orquestador_informa(self):
        tool = OperarGuiTareaTool(orchestrator=None, client=None, gui_tool=None)
        res = await tool.execute(ToolContext(config=None), objetivo="haz la cosa")
        assert not res.success
        assert "no está disponible" in res.content

    async def test_objetivo_vacio_falla(self):
        tool = OperarGuiTareaTool(orchestrator=object())
        res = await tool.execute(ToolContext(config=None), objetivo="  ")
        assert not res.success
