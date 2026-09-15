import os
import re
import shlex
import subprocess
from typing import Dict, Any, List, Optional
from src.security.approval import ApprovalManager
from src.utils.logging import get_logger

logger = get_logger("agents.tools")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


class AgentCodeTools:
    """
    Herramientas de inspección, desarrollo y pruebas para el subagente de programación.
    Toda modificación de archivo o ejecución de comando pasa por la compuerta HITL (ApprovalManager).
    """

    def __init__(self, approval_manager: ApprovalManager, reload_callback=None):
        self.approval_manager = approval_manager
        self.reload_callback = reload_callback
        self._venv_bin = os.path.join(_PROJECT_ROOT, "venv", "bin")

    @staticmethod
    def _resolve_project_path(ruta_relativa: str) -> Optional[str]:
        """
        Resuelve una ruta relativa confinándola estrictamente al directorio del
        proyecto (path jail). Devuelve None si intenta escapar (ej: ../../etc/passwd).
        """
        rel = (ruta_relativa or "").strip()
        if os.path.isabs(rel):
            return None  # Rutas absolutas prohibidas: solo rutas relativas al proyecto
        abs_path = os.path.realpath(os.path.join(_PROJECT_ROOT, rel.lstrip("/")))
        try:
            if os.path.commonpath([abs_path, _PROJECT_ROOT]) != _PROJECT_ROOT:
                return None
        except ValueError:
            return None  # Paths en unidades/raíces distintas
        return abs_path

    @staticmethod
    def _is_safe_readonly_command(cmd: str) -> bool:
        """
        Clasifica un comando como 'seguro de solo lectura' para auto-aprobarlo
        sin compuerta HITL. Rechaza cualquier encadenamiento, redirección,
        sustitución de comandos o binario fuera de la allowlist.
        """
        # Rechazar operadores de shell que permiten encadenar ejecución arbitraria
        for token in (";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r"):
            if token in cmd:
                return False
        try:
            parts = shlex.split(cmd)
        except ValueError:
            return False
        if not parts:
            return False

        binary = os.path.basename(parts[0])
        args = parts[1:]

        if binary in {"which", "whereis", "type", "echo", "uname", "pwd", "ls"}:
            return True
        if binary == "git":
            return bool(args) and args[0] in {"status", "diff", "log", "show", "branch"}
        if binary == "pip":
            return bool(args) and args[0] in {"list", "show"}
        if binary in {"python", "python3"}:
            return bool(args) and args[0] in {"--version", "-V"}
        return False

    def _rewrite_to_venv(self, cmd: str) -> str:
        """
        Red de seguridad: reescribe comandos pip/python desnudos al venv del proyecto.
        Evita el error PEP 668 'externally-managed-environment' de forma transparente.
        No toca comandos que ya usan rutas absolutas o ./venv/.
        """
        if "venv/" in cmd or "/bin/" in cmd:
            return cmd  # Ya apunta a un venv, no tocar

        # Patrones: 'pip install X', 'pip3 install X', 'python script.py', 'python3 -m X'
        rewrites = [
            (r"^pip3?\b", os.path.join(self._venv_bin, "pip")),
            (r"^python3?\b", os.path.join(self._venv_bin, "python")),
        ]
        original = cmd
        for pattern, replacement in rewrites:
            cmd = re.sub(pattern, replacement, cmd)

        if cmd != original:
            logger.info(f"Auto-rewrite venv: '{original}' → '{cmd}'")

        return cmd

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Devuelve las definiciones en formato JSON Schema de OpenAI."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "leer_archivo",
                    "description": "Lee el contenido de un archivo de texto en el proyecto.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ruta_relativa": {
                                "type": "string",
                                "description": "Ruta relativa del archivo desde la raíz del proyecto (ej: 'src/plugins/office/tools.py')."
                            }
                        },
                        "required": ["ruta_relativa"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "escribir_archivo",
                    "description": "Crea o sobrescribe un archivo en el proyecto. Requiere aprobación humana.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ruta_relativa": {
                                "type": "string",
                                "description": "Ruta relativa del archivo a crear o editar."
                            },
                            "contenido": {
                                "type": "string",
                                "description": "El contenido completo del archivo de código."
                            }
                        },
                        "required": ["ruta_relativa", "contenido"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "listar_directorio",
                    "description": "Lista los archivos y subdirectorios de una ruta.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ruta_relativa": {
                                "type": "string",
                                "description": "Ruta relativa del directorio (ej: 'src/plugins' o '.' para la raíz).",
                                "default": "."
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "ejecutar_comando_desarrollo",
                    "description": "Ejecuta un comando en la terminal (ej. pip install, git). Requiere aprobación humana.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "comando": {
                                "type": "string",
                                "description": "El comando shell a ejecutar."
                            }
                        },
                        "required": ["comando"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "ejecutar_pruebas_pytest",
                    "description": "Ejecuta la suite de pruebas automatizadas con pytest para validar que no haya errores.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "recargar_plugins_atlas",
                    "description": "Recarga en caliente todos los plugins de Atlas en memoria para que las nuevas herramientas queden activas inmediatamente.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "capturar_pantalla_desarrollo",
                    "description": "Toma una captura de pantalla ultrarrápida y optimizada (~60KB) del monitor para verificar visualmente si una tarea de interfaz/GUI funcionó, diagnosticar estados de ventanas o confirmar acciones. Es segura y de solo lectura.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "motivo": {
                                "type": "string",
                                "description": "Breve explicación de por qué tomas la captura (ej: 'verificar si WhatsApp abrió el chat', 'comprobar estado de ventana')."
                            }
                        }
                    }
                }
            }
        ]

    async def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Enruta y ejecuta la llamada a herramienta del subagente."""
        try:
            if name == "leer_archivo":
                ruta = self._resolve_project_path(args.get("ruta_relativa", ""))
                if ruta is None:
                    return f"Error: Ruta '{args.get('ruta_relativa')}' fuera del directorio del proyecto (bloqueado por seguridad)."
                if not os.path.exists(ruta):
                    return f"Error: El archivo '{args.get('ruta_relativa')}' no existe."
                with open(ruta, "r", encoding="utf-8") as f:
                    return f.read()

            elif name == "escribir_archivo":
                ruta_rel = args.get("ruta_relativa", "").lstrip("/")
                contenido = args.get("contenido", "")
                ruta_abs = self._resolve_project_path(ruta_rel)
                if ruta_abs is None:
                    return f"Error: Ruta '{ruta_rel}' fuera del directorio del proyecto (bloqueado por seguridad)."

                desc = f"Crear/modificar archivo: {ruta_rel} ({len(contenido)} caracteres)"
                # Solicitar aprobación humana (HITL) con 120s de margen
                approved = await self.approval_manager.request_approval(
                    action_type="file_write",
                    description=desc,
                    payload=contenido,
                    timeout=120.0
                )
                if not approved:
                    return f"Acción rechazada por el usuario: No se permitió escribir '{ruta_rel}'."

                os.makedirs(os.path.dirname(ruta_abs), exist_ok=True)
                with open(ruta_abs, "w", encoding="utf-8") as f:
                    f.write(contenido)
                return f"Archivo '{ruta_rel}' escrito exitosamente tras aprobación del usuario."

            elif name == "listar_directorio":
                ruta_rel = args.get("ruta_relativa", ".").lstrip("/")
                ruta_abs = self._resolve_project_path(ruta_rel)
                if ruta_abs is None:
                    return f"Error: Ruta '{ruta_rel}' fuera del directorio del proyecto (bloqueado por seguridad)."
                if not os.path.exists(ruta_abs):
                    return f"Error: La ruta '{ruta_rel}' no existe."
                entries = []
                for entry in sorted(os.listdir(ruta_abs)):
                    full = os.path.join(ruta_abs, entry)
                    tipo = "📁 [DIR]" if os.path.isdir(full) else "📄 [FILE]"
                    entries.append(f"{tipo} {entry}")
                return "\n".join(entries) if entries else "Directorio vacío."

            elif name == "ejecutar_comando_desarrollo":
                cmd = args.get("comando", "").strip()
                if not cmd:
                    return "Error: Comando vacío."

                # Auto-rewrite: redirigir pip/python desnudos al venv del proyecto
                cmd = self._rewrite_to_venv(cmd)

                # Comandos seguros de solo lectura (validación estricta por argv,
                # inmune a encadenamiento tipo "ls; rm -rf ~" o sustitución "$()").
                # Para leer archivos, el subagente debe usar 'leer_archivo' (con path jail).
                is_safe_readonly = self._is_safe_readonly_command(cmd)

                if not is_safe_readonly:
                    desc = "Ejecutar comando en terminal"
                    # Solicitar aprobación humana (HITL) con 120s de margen
                    approved = await self.approval_manager.request_approval(
                        action_type="shell_command",
                        description=desc,
                        payload=cmd,
                        timeout=120.0
                    )
                    if not approved:
                        return f"Comando rechazado por el usuario: '{cmd}' no fue ejecutado."

                res = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=_PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                output = res.stdout if res.returncode == 0 else f"Fallo (código {res.returncode}):\n{res.stderr}\n{res.stdout}"
                return output if output.strip() else "Comando completado sin salida."

            elif name == "ejecutar_pruebas_pytest":
                res = subprocess.run(
                    ["./venv/bin/pytest", "-v"],
                    cwd=_PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=45
                )
                return res.stdout if res.stdout else res.stderr

            elif name == "recargar_plugins_atlas":
                if self.reload_callback:
                    count = self.reload_callback()
                    return f"Plugins recargados exitosamente en caliente ({count} herramientas activas en memoria)."
                return "Plugins recargados."

            elif name == "capturar_pantalla_desarrollo":
                try:
                    from src.vision.service import OptimizedScreenCaptureService
                    service = OptimizedScreenCaptureService()
                    img_bytes = service.capture_screen(max_dim=1280, quality=70)
                    tmp_path = "/tmp/atlas_subagent_screen.jpg"
                    with open(tmp_path, "wb") as f:
                        f.write(img_bytes)
                    motivo = args.get("motivo", "").strip()
                    detalle = f" Motivo: '{motivo}'." if motivo else ""
                    return f"Captura optimizada tomada con éxito ({len(img_bytes)/1024:.1f} KB) y guardada en '{tmp_path}'.{detalle}"
                except Exception as e:
                    return f"Error capturando pantalla: {e}"

            else:
                return f"Herramienta desconocida: {name}"

        except Exception as e:
            logger.error(f"Error ejecutando herramienta de agente {name}: {e}")
            return f"Error de ejecución: {str(e)}"
