"""
Tests del historial de tareas del DeveloperAgent.

Regresión del incidente: 'consultar_estado_tarea' devolvía "not_found" para una
tarea YA FINALIZADA, porque el finally de run_task la borraba de _active_tasks
y no quedaba rastro. Ahora se conserva un historial acotado.
"""
from unittest.mock import MagicMock

from src.agents.developer_agent import DeveloperAgent


def _make_agent() -> DeveloperAgent:
    return DeveloperAgent(
        client=MagicMock(),
        code_tools=MagicMock(),
        event_bus=None,
    )


class TestHistorial:
    def test_tarea_finalizada_consultable_por_id(self):
        agent = _make_agent()
        agent._record_history("8f0b5c49", "Corrige el typo del README", "completed", "Hecho.")
        status = agent.get_task_status("8f0b5c49")
        assert status["status"] == "completed"
        assert status["detail"] == "Hecho."
        assert status["instruction"] == "Corrige el typo del README"
        assert "finished_at" in status

    def test_error_queda_registrado(self):
        agent = _make_agent()
        agent._record_history("abc12345", "romper todo", "error", "boom")
        status = agent.get_task_status("abc12345")
        assert status["status"] == "error"
        assert "boom" in status["detail"]

    def test_id_desconocido_sigue_siendo_not_found(self):
        agent = _make_agent()
        status = agent.get_task_status("ffffffff")
        assert status["status"] == "not_found"

    def test_sin_id_lista_historial_reciente(self):
        agent = _make_agent()
        agent._record_history("aaaa1111", "tarea uno", "completed", "ok")
        agent._record_history("bbbb2222", "tarea dos", "error", "falló")
        status = agent.get_task_status()
        assert status["aaaa1111"]["status"] == "completed"
        assert status["bbbb2222"]["status"] == "error"

    def test_historial_acotado_fifo(self):
        agent = _make_agent()
        agent._max_history = 3
        for i in range(5):
            agent._record_history(f"t{i:07d}", f"instrucción {i}", "completed", "ok")
        assert len(agent._task_history) == 3
        assert agent.get_task_status("t0000000")["status"] == "not_found"
        assert agent.get_task_status("t0000004")["status"] == "completed"

    def test_sin_tareas_ni_historial_reporta_no_tasks(self):
        agent = _make_agent()
        assert agent.get_task_status()["status"] == "no_tasks"


class TestDeteccionDuplicados:
    def test_instruccion_identica_detectada(self):
        agent = _make_agent()
        agent._record_history("627ff237", "Crea un archivo llamado 'hola.txt' con el texto 'prueba'",
                              "completed", "Hecho.")
        dup = agent.find_similar_completed("Crea un archivo llamado 'hola.txt' con el texto 'prueba'")
        assert dup is not None
        assert dup["task_id"] == "627ff237"

    def test_normaliza_espacios_y_mayusculas(self):
        agent = _make_agent()
        agent._record_history("627ff237", "Crear  hola.txt", "completed", "ok")
        assert agent.find_similar_completed("crear hola.txt") is not None

    def test_instruccion_distinta_no_matchea(self):
        agent = _make_agent()
        agent._record_history("627ff237", "Crear hola.txt", "completed", "ok")
        assert agent.find_similar_completed("Corrige el typo del README") is None

    def test_historial_vacio_no_matchea(self):
        agent = _make_agent()
        assert agent.find_similar_completed("cualquier cosa") is None
