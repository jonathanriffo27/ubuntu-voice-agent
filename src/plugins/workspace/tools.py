"""
Herramientas de SOLO LECTURA sobre el propio código de Atlas (self-knowledge).

Contexto: el agente de voz respondía "no puedo realizar operaciones de archivo
en mi propio directorio de instalación" porque no tenía NINGUNA tool de lectura
segura — leer_archivo/listar_directorio viven solo en AgentCodeTools, que
pertenece al subagente desarrollador. Estas tools tapan ese hueco con lectura
estrictamente confinada a la raíz del proyecto:

- Rutas resueltas y validadas con is_relative_to(project_root) contra escapes
  por "../" o rutas absolutas.
- Archivos sensibles siempre denegados (.env, claves, credenciales) y binarios
  rechazados: defensa en profundidad, ya que además el CredentialBroker redacta
  secretos de TODO output de tools antes de devolverlo al modelo.
- Nada de escritura: modificar código sigue siendo tarea exclusiva del
  subagente ('delegar_tarea_desarrollo'), que opera con worktrees + HITL.
"""
from pathlib import Path
from typing import Any, Dict, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult

# Raíz del proyecto: src/plugins/workspace/tools.py → subir 3 niveles.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Directorios ruidosos o irrelevantes para describir la estructura del proyecto.
_EXCLUDED_DIRS = {
    ".git", "venv", "__pycache__", ".pytest_cache", ".worktrees",
    "node_modules", ".mypy_cache",
}
# Jamás legibles por voz: secretos o material criptográfico.
_DENIED_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_MAX_LIST_ENTRIES = 250
_MAX_READ_CHARS = 8000
_BINARY_SNIFF_BYTES = 2048


def _resolve_confined(subpath: Optional[str]) -> Optional[Path]:
    """Resuelve una ruta relativa confinada al proyecto; None si intenta escapar."""
    if not subpath or subpath in (".", "/"):
        return PROJECT_ROOT
    target = (PROJECT_ROOT / subpath).resolve()
    if not target.is_relative_to(PROJECT_ROOT):
        return None
    return target


def _is_denied_file(path: Path) -> bool:
    return path.name == ".env" or path.suffix.lower() in _DENIED_SUFFIXES


class ListarArchivosProyectoTool(BaseTool):
    """Lista el árbol del proyecto Atlas (1 nivel por llamada), solo lectura."""

    @property
    def name(self) -> str:
        return "listar_archivos_proyecto"

    @property
    def description(self) -> str:
        return (
            "Lista los archivos y carpetas de tu PROPIO proyecto Atlas (tu código fuente), "
            "en modo solo lectura. Úsala cuando el usuario pregunte dónde estás instalado, "
            "qué archivos componen tu sistema o cómo estás construido. Acepta 'subruta' "
            "opcional relativa a la raíz (ej: 'src/voice'); vacía = raíz del proyecto."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "subruta": {
                    "type": "STRING",
                    "description": "Subdirectorio relativo a listar (ej: 'src/voice'). Vacío = raíz.",
                }
            },
        }

    async def execute(self, context: ToolContext, subruta: str = None, **kwargs) -> ToolResult:
        target = _resolve_confined(subruta)
        if target is None:
            return ToolResult(success=False, content=f"Ruta '{subruta}' fuera del proyecto Atlas: no permitido.")
        if not target.is_dir():
            return ToolResult(success=False, content=f"'{subruta or ''}' no existe o no es un directorio del proyecto.")

        dirs, files = [], []
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir():
                if entry.name not in _EXCLUDED_DIRS:
                    dirs.append(entry.name + "/")
            else:
                files.append(entry.name)

        total = len(dirs) + len(files)
        if total == 0:
            return ToolResult(success=True, content=f"El directorio '{subruta or 'raíz del proyecto'}' está vacío.")

        shown = (dirs + files)[:_MAX_LIST_ENTRIES]
        header = f"Contenido de '{subruta or 'raíz del proyecto Atlas'}' ({len(dirs)} carpetas, {len(files)} archivos):"
        body = "\n".join(f"- {name}" for name in shown)
        if total > _MAX_LIST_ENTRIES:
            body += f"\n(... {total - _MAX_LIST_ENTRIES} entradas más omitidas; usa 'subruta' para acotar)"
        return ToolResult(success=True, content=f"{header}\n{body}")


class LeerArchivoProyectoTool(BaseTool):
    """Lee el contenido de texto de un archivo del proyecto, solo lectura."""

    @property
    def name(self) -> str:
        return "leer_archivo_proyecto"

    @property
    def description(self) -> str:
        return (
            "Lee el contenido (solo lectura) de un archivo de tu propio proyecto Atlas, "
            "ej: 'config.yaml', 'src/voice/recorder.py'. Úsala para responder preguntas "
            "sobre cómo está implementada alguna parte de tu sistema. No permite escritura, "
            "ni archivos fuera del proyecto, ni archivos con secretos."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "ruta": {
                    "type": "STRING",
                    "description": "Ruta del archivo relativa a la raíz del proyecto (ej: 'config.yaml').",
                }
            },
            "required": ["ruta"],
        }

    async def execute(self, context: ToolContext, ruta: str = None, **kwargs) -> ToolResult:
        if not ruta:
            return ToolResult(success=False, content="Falta el parámetro 'ruta'.")

        target = _resolve_confined(ruta)
        if target is None:
            return ToolResult(success=False, content=f"Ruta '{ruta}' fuera del proyecto Atlas: no permitido.")
        if _is_denied_file(target):
            return ToolResult(success=False, content=f"'{ruta}' contiene secretos/credenciales: lectura denegada.")
        if not target.is_file():
            return ToolResult(success=False, content=f"'{ruta}' no existe o no es un archivo del proyecto.")

        try:
            with open(target, "rb") as fh:
                sniff = fh.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in sniff:
                return ToolResult(success=False, content=f"'{ruta}' parece un archivo binario: no se puede leer por voz.")
            text = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(success=False, content=f"No pude leer '{ruta}': {e}")

        header = f"Contenido de '{ruta}':"
        if len(text) > _MAX_READ_CHARS:
            text = text[:_MAX_READ_CHARS] + f"\n(... archivo truncado tras {_MAX_READ_CHARS} caracteres)"
        return ToolResult(success=True, content=f"{header}\n{text}")
