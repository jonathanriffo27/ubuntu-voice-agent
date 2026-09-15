"""Tests del WorktreeManager (Fase 3) y del flujo DeveloperAgent con aislamiento.

Usan repositorios git REALES en /tmp (rápido, sin red): es la única forma de
probar honestamente git worktree add/merge/branch. Pytest corre con el intérprete
del venv activo dentro del repo temporal.
"""
import json
import os
import subprocess
import sys
import uuid

import pytest

from src.agents.developer_agent import DeveloperAgent
from src.agents.tools import AgentCodeTools
from src.agents.workspace import WorktreeManager


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)


@pytest.fixture()
def repo(tmp_path):
    """Repositorio git mínimo con suite pytest trivial en verde."""
    repo = tmp_path / "proyecto"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "-c", "user.name=Tester", "-c", "user.email=t@t.cl", "commit", "--allow-empty", "-m", "init")
    (repo / "app.py").write_text("def suma(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import suma\n\ndef test_suma():\n    assert suma(1, 2) == 3\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Tester", "-c", "user.email=t@t.cl", "commit", "-m", "código inicial")
    return str(repo)


@pytest.fixture()
def manager(repo):
    # pytest del intérprete actual (el venv del proyecto) dentro del repo temporal
    return WorktreeManager(project_root=repo, pytest_cmd=[sys.executable, "-m", "pytest", "-q"])


class TestWorktreeManager:
    def test_create_aisla_cambios(self, repo, manager):
        info = manager.create("tarea1")
        assert os.path.isdir(info.path)
        assert info.branch == "agent/tarea1"
        # Escribir en el worktree NO toca el repo principal
        with open(os.path.join(info.path, "nuevo.py"), "w") as f:
            f.write("X = 1\n")
        assert not os.path.exists(os.path.join(repo, "nuevo.py"))
        assert manager.has_changes(info)
        manager.discard(info)
        assert not os.path.exists(info.path)

    def test_commit_y_diff(self, repo, manager):
        info = manager.create("tarea2")
        with open(os.path.join(info.path, "documento.md"), "w") as f:
            f.write("# Hola\n")
        assert manager.commit_all(info)
        diff = manager.diff(info)
        assert "documento.md" in diff and "+# Hola" in diff
        manager.discard(info)

    def test_tests_pasan_y_fallan(self, repo, manager):
        info = manager.create("tarea3")
        ok, out = manager.run_tests(info)
        assert ok, out
        # Romper un test
        with open(os.path.join(info.path, "tests", "test_app.py"), "a") as f:
            f.write("\ndef test_falla():\n    assert False\n")
        ok, out = manager.run_tests(info)
        assert not ok
        manager.discard(info)

    def test_merge_back_integra_al_repo(self, repo, manager):
        info = manager.create("tarea4")
        with open(os.path.join(info.path, "feature.py"), "w") as f:
            f.write("def feature():\n    return 42\n")
        manager.commit_all(info)
        ok, msg = manager.run_tests(info)
        assert ok, msg
        ok, msg = manager.merge_back(info)
        assert ok, msg
        assert os.path.exists(os.path.join(repo, "feature.py"))
        manager.finalize(info)
        assert not os.path.exists(info.path)
        # La rama quedó borrada tras finalize
        ramas = _git(repo, "branch", "--list", "agent/*").stdout
        assert "agent/tarea4" not in ramas

    def test_merge_conflictivo_aborta_limpio(self, repo, manager):
        # El conflicto real exige que main AVANCE después de crear el worktree
        info = manager.create("tarea5")

        # Main se mueve a su propia versión de suma
        with open(os.path.join(repo, "app.py"), "w") as f:
            f.write("def suma(a, b):\n    return a + b + 1000\n")
        _git(repo, "-c", "user.name=Tester", "-c", "user.email=t@t.cl", "commit", "-am", "main cambia suma")

        # El agente cambia las mismas líneas en su rama
        with open(os.path.join(info.path, "app.py"), "w") as f:
            f.write("def suma(a, b):\n    return a + b + 9999\n")
        manager.commit_all(info)
        ok, msg = manager.merge_back(info)
        assert not ok
        assert "onflicto" in msg or "CONFLICT" in msg or "abort" in msg.lower()
        # El repo principal no quedó a medio mergear
        estado = _git(repo, "status", "--porcelain").stdout
        assert "UU" not in estado
        manager.discard(info)

    def test_cleanup_stale(self, repo, manager):
        info = manager.create("stale1")
        # Simular crash: el manager olvida pero el worktree físico queda
        manager._active.clear()
        assert manager.cleanup_stale() >= 1
        assert not os.path.exists(info.path)


