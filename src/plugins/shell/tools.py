import asyncio
import os
import time
import re
from abc import ABC, abstractmethod
from collections import deque
from typing import Deque, Dict, Any, List, Optional, Tuple

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


# Comandos destructivos del sistema: además de que el modelo los proponga, la
# última frase del usuario debe contener una confirmación EXPLÍCITA. La barrera
# no depende del juicio del modelo (una frase ambigua como "¿quieres irte a la
# casa?" no puede gatillar un `sudo shutdown`).
_DANGEROUS_PATTERNS = [
    r"\b(?:shutdown|reboot|poweroff|halt)\b",
    r"\bsystemctl\s+(?:poweroff|reboot|halt|suspend|hibernate)\b",
    r"\b(?:init|telinit)\s+[06]\b",
    r"\brm\s+-[a-z]*r[a-z]*f\b",
    r"\brm\s+-[a-z]*f[a-z]*r\b",
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\b[^\n]*of=/dev/",
]
# Afirmaciones inequívocas (sin "sí" pelado: puede responder a otra pregunta).
_EXPLICIT_CONFIRMATION_PATTERN = re.compile(
    r"\b(?:confirmo|confirmado|apruebo|aprobado|ejecuta|ejecútalo|ejecutalo|procede|adelante|dale|hazlo)\b",
    re.IGNORECASE,
)
CONFIRMATION_WINDOW_SECONDS = 60.0


def _is_dangerous_command(cmd: str) -> bool:
    return any(re.search(p, cmd, re.IGNORECASE) for p in _DANGEROUS_PATTERNS)


def _has_explicit_confirmation(text: str, ts: float) -> bool:
    """True si la última frase del usuario es reciente y confirma sin ambigüedad."""
    if not text or not ts:
        return False
    if time.time() - ts > CONFIRMATION_WINDOW_SECONDS:
        return False
    return bool(_EXPLICIT_CONFIRMATION_PATTERN.search(text))

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
    """
    Cola FIFO de comandos propuestos pendientes de confirmación por voz.

    Antes era un slot único: proponer un segundo comando SOBRESCRIBÍA el
    primero en silencio (incidente: 'ls' + 'cat ~/.ssh/id_rsa' -> solo se
    ejecutó el segundo tras un único 'apruebo'). Ahora cada propuesta se
    encola y 'ejecutar_comando_confirmado' drena la cola en orden.
    """

    def __init__(self, timeout_seconds: int = 30, max_pending: int = 10):
        self._queue: Deque[Tuple[str, float]] = deque()
        self.timeout_seconds = timeout_seconds
        self.max_pending = max_pending

    def _purge_expired(self):
        now = time.time()
        while self._queue and now - self._queue[0][1] > self.timeout_seconds:
            self._queue.popleft()

    def propose(self, command: str) -> int:
        """Encola un comando y devuelve el total de pendientes tras encolar."""
        self._purge_expired()
        if len(self._queue) >= self.max_pending:
            self._queue.popleft()  # descarta el más antiguo: la cola no crece sin límite
        self._queue.append((command, time.time()))
        return len(self._queue)

    def get_pending(self) -> Optional[str]:
        """Extrae el comando pendiente MÁS ANTIGUO (FIFO), o None si no hay."""
        self._purge_expired()
        if not self._queue:
            return None
        return self._queue.popleft()[0]

    def list_pending(self) -> List[str]:
        """Comandos pendientes en orden, sin extraerlos."""
        self._purge_expired()
        return [cmd for cmd, _ in self._queue]

    def clear(self):
        self._queue.clear()

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

        pendientes = self.state.propose(comando)
        extra = ""
        if pendientes > 1:
            extra = f" (encolado: hay {pendientes} comandos pendientes de confirmación)"
        print(f"\n⚠️ [ATLAS PROPONE]: {comando}{extra}\n (Esperando confirmación...)")

        if _is_dangerous_command(comando):
            espera = (
                "Espera a escuchar la confirmación EXPLÍCITA del usuario antes de ejecutarlo: para "
                "comandos destructivos debe decir 'confirmo', 'apruebo' o 'ejecuta' (un 'sí' a otra "
                "pregunta o una frase ambigua NO alcanza)."
            )
        else:
            espera = "Espera a escuchar la confirmación por voz del usuario antes de ejecutarlo."

        return ToolResult(
            success=True,
            content=f"Comando '{comando}' propuesto.{extra} DETENTE AQUÍ. {espera}"
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

        pendientes = self.state.list_pending()
        if not pendientes:
            print("\n❌ [ATLAS ERROR]: Se intentó confirmar sin comando pendiente o ya expiró.")
            return ToolResult(
                success=False,
                content="No hay un comando pendiente válido o ya expiró. Vuelve a proponerlo."
            )

        # Confirmación dura para comandos destructivos: valida la última frase
        # REAL del usuario (voz transcrita o texto de HUD/terminal), no lo que el
        # modelo crea haber entendido. Si no hay confirmación explícita, el
        # comando NO se extrae de la cola y sigue pendiente hasta su TTL.
        if _is_dangerous_command(pendientes[0]):
            ultima_frase = getattr(context, "last_user_utterance", "")
            ultimo_ts = getattr(context, "last_user_utterance_time", 0.0)
            if not _has_explicit_confirmation(ultima_frase, ultimo_ts):
                print(f"\n🛑 [CONFIRMACIÓN INSUFICIENTE]: {pendientes[0]}")
                return ToolResult(
                    success=False,
                    content=(
                        "NO ejecuté nada: no detecté una confirmación explícita del usuario para un comando "
                        "destructivo. Pídele que diga 'confirmo', 'apruebo' o 'ejecuta' (una frase ambigua o "
                        "un 'sí' a otra pregunta no cuenta) e inténtalo de nuevo tras su respuesta."
                    ),
                )

        cmd = self.state.get_pending()
        if not cmd:
            return ToolResult(
                success=False,
                content="No hay un comando pendiente válido o ya expiró. Vuelve a proponerlo."
            )

        print(f"\n🚀 [EJECUTANDO]: {cmd}")

        result = await self.executor.run(cmd)

        remaining = self.state.list_pending()
        cola_msg = ""
        if remaining:
            cola = ", ".join(f"'{c}'" for c in remaining)
            cola_msg = (
                f"\n\n⏳ Quedan {len(remaining)} comando(s) pendientes en cola: {cola}. "
                "CADA UNO requiere su propia confirmación del usuario: "
                "pregúntale si desea ejecutar el siguiente antes de llamar de nuevo a esta herramienta."
            )

        if result.exit_code == 0:
            out_text = f"Comando ejecutado correctamente. Salida:\n{result.stdout}"
            if not result.stdout.strip():
                out_text = "Comando ejecutado correctamente (sin salida)."
            print(f"✅ Completado en {result.duration_ms}ms")
            return ToolResult(
                success=True,
                content=out_text + cola_msg,
                metadata={"exit_code": result.exit_code, "duration_ms": result.duration_ms}
            )
        else:
            out_text = f"El comando falló con código {result.exit_code}.\nError:\n{result.stderr}\nSalida:\n{result.stdout}"
            print(f"❌ Falló en {result.duration_ms}ms")
            return ToolResult(
                success=False,
                content=out_text + cola_msg,
                metadata={"exit_code": result.exit_code, "duration_ms": result.duration_ms}
            )
