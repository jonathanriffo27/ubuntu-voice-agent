import os
import re
import shlex
import subprocess
from typing import Dict, Any, List, Optional
from src.security.approval import ApprovalManager
from src.security.sandbox import BubblewrapSandbox, command_needs_network
from src.security.credentials import get_broker
from src.utils.logging import get_logger

logger = get_logger("agents.tools")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


class AgentCodeTools:
    """
    Herramientas de inspección, desarrollo y pruebas para el subagente de programación.

    Dos modos de operación:
    - Modo directo (por defecto): raíz = proyecto; escrituras y comandos pasan por HITL.
    - Modo worktree (project_root=<worktree>, require_write_approval=False): las
      escrituras corren aisladas y se revisan UNA sola vez al final vía diff + pytest;
      los comandos shell SIGUEN requiriendo HITL (afectan al sistema real, no al sandbox).
    """

    def __init__(self, approval_manager: ApprovalManager, reload_callback=None,
                 project_root: Optional[str] = None, require_write_approval: bool = True,
                 sandbox: Optional[BubblewrapSandbox] = None):
        self.approval_manager = approval_manager
        self.reload_callback = reload_callback
        # La raíz puede ser un worktree aislado (Fase 3); por defecto el proyecto real
        self.project_root = os.path.abspath(project_root) if project_root else _PROJECT_ROOT
        self.require_write_approval = require_write_approval
        # El venv vive SIEMPRE fuera del worktree (compartido, en el repo principal)
        self._venv_bin = os.path.join(_PROJECT_ROOT, "venv", "bin")
        # Sandbox bwrap (Fase 6): se construye perezoso sobre la raíz activa,
        # con el entorno ya limpiado por el credential broker.
        self._sandbox = sandbox

    @property
    def sandbox(self) -> BubblewrapSandbox:
        if self._sandbox is None:
            # En modo worktree (Fase 3) la raíz activa es .worktrees/agent-<id>;
            # el repo principal se monta SOLO LECTURA debajo para que el venv
            # compartido (venv/bin/pytest) siga siendo accesible en el sandbox.
            extra_ro = []
            if self.project_root != _PROJECT_ROOT:
                extra_ro.append(_PROJECT_ROOT)
            self._sandbox = BubblewrapSandbox(
                self.project_root,
                env_builder=lambda: get_broker().scrub_env(),
                extra_ro_binds=extra_ro,
            )
        return self._sandbox

    def _resolve_project_path(self, ruta_relativa: str) -> Optional[str]:
        """
        Resuelve una ruta relativa confinándola estrictamente a la raíz activa
        (proyecto o worktree). Devuelve None si intenta escapar (path jail).
        """
        rel = (ruta_relativa or "").strip()
        if os.path.isabs(rel):
            return None  # Rutas absolutas prohibidas: solo rutas relativas a la raíz
        abs_path = os.path.realpath(os.path.join(self.project_root, rel.lstrip("/")))
        try:
            if os.path.commonpath([abs_path, self.project_root]) != self.project_root:
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
                ruta_raw = args.get("ruta_relativa", "")
                if os.path.isabs(ruta_raw):
                    return f"Error: Ruta absoluta '{ruta_raw}' no permitida; usa rutas relativas al proyecto."
                ruta_rel = ruta_raw.lstrip("/")
                contenido = args.get("contenido", "")
                ruta_abs = self._resolve_project_path(ruta_rel)
                if ruta_abs is None:
                    return f"Error: Ruta '{ruta_rel}' fuera del directorio del proyecto (bloqueado por seguridad)."

                if self.require_write_approval:
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
                else:
                    # Modo worktree: escritura aislada; la revisión humana ocurre
                    # UNA vez al final, sobre el diff completo antes del merge.
                    logger.info(f"✍️ [worktree] Escritura aislada sin HITL intermedio: {ruta_rel}")

                os.makedirs(os.path.dirname(ruta_abs), exist_ok=True)
                with open(ruta_abs, "w", encoding="utf-8") as f:
                    f.write(contenido)
                return f"Archivo '{ruta_rel}' escrito exitosamente."

            elif name == "ejecutar_pruebas_pytest":
                res = self.sandbox.run(
                    f'"{os.path.join(_PROJECT_ROOT, "venv", "bin", "pytest")}" -q',
                    network=False,
                    timeout=180,
                )
                output = res.stdout or res.stderr
                output = output[-4000:] if len(output) > 4000 else output
                return get_broker().redact(output)

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

                # Sandbox bwrap (Fase 6): red OFF por defecto; ON solo si el comando
                # aprobado lo requiere razonablemente (pip install, git clone/push...).
                network = (not is_safe_readonly) and command_needs_network(cmd)
                res = self.sandbox.run(cmd, network=network, timeout=60)
                output = res.stdout if res.returncode == 0 else f"Fallo (código {res.returncode}):\n{res.stderr}\n{res.stdout}"
                output = output if output.strip() else "Comando completado sin salida."
                return get_broker().redact(output)

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