class _FakeClientDev:
    """Cliente CLIProxy falso: escribe un archivo y luego da por terminada la tarea."""

    def __init__(self, nombre_archivo="nota_agente.md", contenido="# Nota del agente\n"):
        self.nombre = nombre_archivo
        self.contenido = contenido
        self._called = 0

    async def chat_completion(self, messages, model, tools, temperature=0.2):
        self._called += 1
        if self._called == 1:
            args = json.dumps({"ruta_relativa": self.nombre, "contenido": self.contenido})
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {"name": "escribir_archivo", "arguments": args},
                        }],
                    }
                }]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Listo, archivo creado."}}]}


class _FakeApproval:
    def __init__(self, approved=True):
        self.approved = approved
        self.requests = []

    async def request_approval(self, action_type, description, payload="", timeout=60.0):
        self.requests.append({"action_type": action_type, "description": description, "payload": payload})
        return self.approved


class TestDeveloperAgentWorktreeFlow:
    async def test_tarea_aislada_mergea_tras_aprobacion(self, repo, manager):
        approval = _FakeApproval(approved=True)
        base_tools = AgentCodeTools(approval_manager=approval)
        agent = DeveloperAgent(client=_FakeClientDev(), code_tools=base_tools,
                               model="fake", worktree_manager=manager)
        resultado = await agent.run_task("crea una nota", task_id="wtask1")

        # El archivo llegó al repo principal por merge, NO por escritura directa
        assert os.path.exists(os.path.join(repo, "nota_agente.md"))
        assert "integrad" in resultado.lower() or "merge" in resultado.lower()
        # Se pidió UNA sola aprobación: la del merge (la escritura fue aislada)
        tipos = [r["action_type"] for r in approval.requests]
        assert tipos == ["git_merge"], tipos
        assert "diff" in approval.requests[0]["payload"].lower() or "nota_agente.md" in approval.requests[0]["payload"]

    async def test_rechazo_no_integra_pero_conserva(self, repo, manager):
        approval = _FakeApproval(approved=False)
        agent = DeveloperAgent(client=_FakeClientDev(), code_tools=AgentCodeTools(approval_manager=approval),
                               model="fake", worktree_manager=manager)
        resultado = await agent.run_task("crea una nota", task_id="wtask2")

        assert not os.path.exists(os.path.join(repo, "nota_agente.md"))
        assert "rechaz" in resultado.lower()

    async def test_sin_worktree_manager_modo_legacy_intacto(self, repo):
        approval = _FakeApproval(approved=True)
        tools = AgentCodeTools(approval_manager=approval, project_root=repo)
        agent = DeveloperAgent(client=_FakeClientDev(), code_tools=tools, model="fake")
        resultado = await agent.run_task("crea una nota", task_id="legacy1")
        assert "Listo" in resultado
        assert os.path.exists(os.path.join(repo, "nota_agente.md"))
        # En modo legacy la escritura SÍ pidió aprobación individual
        tipos = [r["action_type"] for r in approval.requests]
        assert "file_write" in tipos

    async def test_path_jail_en_worktree(self, repo, manager):
        """El path jail sigue firme dentro del worktree (sin escapes absolutos)."""
        approval = _FakeApproval()
        tools = AgentCodeTools(approval_manager=approval, project_root=repo,
                               require_write_approval=False)
        out = await tools.execute_tool("escribir_archivo", {"ruta_relativa": "/etc/pwned", "contenido": "x"})
        assert "Error" in out
        out = await tools.execute_tool("escribir_archivo", {"ruta_relativa": "../../etc/pwned", "contenido": "x"})
        assert "Error" in out
