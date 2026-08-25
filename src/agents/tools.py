import os
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
            }
        ]

    async def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Enruta y ejecuta la llamada a herramienta del subagente."""
        try:
            if name == "leer_archivo":
                ruta = os.path.join(_PROJECT_ROOT, args.get("ruta_relativa", "").lstrip("/"))
                if not os.path.exists(ruta):
                    return f"Error: El archivo '{args.get('ruta_relativa')}' no existe."
                with open(ruta, "r", encoding="utf-8") as f:
                    return f.read()

            elif name == "escribir_archivo":
                ruta_rel = args.get("ruta_relativa", "").lstrip("/")
                contenido = args.get("contenido", "")
                ruta_abs = os.path.join(_PROJECT_ROOT, ruta_rel)

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
                ruta_abs = os.path.join(_PROJECT_ROOT, ruta_rel)
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

                # Comandos seguros de solo lectura (diagnóstico y exploración de entorno)
                SAFE_READONLY_PREFIXES = (
                    "which ", "whereis ", "type ", "echo ", "uname", "pwd", "ls", "find ", "grep ",
                    "cat ", "head ", "tail ", "python --version", "python3 --version", "git status",
                    "git diff", "git log", "pip list", "env | grep", "env|grep"
                )
                is_safe_readonly = any(cmd.startswith(p) for p in SAFE_READONLY_PREFIXES)

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

            else:
                return f"Herramienta desconocida: {name}"

        except Exception as e:
            logger.error(f"Error ejecutando herramienta de agente {name}: {e}")
            return f"Error de ejecución: {str(e)}"
