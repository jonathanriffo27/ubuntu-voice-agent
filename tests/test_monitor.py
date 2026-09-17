"""Tests del monitor de anomalías de acciones (Fase 6)."""
import pytest

from src.security.monitor import ActionMonitor, monitor_action_name


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def tick(self, s=1.0):
        self.t += s


def make_monitor(**kw):
    clock = kw.pop("clock", None) or FakeClock()
    return ActionMonitor(clock=clock, **kw), clock


class TestNormalNoDispara:
    def test_flujo_humano_tipico_sin_alerta(self):
        mon, _ = make_monitor()
        assert mon.record("navegador_web.abrir", "gmail.com") is None
        assert mon.record("navegador_web.leer") is None
        assert mon.record("buscar_en_internet", "clima") is None
        assert mon.record("reproducir_musica", "lofi") is None

    def test_dos_escrituras_rapidas_no_son_racha(self):
        mon, _ = make_monitor()
        assert mon.record("navegador_web.click", "Enviar") is None
        assert mon.record("navegador_web.click", "Aceptar") is None


class TestRepetition:
    def test_misma_accion_mismo_payload_dispara(self):
        mon, _ = make_monitor(repeat_threshold=3)
        assert mon.record("interactuar_gui", "click en Enviar") is None
        assert mon.record("interactuar_gui", "click en Enviar") is None
        alert = mon.record("interactuar_gui", "click en Enviar")
        assert alert is not None and alert.rule == "repeat"
        assert "3 veces" in alert.reason

    def test_misma_accion_payload_distinto_no_dispara(self):
        mon, _ = make_monitor(repeat_threshold=3)
        assert mon.record("navegador_web.click", "Botón A") is None
        assert mon.record("navegador_web.click", "Botón B") is None
        assert mon.record("navegador_web.click", "Botón C") is None

    def test_reset_desarma_alerta(self):
        mon, _ = make_monitor(repeat_threshold=3)
        for _ in range(3):
            mon.record("interactuar_gui", "click en X")
        assert mon.alerted
        mon.reset()
        assert not mon.alerted
        assert mon.record("navegador_web.leer") is None


class TestBurst:
    def test_rafaga_total(self):
        mon, _ = make_monitor(burst_total=5)
        alert = None
        for i in range(5):
            r = mon.record(f"herramienta_{i}")
            if r:
                alert = r
        assert alert is not None and alert.rule == "burst_total"

    def test_rafaga_riesgosa(self):
        mon, _ = make_monitor(burst_total=100, burst_risky=3)
        assert mon.record("navegador_web.click", "A") is None
        assert mon.record("navegador_web.click", "B") is None
        alert = mon.record("navegador_web.click", "C")
        assert alert is not None and alert.rule == "burst_risky"
        assert "3 acciones de escritura" in alert.reason

    def test_lecturas_no_cuentan_como_riesgo(self):
        mon, _ = make_monitor(burst_total=100, burst_risky=3)
        for i in range(5):
            assert mon.record("buscar_en_internet", f"consulta {i}") is None

    def test_ventana_expira_eventos_viejos(self):
        mon, clock = make_monitor(burst_total=3)
        assert mon.record("buscar_en_internet", "1") is None
        assert mon.record("buscar_en_internet", "2") is None
        clock.tick(120)  # fuera de la ventana de 60s
        assert mon.record("buscar_en_internet", "3") is None  # no burst: expiraron


class TestPausa:
    def test_persiste_hasta_reset(self):
        mon, _ = make_monitor(repeat_threshold=3)
        for _ in range(3):
            mon.record("interactuar_gui", "X")
        alert = mon.record("navegador_web.leer")  # acción inocente, monitor en pausa
        assert alert is not None and alert.rule == "pause"


class TestMonitorActionName:
    """El monitor debe ver la sub-acción punteada, como las tools al clasificar."""

    def test_extrae_sub_accion(self):
        assert monitor_action_name("navegador_web", {"accion": "elementos"}) == "navegador_web.elementos"
        assert monitor_action_name("interactuar_gui", {"accion": "Leer"}) == "interactuar_gui.leer"

    def test_sin_accion_devuelve_nombre_pelado(self):
        assert monitor_action_name("buscar_en_internet", {"query": "x"}) == "buscar_en_internet"
        assert monitor_action_name("abrir_aplicacion", None) == "abrir_aplicacion"
        assert monitor_action_name("navegador_web", {"accion": ""}) == "navegador_web"


class TestSecuenciaGmailIncidente:
    """Regresión del incidente real: abrir Gmail -> leer GUI vacía -> fallback
    navegador con varias lecturas NO debe disparar burst_risky."""

    def test_flujo_lectura_gmail_sin_alerta(self):
        mon, _ = make_monitor()
        secuencia = [
            ("controlar_musica", {"accion": "play"}),
            ("controlar_musica", {"accion": "pausar"}),
            ("abrir_aplicacion", {"nombre": "gmail"}),
            ("interactuar_gui", {"accion": "leer", "app": "gmail"}),
            ("navegador_web", {"accion": "abrir", "objetivo": "https://mail.google.com/"}),
            ("navegador_web", {"accion": "leer"}),
            ("navegador_web", {"accion": "elementos"}),
            ("navegador_web", {"accion": "pestanas"}),
        ]
        import json
        for nombre, args in secuencia:
            alert = mon.record(monitor_action_name(nombre, args),
                               json.dumps(args, ensure_ascii=False))
            assert alert is None, f"Falso positivo en {nombre}: {alert and alert.reason}"

    def test_racha_risky_real_sigue_disparando(self):
        """La protección sigue ahí: acciones de escritura repetidas SÍ alertan."""
        import json
        mon, _ = make_monitor()
        alertas = []
        for i in range(6):
            args = {"accion": "click", "objetivo": f"boton {i}"}
            alert = mon.record(monitor_action_name("navegador_web", args),
                               json.dumps(args, ensure_ascii=False))
            if alert:
                alertas.append(alert)
        assert any(a.rule == "burst_risky" for a in alertas)
