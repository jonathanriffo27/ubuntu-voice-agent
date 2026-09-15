"""Tests del ElementResolver: matching por lenguaje natural y renderizado SoM."""
import io

import pytest
from PIL import Image

from src.input.resolver import ElementResolver, GuiElement, _normalize


def _el(index, name, role="push_button", bounds=(10, 20, 100, 30), editable=False, actions=None, app="brave"):
    return GuiElement(
        index=index, name=name, role=role, bounds=bounds,
        is_editable=editable, is_focusable=True,
        actions=list(actions) if actions is not None else ["click"],
        app_name=app, node=None,
    )


class TestNormalize:
    def test_acentos_y_mayusculas(self):
        assert _normalize("  Búsquedá  RÁPIDA ") == "busqueda rapida"

    def test_vacio(self):
        assert _normalize("") == ""


class TestFind:
    def test_match_exacto(self):
        elementos = [_el(1, "Enviar"), _el(2, "Cancelar")]
        assert ElementResolver().find("Enviar", elementos).index == 1

    def test_match_con_acentos_y_case(self):
        elementos = [_el(1, "Búsqueda"), _el(2, "Cancelar")]
        assert ElementResolver().find("busqueda", elementos).index == 1

    def test_match_parcial(self):
        elementos = [_el(1, "Buscar un chat o empezar uno nuevo"), _el(2, "Adjuntar")]
        assert ElementResolver().find("buscar chat", elementos).index == 1

    def test_bonus_por_rol_boton(self):
        # El label exacto existe en un texto estático, pero se pide "el botón Enviar"
        elementos = [
            _el(1, "Enviar", role="static_text", actions=[]),
            _el(2, "Enviar", role="push_button", actions=["click"]),
        ]
        ganador = ElementResolver().find("botón Enviar", elementos)
        assert ganador.index == 2

    def test_bonus_por_editable_en_campo(self):
        elementos = [
            _el(1, "Buscar", role="static_text", editable=False),
            _el(2, "Buscar", role="entry", editable=True),
        ]
        assert ElementResolver().find("campo buscar", elementos).index == 2

    def test_sin_coincidencia_devuelve_none(self):
        elementos = [_el(1, "Aceptar"), _el(2, "Rechazar")]
        assert ElementResolver().find("Zanzíbar inexistente", elementos) is None

    def test_lista_vacia_o_query_vacia(self):
        assert ElementResolver().find("x", []) is None
        assert ElementResolver().find("", [_el(1, "A")]) is None

    def test_fuzzy_por_encima_de_umbral(self):
        elementos = [_el(1, "Configuración")]
        assert ElementResolver().find("configuracion", elementos).index == 1


class TestGeometry:
    def test_center(self):
        el = _el(1, "A", bounds=(100, 200, 50, 40))
        assert el.center == (125, 220)

    def test_center_none_sin_bounds(self):
        el = _el(1, "A", bounds=None)
        assert el.center is None

    def test_can_click_semantic(self):
        assert _el(1, "A", actions=["click"]).can_click_semantic
        assert not _el(1, "A", actions=[]).can_click_semantic


class TestRenderSom:
    def _jpeg(self, w=800, h=600):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (200, 200, 200)).save(buf, format="JPEG")
        return buf.getvalue()

    def test_som_dibuja_y_devuelve_jpeg(self):
        elementos = [
            _el(1, "Enviar", bounds=(100, 100, 80, 30)),
            _el(2, "Cancelar", bounds=(300, 400, 90, 40)),
        ]
        out, shown = ElementResolver().render_som(self._jpeg(), elementos)
        assert len(shown) == 2
        img = Image.open(io.BytesIO(out))
        assert img.format == "JPEG" and img.size == (800, 600)
        assert out != self._jpeg()  # la imagen cambió (se dibujó encima)

    def test_som_omite_elementos_sin_bounds(self):
        elementos = [_el(1, "Invisible", bounds=None), _el(2, "Visible", bounds=(10, 10, 20, 20))]
        out, shown = ElementResolver().render_som(self._jpeg(), elementos)
        assert [e.index for e in shown] == [2]

    def test_som_con_escala(self):
        # bounds físicos 2000px mapeados a imagen de 1000px => scale 2.0
        elementos = [_el(1, "A", bounds=(200, 200, 100, 50))]
        out, shown = ElementResolver().render_som(self._jpeg(1000, 500), elementos, scale_x=2.0, scale_y=2.0)
        assert len(shown) == 1

    def test_som_legend(self):
        elementos = [
            _el(1, "Campo", role="entry", editable=True, actions=[]),
            _el(2, "Enviar", actions=["click"]),
        ]
        leyenda = ElementResolver.som_legend(elementos)
        assert "[1]" in leyenda and "editable" in leyenda
        assert "[2]" in leyenda and "accionable" in leyenda


class _FakeNode:
    def __init__(self, name="", role="frame"):
        self._name = name
        self._role = role

    def get_name(self):
        return self._name

    def get_role_name(self):
        return self._role


class _FakeApp(_FakeNode):
    def __init__(self, name, windows):
        super().__init__(name, "application")
        self._windows = windows

    def get_child_count(self):
        return len(self._windows)

    def get_child_at_index(self, i):
        return self._windows[i] if i < len(self._windows) else None


class _FakeDesktop:
    def __init__(self, apps):
        self._apps = apps

    def get_child_count(self):
        return len(self._apps)

    def get_child_at_index(self, i):
        return self._apps[i] if i < len(self._apps) else None


class _FakeSensor:
    _atspi_gobject_ready = True

    def __init__(self, nodes_per_window):
        self.nodes_per_window = nodes_per_window

    def is_available(self):
        return True

    def find_interactive_nodes(self, root_node, max_depth=25):
        return self.nodes_per_window


class TestListElements:
    def test_enumera_con_hint_primero(self, monkeypatch):
        app_brave = _FakeApp("brave", [_FakeNode("WhatsApp", "frame")])
        app_gedit = _FakeApp("gedit", [_FakeNode("Doc", "frame")])
        fake_atspi = type("AtspiMod", (), {
            "get_desktop": staticmethod(lambda i: _FakeDesktop([app_gedit, app_brave]))
        })
        monkeypatch.setattr("src.utils.a11y.Atspi", fake_atspi)

        nodos = [{
            "node": object(), "name": "Enviar", "role": "push_button",
            "is_editable": False, "is_focusable": True,
            "actions": ["click"], "bounds": (10, 10, 50, 20),
        }]
        sensor = _FakeSensor(nodos)
        resolver = ElementResolver(sensor=sensor)
        elementos = resolver.list_elements(app_hint="brave")
        assert len(elementos) == 2  # una por ventana de cada app
        assert elementos[0].app_name == "brave"  # la del hint primero
        assert elementos[0].index == 1 and elementos[1].index == 2

    def test_omite_nodos_sin_bounds(self, monkeypatch):
        app = _FakeApp("brave", [_FakeNode("Win", "frame")])
        fake_atspi = type("AtspiMod", (), {"get_desktop": staticmethod(lambda i: _FakeDesktop([app]))})
        monkeypatch.setattr("src.utils.a11y.Atspi", fake_atspi)
        nodos = [{"node": object(), "name": "X", "role": "button", "is_editable": False,
                  "is_focusable": True, "actions": [], "bounds": None}]
        resolver = ElementResolver(sensor=_FakeSensor(nodos))
        assert resolver.list_elements() == []

    def test_sensor_no_disponible(self):
        class S(_FakeSensor):
            _atspi_gobject_ready = True
            def is_available(self):
                return False
        assert ElementResolver(sensor=S([])).list_elements() == []
