import asyncio
import json
import traceback
import uuid
from typing import Dict, Any, List, Optional
from src.agents.client import CLIProxyClient
from src.agents.tools import AgentCodeTools
from src.events.bus import EventBus
from src.events.base import ConversationContext, TaskDelegated, TaskCompleted, ErrorOccurred
from src.utils.logging import get_logger

logger = get_logger("agents.developer")

DEVELOPER_SYSTEM_PROMPT = """Eres el Agente Desarrollador y Autónomo de Atlas, un asistente de voz modular en Linux.
Tu misión es ejecutar tareas complejas de desarrollo, crear nuevos plugins, arreglar bugs y agregar funcionalidades al proyecto.

REGLAS DE DESARROLLO DE ATLAS:
1. Todos los plugins residen en 'src/plugins/<nombre_plugin>/'.
2. Cada plugin tiene:
   - '__init__.py': con función `setup(registry, dependencies)` para registrar herramientas.
   - 'tools.py': con clases que heredan de `BaseTool(ABC)` definiendo `name`, `description`, `parameters` (dict) y `async execute(context, **kwargs) -> ToolResult`.
3. Para escribir archivos o correr comandos shell, usa las herramientas correspondientes. El sistema solicitará automáticamente la aprobación del usuario antes de aplicarlos.
4. Siempre que crees o edites código, corre 'ejecutar_pruebas_pytest' para asegurar que la suite pase al 100%.
5. Cuando termines satisfactoriamente, llama a 'recargar_plugins_atlas' para que Atlas tenga las nuevas herramientas disponibles en caliente sin reiniciar.
6. Responde de forma clara y estructurada en español resumiendo lo que lograste.

REGLAS CRÍTICAS DE ENTORNO:
7. SIEMPRE usa el virtualenv del proyecto para instalar paquetes y ejecutar scripts:
   - Para instalar: './venv/bin/pip install <paquete>'
   - Para ejecutar: './venv/bin/python <script.py>'
   - NUNCA uses 'pip install' ni 'python' directamente (el sistema usa PEP 668 y fallará con 'externally-managed-environment').

REGLAS DE EJECUCIÓN COMPLETA (OBLIGATORIAS):
8. Completa las tareas de PRINCIPIO A FIN. El flujo esperado para una tarea es:
   a) Instalar dependencias necesarias con './venv/bin/pip install ...'
   b) Crear el script o plugin con 'escribir_archivo'
   c) EJECUTAR el script con 'ejecutar_comando_desarrollo' para que la acción se complete
   d) Reportar el resultado de la ejecución (éxito o error concreto)
9. NUNCA termines diciendo "preparé el entorno" o "puedes ejecutar el script manualmente".
   El usuario espera que HAGAS la tarea, no que le digas cómo hacerla.
10. Si la ejecución falla (ej: falta una variable de entorno, falta un token), reporta
    el ERROR EXACTO para que el usuario sepa qué debe configurar. Ejemplo:
    "Intenté enviar el mensaje pero falló: falta la variable TELEGRAM_API_ID.
     Configúrala con: export TELEGRAM_API_ID=tu_id"
11. Puedes verificar variables de entorno con: ejecutar_comando_desarrollo('echo $NOMBRE_VAR')

REGLAS DE DISEÑO PRAGMÁTICO (APP-FIRST Y NATIVO EN LINUX):
12. FILOSOFÍA CERO-FRICCIÓN (NO REINVENTAR LA RUEDA CON APIs COMPLEJAS):
    Antes de buscar librerías o APIs externas que requieran registro de claves (API IDs, hashes, tokens de desarrollador, bots, OAuth, client_secrets):
    - PRIORIZA SIEMPRE las aplicaciones, herramientas y mecanismos que el usuario YA TIENE instalados y funcionando en su sistema Linux:
      * Comandos CLI nativos ('playerctl', 'pamixer', 'pactl', 'nmcli', 'bluetoothctl', 'brightnessctl', 'xdg-open', etc.).
      * Interfaz D-Bus del sistema o de la app (ej: 'org.mpris.MediaPlayer2' para Spotify/VLC, 'org.freedesktop.Notifications', etc.).
      * Automatización de escritorio / GUI en Wayland: usar tecla Super (125) para enfocar la app, atajos de teclado con 'ydotool', 'wl-copy' para el portapapeles y schemes 'xdg-open'.
    - Si el usuario pide interactuar con una app (Telegram, Spotify, WhatsApp Web, Obsidian, navegador, reproductor, terminal), usa la app local antes de intentar crear clientes de API o scrapers.
    - Las soluciones deben funcionar DE INMEDIATO para el usuario sin pedirle configurar tokens ni API keys adicionales.
"""




