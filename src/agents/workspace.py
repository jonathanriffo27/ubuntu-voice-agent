"""
WorktreeManager: aislamiento de trabajo para el DeveloperAgent (Fase 3).

Cada tarea del subagente corre en un git worktree propio:
  - Escribe y prueba sin riesgo sobre el árbol de trabajo del usuario.
  - La integración a la rama principal pasa por pytest + diff + HITL.

Convenciones:
  - Worktrees en <repo>/.worktrees/agent-<task_id> (debe estar en .gitignore).
  - Ramas: agent/<task_id>.
  - Los tests se ejecutan con el venv del proyecto (fuera del worktree).
"""
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger("agents.workspace")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


@dataclass
class WorktreeInfo:
    task_id: str
    path: str
    branch: str
    base_sha: str


class WorktreeManager:
    """Ciclo de vida de worktrees de tareas: crear, probar, mergear, descartar."""

    def __init__(self, project_root: Optional[str] = None, pytest_cmd: Optional[List[str]] = None,
                 allow_main_working_tree: bool = True):
        self.root = project_root or _PROJECT_ROOT
        self.worktrees_dir = os.path.join(self.root, ".worktrees")
        # pytest del venv principal: el worktree no lleva .venv propio
        self.pytest_cmd = pytest_cmd or [os.path.join(self.root, "venv", "bin", "pytest"), "-q"]
        self._active: dict[str, WorktreeInfo] = {}

    # ------------------------------------------------------------------
    def _git(self, args: List[str], cwd: Optional[str] = None, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd or self.root,
            capture_output=True, text=True, timeout=timeout,
        )

    def git_available(self) -> bool:
        """El proyecto es un repo git utilizable."""
        try:
            proc = self._git(["rev-parse", "--is-inside-work-tree"], timeout=10)
            return proc.returncode == 0 and proc.stdout.strip() == "true"
        except Exception:
            return False

    # ------------------------------------------------------------------
    def create(self, task_id: str) -> WorktreeInfo:
        """Crea worktree + rama agent/<task_id> a partir del HEAD actual."""
        if not self.git_available():
            raise RuntimeError("El proyecto no es un repositorio git utilizable.")

        safe_id = "".join(c for c in task_id if c.isalnum() or c in ("-", "_"))[:24] or "task"
        path = os.path.join(self.worktrees_dir, f"agent-{safe_id}")
        branch = f"agent/{safe_id}"

        base = self._git(["rev-parse", "HEAD"])
        base_sha = base.stdout.strip() if base.returncode == 0 else "HEAD"

        self._git(["worktree", "remove", "--force", path], timeout=30)  # limpieza defensiva
        self._git(["branch", "-D", branch], timeout=30)

        proc = self._git(["worktree", "add", path, "-b", branch], timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"git worktree add falló: {proc.stderr.strip()}")

        info = WorktreeInfo(task_id=safe_id, path=path, branch=branch, base_sha=base_sha)
        self._active[safe_id] = info
        logger.info(f"🌿 Worktree creado para [{safe_id}]: {path} (rama {branch})")
        return info

    # ------------------------------------------------------------------
    def has_changes(self, info: WorktreeInfo) -> bool:
        proc = self._git(["status", "--porcelain"], cwd=info.path)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def commit_all(self, info: WorktreeInfo, message: Optional[str] = None) -> bool:
        """Commit de todo el trabajo del agente dentro del worktree."""
        if not self.has_changes(info):
            return False
        self._git(["add", "-A"], cwd=info.path)
        msg = message or f"agent({info.task_id}): cambios del DeveloperAgent"
        proc = self._git(["-c", f"user.name=Atlas Agent", "-c",
                          f"user.email=atlas-agent@localhost",
                          "commit", "-m", msg], cwd=info.path)
        if proc.returncode != 0:
            logger.error(f"Commit en worktree falló: {proc.stderr}")
            return False
        return True

    def diff(self, info: WorktreeInfo, max_chars: int = 8000) -> str:
        """Diff unificado del trabajo del agente respecto al punto de partida."""
        proc = self._git(["diff", f"{info.base_sha}..HEAD"], cwd=info.path, timeout=30)
        diff = proc.stdout or ""
        if len(diff) > max_chars:
            diff = diff[:max_chars] + f"\n\n[... diff truncado: {len(diff)} caracteres totales]"
        return diff

    def diff_stat(self, info: WorktreeInfo) -> str:
        proc = self._git(["diff", "--stat", f"{info.base_sha}..HEAD"], cwd=info.path, timeout=30)
        return proc.stdout or ""

    # ------------------------------------------------------------------
    def run_tests(self, info: WorktreeInfo) -> Tuple[bool, str]:
        """Ejecuta la suite pytest dentro del worktree con el venv del proyecto."""
        try:
            proc = subprocess.run(
                self.pytest_cmd, cwd=info.path,
                capture_output=True, text=True, timeout=180,
            )
            output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            ok = proc.returncode == 0
            logger.info(f"🧪 Tests en worktree [{info.task_id}]: {'✅ PASS' if ok else '❌ FAIL'}")
            return ok, output[-4000:]
        except subprocess.TimeoutExpired:
            return False, "pytest excedió el timeout de 180s."
        except Exception as e:
            return False, f"No se pudo ejecutar pytest en el worktree: {e}"

    # ------------------------------------------------------------------
    def merge_back(self, info: WorktreeInfo) -> Tuple[bool, str]:
        """Mergea la rama del agente en la rama actual del repo principal."""
        proc = self._git(["merge", "--no-edit", info.branch], timeout=60)
        if proc.returncode == 0:
            logger.info(f"🔀 Merge de {info.branch} completado.")
            return True, "Merge completado."
        detalle = (proc.stderr or proc.stdout or "").strip()
        logger.error(f"Merge de {info.branch} falló: {detalle}")
        self._git(["merge", "--abort"], timeout=30)
        return False, f"Conflicto de merge (abortado limpiamente): {detalle[:400]}"

    def discard(self, info: WorktreeInfo) -> None:
        """Elimina worktree y rama (con o sin cambios)."""
        self._git(["worktree", "remove", "--force", info.path], timeout=30)
        self._git(["branch", "-D", info.branch], timeout=30)
        self._active.pop(info.task_id, None)
        logger.info(f"🗑️ Worktree [{info.task_id}] descartado.")

    def finalize(self, info: WorktreeInfo) -> None:
        """Elimina el worktree conservando la rama ya mergeada; borra la rama."""
        self._git(["worktree", "remove", "--force", info.path], timeout=30)
        self._git(["branch", "-d", info.branch], timeout=30)
        self._active.pop(info.task_id, None)

    def list_active(self) -> List[WorktreeInfo]:
        return list(self._active.values())

    def cleanup_stale(self) -> int:
        """Recoge worktrees huérfanos de ejecuciones anteriores. Devuelve cuántos."""
        removed = 0
        proc = self._git(["worktree", "list", "--porcelain"], timeout=15)
        if proc.returncode != 0:
            return 0
        current_path = None
        for line in proc.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = line.split(" ", 1)[1].strip()
            elif line.startswith("detached") or line.startswith("branch"):
                if current_path and current_path.startswith(self.worktrees_dir + os.sep):
                    if not os.path.isdir(current_path) or current_path not in [i.path for i in self._active.values()]:
                        self._git(["worktree", "remove", "--force", current_path], timeout=30)
                        removed += 1
        if removed:
            logger.info(f"🧹 {removed} worktrees huérfanos limpiados.")
        return removed
