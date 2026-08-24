import asyncio
import json
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
"""


class DeveloperAgent:
    """
    Subagente de Razonamiento y Programación en Segundo Plano.
    Se comunica con CLIProxyAPI en el servidor Oracle (usando gemini-3.7-flash-high)
    para ejecutar tareas autónomas de código con compuerta de aprobación HITL.
    """

    def __init__(
        self,
        client: CLIProxyClient,
        code_tools: AgentCodeTools,
        event_bus: Optional[EventBus] = None,
        model: str = "gemini-3.7-flash-high",
        max_iterations: int = 15
    ):
        self.client = client
        self.tools = code_tools
        self.event_bus = event_bus or EventBus()
        self.model = model
        self.max_iterations = max_iterations
        self._active_tasks: Dict[str, asyncio.Task] = {}

    def start_background_task(self, instruction: str) -> str:
        """Inicia una tarea en segundo plano sin bloquear el frontend de voz de Atlas."""
        task_id = str(uuid.uuid4())[:8]
        task = asyncio.create_task(self.run_task(instruction, task_id=task_id))
        self._active_tasks[task_id] = task
        return task_id

    async def run_task(self, instruction: str, task_id: Optional[str] = None) -> str:
        """Bucle autónomo ReAct para resolver la instrucción."""
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

        messages = [
            {"role": "system", "content": DEVELOPER_SYSTEM_PROMPT},
            {"role": "user", "content": instruction}
        ]

        tool_defs = self.tools.get_tool_definitions()
        iteration = 0

        try:
            while iteration < self.max_iterations:
                iteration += 1
                logger.debug(f"Subagente [{task_id}] Iteración {iteration}/{self.max_iterations}")

                response = await self.client.chat_completion(
                    messages=messages,
                    model=self.model,
                    tools=tool_defs,
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
                    tool_output = await self.tools.execute_tool(fn_name, fn_args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", str(uuid.uuid4())),
                        "name": fn_name,
                        "content": str(tool_output)
                    })

            # Excedió límite de iteraciones
            timeout_msg = f"Se alcanzó el límite máximo de iteraciones ({self.max_iterations})."
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
            err_str = str(e)
            if "connection" in err_str.lower() or "connect" in err_str.lower() or "failed" in err_str.lower():
                err_msg = (
                    f"No se pudo conectar con CLIProxyAPI en {getattr(self.client, 'base_url', '127.0.0.1:8317')}. "
                    "Por favor inicia el servicio del túnel con: 'systemctl --user start cliproxy-tunnel'"
                )
            else:
                err_msg = f"Error: {err_str}"

            logger.error(f"Error en subagente desarrollador [{task_id}]: {err_msg}")
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
            self._active_tasks.pop(task_id, None)
