import asyncio
import os
import time
import re
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
_VENV_BIN = os.path.join(_PROJECT_ROOT, "venv", "bin")


def _rewrite_cmd_to_venv(cmd: str) -> str:
    """Auto-rewrite pip/python desnudos al venv del proyecto (red de seguridad PEP 668)."""
    if "venv/" in cmd or "/bin/" in cmd:
        return cmd
    rewrites = [
        (r"^pip3?\b", os.path.join(_VENV_BIN, "pip")),
        (r"^python3?\b", os.path.join(_VENV_BIN, "python")),
    ]
    for pattern, replacement in rewrites:
        cmd = re.sub(pattern, replacement, cmd)
    return cmd

class CommandResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, duration_ms: int):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms

class CommandExecutor(ABC):
    @abstractmethod
    async def run(self, command: str) -> CommandResult:
        pass

class BashExecutor(CommandExecutor):
    async def run(self, command: str) -> CommandResult:
        start = time.time()
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45.0)
            duration = int((time.time() - start) * 1000)
            return CommandResult(
                exit_code=process.returncode,
                stdout=stdout.decode('utf-8', errors='replace'),
                stderr=stderr.decode('utf-8', errors='replace'),
                duration_ms=duration
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            duration = int((time.time() - start) * 1000)
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr="El comando excedió el tiempo límite y fue cancelado.",
                duration_ms=duration
            )

class CommandState:
    def __init__(self, timeout_seconds: int = 30):
        self.pending_command: Optional[str] = None
        self.pending_timestamp: float = 0
        self.timeout_seconds = timeout_seconds

    def propose(self, command: str):
        self.pending_command = command
        self.pending_timestamp = time.time()

    def get_pending(self) -> Optional[str]:
        if not self.pending_command:
            return None
        if time.time() - self.pending_timestamp > self.timeout_seconds:
            self.pending_command = None
            return None
        return self.pending_command

    def clear(self):
        self.pending_command = None

class ProponerComandoTool(BaseTool):
    def __init__(self, state: CommandState):
        self.state = state

    @property
    def name(self) -> str:
        return "proponer_comando"

    @property
    def description(self) -> str:
        return "Propone un comando de terminal. SIEMPRE usa esta herramienta antes de ejecutar comandos destructivos o modificar el sistema."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "comando": {"type": "STRING", "description": "El comando bash exacto"}
            }, 
            "required": ["comando"]
        }

    async def execute(self, context: ToolContext, comando: str = None) -> ToolResult:
        if not comando:
            return ToolResult(success=False, content="Falta el comando.")

        # Auto-rewrite: redirigir pip/python desnudos al venv del proyecto
        comando = _rewrite_cmd_to_venv(comando)
            
        config = context.config.tools.shell
        if not config.enabled:
            return ToolResult(success=False, content="La herramienta de shell está deshabilitada por configuración.")
            
        # Defensa en profundidad, la barrera real es la confirmación humana
        blocked_patterns = [
            r"rm\s+-r[fF]?\s+(?:/|~|\$)",
            r"dd\s+.*of=/dev/",
            r"mkfs\.",
            r"(?:curl|wget)\s+.*\|\s*(?:bash|sh)"
        ]
        if any(re.search(pat, comando) for pat in blocked_patterns):
            return ToolResult(success=False, content=f"El comando '{comando}' está bloqueado por seguridad (defensa en profundidad).")

        if config.confirmation == "never":
            # Si no requiere confirmación, podríamos ejecutarlo directo aquí, 
            # pero para mantener el flujo, lo proponemos y el sistema podría auto-confirmarlo.
            # En un entorno sin confirmación, esto se comportaría distinto.
            pass

        self.state.propose(comando)
        print(f"\n⚠️ [ATLAS PROPONE]: {comando}\n (Esperando confirmación...)")
        
        return ToolResult(
            success=True, 
            content=f"Comando '{comando}' propuesto. DETENTE AQUÍ. Espera a escuchar la confirmación por voz del usuario antes de ejecutarlo."
        )

class EjecutarComandoTool(BaseTool):
    def __init__(self, executor: CommandExecutor, state: CommandState):
        self.executor = executor
        self.state = state

    @property
    def name(self) -> str:
        return "ejecutar_comando_confirmado"

    @property
    def description(self) -> str:
        return "Ejecuta el último comando de terminal propuesto. Úsalo SOLO DESPUÉS de que el usuario lo haya confirmado."

    @property
    def parameters(self) -> Dict[str, Any]:
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        config = context.config.tools.shell
        if not config.enabled:
            return ToolResult(success=False, content="La herramienta de shell está deshabilitada.")

        cmd = self.state.get_pending()
        if not cmd:
            print("\n❌ [ATLAS ERROR]: Se intentó confirmar sin comando pendiente o ya expiró.")
            return ToolResult(
                success=False, 
                content="No hay un comando pendiente válido o ya expiró. Vuelve a proponerlo."
            )

        self.state.clear()
        print(f"\n🚀 [EJECUTANDO]: {cmd}")
        
        result = await self.executor.run(cmd)
        
        if result.exit_code == 0:
            out_text = f"Comando ejecutado correctamente. Salida:\n{result.stdout}"
            if not result.stdout.strip():
                out_text = "Comando ejecutado correctamente (sin salida)."
            print(f"✅ Completado en {result.duration_ms}ms")
            return ToolResult(
                success=True, 
                content=out_text,
                metadata={"exit_code": result.exit_code, "duration_ms": result.duration_ms}
            )
        else:
            out_text = f"El comando falló con código {result.exit_code}.\nError:\n{result.stderr}\nSalida:\n{result.stdout}"
            print(f"❌ Falló en {result.duration_ms}ms")
            return ToolResult(
                success=False, 
                content=out_text,
                metadata={"exit_code": result.exit_code, "duration_ms": result.duration_ms}
            )