class DeveloperAgent:
    """
    Subagente de Razonamiento y Programación en Segundo Plano.
    Se comunica con CLIProxyAPI en el servidor Oracle (usando gemini-3.7-flash-high)
    para ejecutar tareas autónomas de código con compuerta de aprobación HITL.

    Con `worktree_manager` (Fase 3), cada tarea trabaja en un git worktree aislado:
    las escrituras NO requieren HITL intermedio (son seguras: están en una rama
    aparte) y la integración se decide UNA vez al final: pytest en el worktree,
    diff mostrado al usuario y merge solo si aprueba.
    """

    def __init__(
        self,
        client: CLIProxyClient,
        code_tools: AgentCodeTools,
        event_bus: Optional[EventBus] = None,
        model: str = "gemini-3.7-flash-high",
        max_iterations: int = 15,
        worktree_manager=None,
    ):
        self.client = client
        self.tools = code_tools
        self.event_bus = event_bus or EventBus()
        self.model = model
        self.max_iterations = max_iterations
        self.worktree_manager = worktree_manager
        self._active_tasks: Dict[str, asyncio.Task] = {}

    def start_background_task(self, instruction: str) -> str:
        """Inicia una tarea en segundo plano sin bloquear el frontend de voz de Atlas."""
        task_id = str(uuid.uuid4())[:8]
        task = asyncio.create_task(self.run_task(instruction, task_id=task_id))
        self._active_tasks[task_id] = task
        return task_id

    def get_task_status(self, task_id: str = None) -> dict:
        """
        Devuelve el estado real de una tarea en segundo plano.
        Si no se pasa task_id, devuelve el estado de todas las tareas activas.
        """
        if task_id:
            task = self._active_tasks.get(task_id)
            if not task:
                return {"task_id": task_id, "status": "not_found", "detail": "No existe tarea activa con ese ID."}
            if task.done():
                try:
                    result = task.result()
                    return {"task_id": task_id, "status": "completed", "detail": str(result)[:500]}
                except Exception as e:
                    return {"task_id": task_id, "status": "error", "detail": str(e)[:500]}
            else:
                return {"task_id": task_id, "status": "running", "detail": "La tarea sigue ejecutándose."}

        # Sin task_id: devolver resumen de todas
        if not self._active_tasks:
            return {"status": "no_tasks", "detail": "No hay tareas activas en segundo plano."}

        result = {}
        for tid, task in self._active_tasks.items():
            if task.done():
                try:
                    res = task.result()
                    result[tid] = {"status": "completed", "detail": str(res)[:200]}
                except Exception as e:
                    result[tid] = {"status": "error", "detail": str(e)[:200]}
            else:
                result[tid] = {"status": "running"}
        return result

    def _prepare_task_tools(self, task_id: str):
        """
        Devuelve (tools, worktree_info). Si hay WorktreeManager disponible,
        la tarea corre aislada en su propia rama/worktree.
        """
        if not self.worktree_manager:
            return self.tools, None
        try:
            info = self.worktree_manager.create(task_id)
            tools = AgentCodeTools(
                approval_manager=self.tools.approval_manager,
                reload_callback=self.tools.reload_callback,
                project_root=info.path,
                require_write_approval=False,  # aislado: se revisa el diff al final
            )
            logger.info(f"🌿 Tarea [{task_id}] aislada en worktree: {info.path}")
            return tools, info
        except Exception as e:
            logger.warning(f"No se pudo crear worktree para [{task_id}] ({e}); modo directo con HITL.")
            return self.tools, None

    async def _finalize_worktree(self, info, task_id: str) -> str:
        """
        Cierre de una tarea con worktree: commit → pytest → diff → HITL → merge.
        Devuelve un apéndice textual para el resumen final de la tarea.
        """
        mgr = self.worktree_manager
        approval = self.tools.approval_manager

        if not mgr.has_changes(info):
            mgr.discard(info)
            return "La tarea no produjo cambios en el código."

        mgr.commit_all(info)

        ok, test_out = mgr.run_tests(info)
        if not ok:
            return (
                f"⚠️ Los tests FALLARON en el worktree; NO se integró nada. "
                f"El trabajo quedó en {info.path} (rama {info.branch}) para inspección manual. "
                f"Salida de pytest: {test_out[-800:]}"
            )

        diff_stat = mgr.diff_stat(info)
        diff = mgr.diff(info)
        approved = await approval.request_approval(
            action_type="git_merge",
            description=(
                f"Integrar trabajo del agente [{task_id}]: tests en verde. "
                f"Cambios: {'; '.join(diff_stat.splitlines()[-3:]) if diff_stat else 'sin stat'}"
            ),
            payload=f"{diff_stat}\n\n{diff}",
            timeout=300.0,
        )

        if not approved:
            return (
                f"El usuario rechazó la integración. El trabajo se conserva en {info.path} "
                f"(rama {info.branch}) por si quieres revisarlo luego."
            )

        ok, msg = mgr.merge_back(info)
        if not ok:
            return f"⚠️ {msg} La rama {info.branch} quedó conservada para resolución manual."

        reload_cb = self.tools.reload_callback
        recarga = ""
        if reload_cb:
            try:
                n = reload_cb()
                recarga = f" Plugins recargados en caliente ({n} herramientas)."
            except Exception as e:
                recarga = f" (la recarga en caliente falló: {e})"

        mgr.finalize(info)
        return f"✅ Trabajo integrado a la rama principal (tests en verde).{recarga}"

    async def run_task(self, instruction: str, task_id: Optional[str] = None) -> str:
        """Bucle autónomo ReAct para resolver la instrucción (aislado en worktree si es posible)."""
        task_id = task_id or str(uuid.uuid4())[:8]
        logger.info(f"🚀 [Subagente Desarrollador Iniciado] Tarea [{task_id}]: {instruction}")

        self.event_bus.publish(
            TaskDelegated(
                ConversationContext(),
                task_id=task_id,
                instruction=instruction,
                model=self.model
            )
        )

        tools, worktree_info = self._prepare_task_tools(task_id)

        messages = [
            {"role": "system", "content": DEVELOPER_SYSTEM_PROMPT},
            {"role": "user", "content": instruction}
        ]

        tool_defs = tools.get_tool_definitions()
        iteration = 0

        try:
            while iteration < self.max_iterations:
                iteration += 1
                logger.debug(f"Subagente [{task_id}] Iteración {iteration}/{self.max_iterations}")

                response = await self.client.chat_completion(
                    messages=messages,
                    model=self.model,
                    tools=tools.get_tool_definitions(),
                    temperature=0.2
                )

                choices = response.get("choices", [])
                if not choices:
                    raise RuntimeError("Respuesta vacía del modelo.")

                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content")
                tool_calls = message.get("tool_calls")
                reasoning = message.get("reasoning_content")

                if reasoning:
                    logger.debug(f"🧠 [Thinking Subagente]: {reasoning[:200]}...")

                messages.append(message)

                # Si el modelo no pide más herramientas, ha finalizado su tarea
                if not tool_calls:
                    final_result = content or "Tarea completada exitosamente."
                    if worktree_info is not None:
                        closure = await self._finalize_worktree(worktree_info, task_id)
                        final_result = f"{final_result}\n\n{closure}"
                        worktree_info = None  # ya gestionado
                    logger.info(f"✅ [Subagente Tarea Completada] [{task_id}]: {final_result[:100]}...")

                    self.event_bus.publish(
                        TaskCompleted(
                            ConversationContext(),
                            task_id=task_id,
                            success=True,
                            result=final_result
                        )
                    )
                    return final_result

                # Ejecutar herramientas solicitadas por el modelo
                for tc in tool_calls:
                    func = tc.get("function", {})
                    fn_name = func.get("name")
                    fn_args_raw = func.get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                    except Exception:
                        fn_args = {}

                    logger.info(f"🔧 [Subagente Tool Call]: {fn_name}({fn_args})")
                    tool_output = str(await tools.execute_tool(fn_name, fn_args))

                    # Truncar outputs gigantes: un 'cat' de un archivo grande o un
                    # pytest verboso puede inflar el contexto por iteración (costo/latencia)
                    MAX_TOOL_OUTPUT_CHARS = 8000
                    if len(tool_output) > MAX_TOOL_OUTPUT_CHARS:
                        tool_output = (
                            tool_output[:MAX_TOOL_OUTPUT_CHARS]
                            + f"\n\n[... salida truncada: {len(tool_output)} caracteres totales. "
                            "Usa comandos más específicos (grep, head, rangos) si necesitas más detalle.]"
                        )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", str(uuid.uuid4())),
                        "name": fn_name,
                        "content": tool_output
                    })

            # Excedió límite de iteraciones
            timeout_msg = f"Se alcanzó el límite máximo de iteraciones ({self.max_iterations})."
            if worktree_info is not None:
                # Conservar el trabajo parcial para inspección; no integrar.
                timeout_msg += f" El trabajo parcial quedó en {worktree_info.path} (rama {worktree_info.branch})."
                worktree_info = None
            self.event_bus.publish(
                TaskCompleted(
                    ConversationContext(),
                    task_id=task_id,
                    success=False,
                    result=timeout_msg
                )
            )
            return timeout_msg

        except Exception as e:
            err_type = type(e).__name__
            err_str = str(e) or repr(e)
            tb = traceback.format_exc()

            # Diagnóstico específico por tipo de error
            if "connection" in err_str.lower() or "connect" in err_str.lower():
                err_msg = (
                    f"No se pudo conectar con CLIProxyAPI en {getattr(self.client, 'base_url', '127.0.0.1:8317')}. "
                    "Por favor inicia el servicio del túnel con: 'systemctl --user start cliproxy-tunnel'"
                )
            elif "timeout" in err_str.lower() or "Timeout" in err_type:
                err_msg = (
                    f"Timeout al comunicarse con CLIProxyAPI ({err_type}). "
                    "El modelo puede estar tardando demasiado. Intenta simplificar la instrucción."
                )
            elif "HTTPStatusError" in err_type or "status" in err_str.lower():
                # Intentar extraer body de respuesta HTTP si existe
                response_text = ""
                if hasattr(e, "response"):
                    try:
                        response_text = f" | Response: {e.response.text[:500]}"
                    except Exception:
                        pass
                err_msg = f"Error HTTP de CLIProxy ({err_type}): {err_str}{response_text}"
            else:
                err_msg = f"Error ({err_type}): {err_str}"

            logger.error(f"Error en subagente desarrollador [{task_id}]: {err_msg}")
            logger.debug(f"Traceback completo subagente [{task_id}]:\n{tb}")
            self.event_bus.publish(
                TaskCompleted(
                    ConversationContext(),
                    task_id=task_id,
                    success=False,
                    result=err_msg
                )
            )
            return err_msg
        finally:
            # Limpieza defensiva: un worktree cuyo flujo terminó en excepción no
            # debe quedar reservado para siempre en _active (su rama/worktree físico
            # se conserva para inspección; cleanup_stale() lo recoge más tarde).
            self._active_tasks.pop(task_id, None)
