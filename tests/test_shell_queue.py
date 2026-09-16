"""
Tests de la cola FIFO de comandos pendientes (CommandState).

Regresión del incidente: proponer 'ls' y luego 'cat ~/.ssh/id_rsa' y decir
"apruebo" ejecutaba SOLO el segundo — el slot único sobrescribía el primero.
"""
import pytest

from src.plugins.shell.tools import CommandState


class TestColaFIFO:
    def test_multiples_propuestas_se_conservan_en_orden(self):
        state = CommandState(timeout_seconds=60)
        assert state.propose("ls") == 1
        assert state.propose("pwd") == 2
        assert state.propose("whoami") == 3
        assert state.list_pending() == ["ls", "pwd", "whoami"]

    def test_get_pending_drena_en_orden_sin_perder_ninguno(self):
        state = CommandState(timeout_seconds=60)
        state.propose("ls")
        state.propose("cat archivo.txt")
        assert state.get_pending() == "ls"          # el primero ya no se pierde
        assert state.get_pending() == "cat archivo.txt"
        assert state.get_pending() is None

    def test_expiracion_descarta_solo_los_vencidos(self, monkeypatch):
        reloj = {"t": 1000.0}
        monkeypatch.setattr("src.plugins.shell.tools.time.time", lambda: reloj["t"])
        state = CommandState(timeout_seconds=30)
        state.propose("viejo")
        reloj["t"] = 1005.0
        state.propose("reciente")
        reloj["t"] = 1035.0  # 'viejo' expiró, 'reciente' no
        assert state.list_pending() == ["reciente"]
        assert state.get_pending() == "reciente"
        assert state.get_pending() is None

    def test_clear_vacia_la_cola(self):
        state = CommandState(timeout_seconds=60)
        state.propose("ls")
        state.propose("pwd")
        state.clear()
        assert state.list_pending() == []
        assert state.get_pending() is None

    def test_cola_acotada_descarta_el_mas_antiguo(self):
        state = CommandState(timeout_seconds=60, max_pending=2)
        state.propose("uno")
        state.propose("dos")
        assert state.propose("tres") == 2
        assert state.list_pending() == ["dos", "tres"]


class TestIntegracionTools:
    @pytest.fixture
    def context(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            config=SimpleNamespace(tools=SimpleNamespace(shell=SimpleNamespace(enabled=True, confirmation="always")))
        )

    async def test_propuesta_informa_del_tamano_de_cola(self, context):
        from src.plugins.shell.tools import CommandState, ProponerComandoTool
        state = CommandState(timeout_seconds=60)
        tool = ProponerComandoTool(state)
        r1 = await tool.execute(context, comando="ls")
        assert "encolad" not in r1.content  # primer comando: sin aviso extra
        r2 = await tool.execute(context, comando="pwd")
        assert "2 comandos pendientes" in r2.content

    async def test_ejecucion_reporta_restantes_en_cola(self, context):
        from src.plugins.shell.tools import (
            BashExecutor, CommandState, EjecutarComandoTool,
        )
        state = CommandState(timeout_seconds=60)
        tool = EjecutarComandoTool(BashExecutor(), state)
        state.propose("true")
        state.propose("echo hola")
        result = await tool.execute(context)
        assert result.success
        assert "Quedan 1 comando(s)" in result.content
        assert "echo hola" in result.content
        assert "confirmación" in result.content

    async def test_ejecucion_sin_cola_menciona_nada_extra(self, context):
        from src.plugins.shell.tools import (
            BashExecutor, CommandState, EjecutarComandoTool,
        )
        state = CommandState(timeout_seconds=60)
        tool = EjecutarComandoTool(BashExecutor(), state)
        state.propose("true")
        result = await tool.execute(context)
        assert result.success
        assert "pendientes en cola" not in result.content
