"""
Sandbox de ejecución para comandos shell del subagente (Fase 6, COMPUTER_USE_PLAN.md).

Usa bubblewrap (bwrap) para confinar los comandos del DeveloperAgent:
- RedOFF por defecto (--unshare-net); solo se activa para comandos que la
  necesitan (pip install, git fetch/push, curl...) y que ya pasaron por HITL.
- Sistema de archivos mínimo: /usr, /etc, /lib* en solo lectura; el proyecto
  (o worktree) es lo único escribible. ~/.ssh, ~/.gnupg, ~/.aws y el resto del
  HOME del usuario NO existen dentro del sandbox (HOME=/tmp, tmpfs).
- Entorno limpio (--clearenv): ninguna API key llega al proceso hijo.
- --die-with-parent: si Atlas muere, el comando muere con él.

Si bwrap no está instalado, degrada a ejecución directa PERO con entorno
limpio (lo esencial —no filtrar secretos— se mantiene) y lo advierte en log.
"""
import os
import shlex
import shutil
import subprocess
from typing import Dict, List, Optional

from src.utils.logging import get_logger

logger = get_logger("security.sandbox")

# Binarios/patrones que razonablemente necesitan red (pasaron HITL antes).
_NET_BINARIES = {"curl", "wget", "apt", "apt-get", "npm", "npx", "pnpm", "yarn", "docker"}
_NET_PATTERNS = (
    ("pip", ("install", "download", "wheel")),
    ("git", ("clone", "fetch", "pull", "push", "remote", "submodule")),
)

# PATH mínimo dentro del sandbox (el venv se invoca por ruta absoluta).
_SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"


def command_needs_network(cmd: str) -> bool:
    """Heurística conservadora: ¿este comando aprobado necesita acceso a red?"""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    binary = os.path.basename(parts[0])
    if binary in _NET_BINARIES:
        return True
    sub = parts[1] if len(parts) > 1 else ""
    for tool, subcmds in _NET_PATTERNS:
        if binary == tool and sub in subcmds:
            return True
    # pip/git invocados por ruta absoluta o con sufijo de versión (pip3, /venv/bin/pip)
    for tool, subcmds in _NET_PATTERNS:
        if binary in (tool, tool + "3") and sub in subcmds:
            return True
    return False


class BubblewrapSandbox:
    """Constructs y ejecuta comandos confinados con bwrap."""

    def __init__(self, project_root: str, env_builder=None,
                 extra_ro_binds: Optional[List[str]] = None):
        self.project_root = os.path.abspath(project_root)
        self.extra_ro_binds = [os.path.abspath(p) for p in (extra_ro_binds or [])]
        self._bwrap = shutil.which("bwrap")
        # env_builder: callable() -> dict (CredentialBroker.scrub_env inyectable)
        self._env_builder = env_builder or (lambda: {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        })
        if not self._bwrap:
            logger.warning(
                "bwrap no encontrado: los comandos del subagente correrán SIN sandbox "
                "(solo con entorno limpio). Instala bubblewrap para confinamiento completo."
            )

    @property
    def available(self) -> bool:
        return self._bwrap is not None

    # ------------------------------------------------------------------
    def build_argv(self, cmd: str, network: bool = False,
                   env: Optional[Dict[str, str]] = None) -> List[str]:
        """Construye la línea bwrap completa (testeable sin ejecutar nada)."""
        argv = [self._bwrap or "bwrap"]

        # Sistema base de solo lectura (respetando symlinks estilo /bin -> usr/bin)
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt"):
            if os.path.islink(path):
                argv += ["--symlink", os.readlink(path), path]
            elif os.path.isdir(path):
                argv += ["--ro-bind", path, path]
        if os.path.isdir("/etc"):
            argv += ["--ro-bind", "/etc", "/etc"]  # resolv.conf/hosts si hay red

        argv += [
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
        ]
        # Binds extra de SOLO LECTURA ANTES del bind rw del proyecto: si el
        # sandbox corre sobre un worktree (Fase 3), el repo principal se monta
        # ro primero para que el venv compartido (venv/bin/...) sea accesible,
        # y el worktree rw se monta DESPUÉS encima (bwrap aplica en orden).
        for path in self.extra_ro_binds:
            argv += ["--ro-bind", path, path]
        argv += [
            # Único punto escribible: el proyecto (o el worktree de la tarea)
            "--bind", self.project_root, self.project_root,
        ]

        # Entorno: vacío por defecto + solo las variables limpias aprobadas
        argv.append("--clearenv")
        for key, value in (env or {}).items():
            argv += ["--setenv", key, value]
        # Overrides de seguridad: HOME aislado en tmpfs (inaccesibles ~/.ssh,
        # ~/.aws, ~/.gnupg del usuario). Van AL FINAL para ganar a cualquier env.
        argv += [
            "--setenv", "HOME", "/tmp",
            "--setenv", "TERM", "dumb",
        ]
        if not network:
            argv.append("--unshare-net")
        argv += [
            "--unshare-pid",
            "--die-with-parent",
            "--new-session",
            "--chdir", self.project_root,
            "--", "sh", "-c", cmd,
        ]
        return argv

    # ------------------------------------------------------------------
    def run(self, cmd: str, network: bool = False, timeout: float = 60.0) -> subprocess.CompletedProcess:
        """Ejecuta el comando confinado (o degradado) y devuelve CompletedProcess."""
        env = self._env_builder()
        if self.available:
            argv = self.build_argv(cmd, network=network, env=env)
            logger.info(f"⛓️ [sandbox] bwrap red={'ON' if network else 'OFF'}: {cmd[:120]}")
            # bwrap recibe un env mínimo propio: con --clearenv no lo propaga al
            # hijo, pero aun así no le damos secretos al proceso wrapper.
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                  env={"PATH": _SANDBOX_PATH})
        # Degradado: sin aislamiento de fs/red, pero jamás con secretos en el env
        logger.warning(f"⚠️ [sandbox DEGRADADO, sin bwrap] {cmd[:120]}")
        return subprocess.run(cmd, shell=True, cwd=self.project_root,
                              capture_output=True, text=True, timeout=timeout, env=env)
