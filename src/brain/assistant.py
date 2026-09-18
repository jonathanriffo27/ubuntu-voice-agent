import asyncio
import json
import os
import sys
import time
import traceback
import contextlib

from typing import List, Optional
import pyaudio

from src.voice.constants import AUDIO_FORMAT, AUDIO_CHANNELS, AUDIO_IN_RATE, AUDIO_OUT_RATE, CHUNK_SIZE
from src.voice.recorder import AudioRecorder
from src.voice.player import AudioPlayer, play_sound
from src.providers.base import (
    BaseProvider, ProviderSession, AudioChunk, TextChunk, UserTextChunk,
    ToolCallRequest, Interrupted, TurnComplete, ToolResponseItem
)
from src.brain.prompts import build_system_prompt
from src.tools.registry import ToolRegistry
from src.tools.base import ToolContext
from src.knowledge.manager import KnowledgeManager
from src.events.base import (
    ConversationContext, SessionStarted, SessionEnded, ToolStarted,
    ToolSucceeded, ToolFailed, ResponseGenerated, AssistantTextChunk,
    ErrorOccurred, TaskCompleted, TurnCompleted, UserInterrupted,
    SessionReconnected, SpeechRecognized, SystemNotification, ReminderTriggered
)
from src.events.bus import EventBus
from src.ui.terminal_input import TerminalInteractionManager
from src.utils.logging import get_logger
from src.security.monitor import ActionMonitor, monitor_action_name
from src.security.credentials import get_broker

logger = get_logger("brain.assistant")


class Assistant:
    def __init__(
        self,
        provider: BaseProvider,
        registry: ToolRegistry,
        config=None,
        knowledge_manager: KnowledgeManager = None,
        event_bus: EventBus = None,
        mcp_manager=None,
        overlay_server=None,
        reminder_scheduler=None,
        approval_manager=None,
        trajectory_manager=None,
        action_monitor: "ActionMonitor" = None,
        credential_broker=None,
        max_reconnect_attempts: Optional[int] = None,
        reconnect_initial_backoff: float = 1.0,
        reconnect_max_backoff: float = 30.0
    ):
        self.provider = provider
        self.registry = registry
        self.config = config
        self.knowledge_manager = knowledge_manager
        self.event_bus = event_bus or EventBus()
        self.conversation_context = ConversationContext()
        self.mcp_manager = mcp_manager
        self.overlay_server = overlay_server
        self.reminder_scheduler = reminder_scheduler
        self.approval_manager = approval_manager
        self.trajectory_manager = trajectory_manager
        # Fase 6: monitor de anomalías en la secuencia de acciones y broker de
        # credenciales para redactar secretos antes de que vuelvan al contexto.
        self.action_monitor = action_monitor or ActionMonitor()
        self._credential_broker = credential_broker or get_broker()

        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_initial_backoff = reconnect_initial_backoff
        self.reconnect_max_backoff = reconnect_max_backoff

        # Deduplicación de tool calls DENTRO de un mismo turno (el modelo a veces
        # repite la misma llamada). Se resetea en cada TurnComplete.
        self._duplicate_guard: Dict[str, int] = {}

        self.p = None
        self.in_stream = None
        self.out_stream = None
        self.recorder: Optional[AudioRecorder] = None
        self.player: Optional[AudioPlayer] = None
        self._active_session: Optional[ProviderSession] = None

    async def send_realtime(self, session: ProviderSession, audio_queue_input: asyncio.Queue):
        """Consume chunks de audio del micrófono y los envía al proveedor en tiempo real."""
        while True:
            try:
                data = await audio_queue_input.get()
                if self.recorder and self.recorder.processing_tool:
                    continue
                if data == "END_OF_TURN":
                    await session.end_turn()
                    continue
                await session.send_audio(data, AUDIO_IN_RATE)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en send_realtime: {e}")
                raise

    def _is_duplicate_call(self, name: str, args: dict) -> bool:
        """
        True si esta MISMA llamada (nombre + args) ya se ejecutó en el turno actual.
        La guardia se limpia en cada TurnComplete, así que llamadas idénticas en
        turnos distintos son legítimas y se ejecutan con normalidad.
        """
        call_key = f"{name}:{args}"
        count = self._duplicate_guard.get(call_key, 0)
        self._duplicate_guard[call_key] = count + 1
        return count > 0

    async def receive_and_route(
        self,
        session: ProviderSession,
        audio_queue_output: asyncio.Queue,
        audio_queue_input: asyncio.Queue
    ):
        """Recibe eventos normalizados del modelo y enruta audio, texto y ejecución de herramientas."""
        printed_prefix = False
        current_response_text: List[str] = []
        # Última transcripción del usuario publicada en ESTE turno. Gemini Live
        # a veces re-emite el mismo texto final (duplicado exacto); se filtra
        # para no imprimir "🎙️ Tú (voz): …" dos veces. Se reinicia por turno,
        # de modo que repetir la misma pregunta en turnos distintos sí se muestra.
        last_user_text: Optional[str] = None
        try:
            while True:
                async for event in session.receive():
                    if isinstance(event, Interrupted):
                        current_response_text.clear()
                        last_user_text = None
                        if self.player:
                            self.player.stop_and_clear(audio_queue_output)
                        if self.recorder:
                            self.recorder.waiting_for_model = False
                        self.event_bus.publish(UserInterrupted(self.conversation_context))
                        printed_prefix = False

                    elif isinstance(event, AudioChunk):
                        audio_queue_output.put_nowait(event.data)

                    elif isinstance(event, UserTextChunk):
                        text_clean = event.text.strip()
                        if text_clean and text_clean != last_user_text:
                            last_user_text = text_clean
                            self.event_bus.publish(SpeechRecognized(self.conversation_context, text=event.text))

                    elif isinstance(event, TextChunk):
                        if not printed_prefix:
                            printed_prefix = True
                        current_response_text.append(event.text)
                        self.event_bus.publish(AssistantTextChunk(self.conversation_context, text=event.text))

                    elif isinstance(event, TurnComplete):
                        full_text = "".join(current_response_text).strip()
                        if full_text:
                            self.event_bus.publish(ResponseGenerated(self.conversation_context, text=full_text))
                        current_response_text.clear()
                        last_user_text = None
                        self.event_bus.publish(TurnCompleted(self.conversation_context))
                        # Reset de la deduplicación: la misma tool con los mismos args
                        # en un turno FUTURO es una petición legítima, no un duplicado.
                        self._duplicate_guard.clear()
                        if self.recorder:
                            self.recorder.waiting_for_model = False
                        printed_prefix = False


                    elif isinstance(event, ToolCallRequest):
                        if self.recorder:
                            self.recorder.processing_tool = True
                        responses: List[ToolResponseItem] = []

                        for fc in event.calls:
                            if self._is_duplicate_call(fc.name, fc.args) and fc.name != "obtener_estado_sistema":
                                responses.append(
                                    ToolResponseItem(
                                        name=fc.name,
                                        id=fc.id,
                                        response={"status": "success", "message": "Ignorado por duplicado en este turno."}
                                    )
                                )
                                continue

                            if fc.name != "obtener_estado_sistema":
                                self.event_bus.publish(
                                    ToolStarted(self.conversation_context, tool_name=fc.name, arguments=fc.args)
                                )

                            tool = self.registry.get_tool(fc.name)
                            if tool:
                                try:
                                    # Fase 6 — Monitor de anomalías: pausa + HITL si la
                                    # secuencia de acciones se sale de política.
                                    try:
                                        args_repr = json.dumps(fc.args, ensure_ascii=False, default=str)[:400]
                                    except Exception:
                                        args_repr = str(fc.args)[:400]
                                    # Clasificar con la sub-acción si existe (navegador_web.elementos,
                                    # interactuar_gui.leer...): las tools clasifican así internamente;
                                    # si el monitor ve solo el nombre pelado, lecturas inocuas cuentan
                                    # como "risky" y la ráfaga dispara falsos positivos.
                                    alert = self.action_monitor.record(
                                        monitor_action_name(fc.name, fc.args), args_repr
                                    )
                                    if alert:
                                        resumed = False
                                        if self.approval_manager:
                                            resumed = await self.approval_manager.request_approval(
                                                action_type="anomaly_pause",
                                                description=(
                                                    f"🚨 Monitor de anomalías [{alert.rule}]: {alert.reason} "
                                                    f"Acción detenida: {fc.name}. ¿Reanudar operación normal?"
                                                ),
                                                payload=alert.evidence,
                                                timeout=120.0,
                                            )
                                        if resumed:
                                            self.action_monitor.reset()
                                            logger.warning(f"🚨 Anomalía resuelta por el usuario ({alert.rule}); reanudando.")
                                        else:
                                            msg = (f"Pausado por el monitor de anomalías ({alert.rule}): {alert.reason} "
                                                   "El usuario debe confirmar para continuar.")
                                            logger.warning(f"🚨 {msg}")
                                            self.event_bus.publish(
                                                ToolFailed(self.conversation_context, tool_name=fc.name, error=msg)
                                            )
                                            responses.append(ToolResponseItem(name=fc.name, id=fc.id,
                                                                              response={"error": msg}))
                                            continue

                                    context = ToolContext(
                                        config=self.config,
                                        event_bus=self.event_bus,
                                        conversation_context=self.conversation_context
                                    )
                                    tool_result = await tool.execute(context, **fc.args)

                                    # Fase 6 — Credential broker: redactar secretos del
                                    # output antes de devolverlo al contexto del LLM.
                                    if tool_result.content:
                                        tool_result.content = self._credential_broker.redact(tool_result.content)

                                    res_dict = {"result": tool_result.content}

                                    if tool_result.metadata:
                                        if "inline_data" in tool_result.metadata:
                                            inline = tool_result.metadata["inline_data"]
                                            try:
                                                import base64
                                                data_bytes = base64.b64decode(inline["data"])
                                                await session.send_video(data_bytes, inline["mime_type"])
                                                res_dict["status"] = "Imagen adjuntada exitosamente al flujo de video."
                                            except Exception as ve:
                                                res_dict["status"] = f"Error inyectando imagen: {ve}"
                                        else:
                                            res_dict.update(tool_result.metadata)

                                    if fc.name != "obtener_estado_sistema":
                                        self.event_bus.publish(
                                            ToolSucceeded(self.conversation_context, tool_name=fc.name, result=res_dict)
                                        )

                                    responses.append(ToolResponseItem(name=fc.name, id=fc.id, response=res_dict))
                                except Exception as e:
                                    self.event_bus.publish(
                                        ToolFailed(self.conversation_context, tool_name=fc.name, error=str(e))
                                    )
                                    responses.append(ToolResponseItem(name=fc.name, id=fc.id, response={"error": str(e)}))
                            else:
                                self.event_bus.publish(
                                    ErrorOccurred(
                                        self.conversation_context,
                                        error=f"Tool no encontrada: {fc.name}",
                                        source="Assistant"
                                    )
                                )

                        if responses:
                            drained = 0
                            while not audio_queue_input.empty():
                                try:
                                    audio_queue_input.get_nowait()
                                    drained += 1
                                except asyncio.QueueEmpty:
                                    break
                            if drained > 0:
                                logger.debug(f"🗑️ [DRAINED] {drained} mensajes descartados")

                            try:
                                await session.send_tool_response(responses)
                            except Exception as e:
                                logger.error(f"Error enviando respuesta de tool: {e}")

                        if self.recorder:
                            self.recorder.processing_tool = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            err_msg = str(e)
            if "1008" not in err_msg and "1011" not in err_msg and "1006" not in err_msg and "abnormal closure" not in err_msg.lower() and "aborted" not in err_msg.lower() and "connection" not in err_msg.lower() and "internal error" not in err_msg.lower():
                self.event_bus.publish(
                    ErrorOccurred(self.conversation_context, error=err_msg, source="Assistant: receive_and_route")
                )
            logger.debug(f"receive_and_route finalizado: {e}")
            raise
        finally:
            if self.player:
                self.player.is_speaking = False
            if self.recorder:
                self.recorder.processing_tool = False

    async def send_text_message(self, text: str) -> None:
        """Permite enviar mensajes de texto al modelo (desde el HUD web o API)."""
        if self._active_session:
            logger.info(f"💬 [HUD -> Atlas]: {text}")
            if self.recorder:
                self.recorder.waiting_for_model = True
            await self._active_session.send_text(text, end_of_turn=True)

    def toggle_microphone_pause(self) -> None:
        """Pausa o reanuda el micrófono."""
        if self.recorder:
            self.recorder.is_paused = not self.recorder.is_paused
            state = "PAUSADO ⏸️" if self.recorder.is_paused else "REANUDADO ▶️"
            logger.info(f"Estado micrófono alternado: {state}")

    async def run_async(self):
        @contextlib.contextmanager
        def suppress_stderr():
            devnull = os.open(os.devnull, os.O_WRONLY)
            old_stderr = os.dup(2)
            sys.stderr.flush()
            os.dup2(devnull, 2)
            try:
                yield
            finally:
                os.dup2(old_stderr, 2)
                os.close(devnull)
                os.close(old_stderr)

        # Iniciar servidores MCP si están configurados
        if self.mcp_manager and self.config and getattr(self.config, 'mcp_servers', None):
            try:
                await self.mcp_manager.load_servers(self.config.mcp_servers, self.registry)
            except Exception as e:
                logger.error(f"Error cargando servidores MCP: {e}")

        # Iniciar motor de recordatorios
        if self.reminder_scheduler:
            self.reminder_scheduler.start()

        # Iniciar servidor de Overlay HUD si está habilitado
        if self.overlay_server and self.config:
            self.overlay_server.on_user_input = self.send_text_message
            self.overlay_server.on_toggle_pause = self.toggle_microphone_pause
            ui_cfg = getattr(self.config, 'ui', None)
            auto_open = getattr(ui_cfg, 'auto_open_browser', False) if ui_cfg else False
            try:
                await self.overlay_server.start(auto_open=auto_open)
            except Exception as e:
                logger.error(f"Error iniciando Web Overlay HUD: {e}")

        # Asegurar túnel SSH a CLIProxy en segundo plano (0ms bloqueo en arranque)
        if self.config and getattr(self.config, 'developer_agent', None) and self.config.developer_agent.enabled:
            from src.agents.tunnel import ensure_cliproxy_tunnel
            asyncio.create_task(ensure_cliproxy_tunnel())

        with suppress_stderr():
            self.p = pyaudio.PyAudio()

        logger.info("Cargando modelo de wake word...")
        with suppress_stderr():
            self.in_stream = self.p.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_IN_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )
            self.out_stream = self.p.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_OUT_RATE,
                output=True,
                frames_per_buffer=1024
            )

        voice_cfg = self.config.voice if self.config else None
        self.recorder = AudioRecorder(
            self.in_stream,
            self.event_bus,
            self.conversation_context,
            voice_config=voice_cfg
        )
        # Inicializar modelo de wake word y calibración de micrófono en paralelo
        await asyncio.gather(
            self.recorder.load_wake_word(),
            self.recorder.calibrate()
        )

        self.player = AudioPlayer(self.out_stream)

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        # 1. Suscribir callback de subagente de desarrollo una sola vez
        def _on_task_completed(event: TaskCompleted):
            if self._active_session:
                estado = "completó exitosamente" if event.success else "falló"
                resumen = event.result[:800] if event.result else "(sin detalle)"
                msg = (
                    f"[SISTEMA: El subagente de desarrollo (ID: {event.task_id}) {estado}. "
                    f"Resultado: {resumen}]"
                )
                asyncio.create_task(self._active_session.send_text(msg, end_of_turn=False))

        self.event_bus.subscribe(TaskCompleted, _on_task_completed)

        # 1.1. Suscribir notificaciones generales del sistema (ej: documentos listos)
        def _on_system_notification(event: SystemNotification):
            if self._active_session:
                msg = f"[SISTEMA: {event.message}]"
                asyncio.create_task(self._active_session.send_text(msg, end_of_turn=False))

        self.event_bus.subscribe(SystemNotification, _on_system_notification)

        # 1.2. Suscribir disparo de recordatorios para notificación por voz
        def _on_reminder_triggered(event: ReminderTriggered):
            if self._active_session:
                msg = f"[SISTEMA: El recordatorio programado '{event.message}' acaba de sonar. Notifícaselo brevemente al usuario.]"
                asyncio.create_task(self._active_session.send_text(msg, end_of_turn=False))

        self.event_bus.subscribe(ReminderTriggered, _on_reminder_triggered)

        # 2. Suscribir follow-up de wake word al completar un turno
        def _on_turn_completed(event: TurnCompleted):
            if self.recorder:
                self.recorder.enter_follow_up()

        self.event_bus.subscribe(TurnCompleted, _on_turn_completed)

        # 3. Iniciar gestores de hardware e interacción continua en el ámbito exterior continuo
        terminal_manager = TerminalInteractionManager(
            assistant=self,
            recorder=self.recorder,
            approval_manager=self.approval_manager,
            audio_queue_input=q_in
        )
        terminal_task = asyncio.create_task(terminal_manager.listen())
        recorder_task = asyncio.create_task(self.recorder.listen(q_in, q_out, self.player))
        player_task = asyncio.create_task(self.player.play(q_out))

        attempt = 0
        backoff = self.reconnect_initial_backoff
        is_first_connection = True

        try:
            # None = reintentos ilimitados: un asistente de voz no debe morir
            # permanentemente por una racha de fallos de conexión.
            while self.max_reconnect_attempts is None or attempt < self.max_reconnect_attempts:
                attempt += 1
                session_start_time = time.time()
                if is_first_connection:
                    logger.info(f"Conectando al proveedor (intento {attempt}/{self.max_reconnect_attempts})...")
                else:
                    logger.debug(f"Reconectando al proveedor (intento {attempt}/{self.max_reconnect_attempts})...")

                sys_prompt, _ = build_system_prompt(self.knowledge_manager, trajectory_manager=self.trajectory_manager)

                try:
                    async with self.provider.connect(
                        system_prompt=sys_prompt,
                        tools=self.registry.get_all_tools()
                    ) as session:
                        self._active_session = session

                        if is_first_connection:
                            self.event_bus.publish(SessionStarted(self.conversation_context))
                            play_sound("ready")
                            await session.send_text(
                                "[DIRECTIVA INICIAL: Di únicamente una frase corta confirmando que estás en línea y listo (ejemplo: 'Sistema Atlas en línea y listo'). Prohibido ejecutar herramientas o búsquedas.]",
                                end_of_turn=True
                            )
                            is_first_connection = False
                            logger.info("Conexión establecida con el proveedor.")
                        else:
                            # Reconexión silenciosa y transparente
                            self.event_bus.publish(SessionReconnected(self.conversation_context, attempt=attempt))
                            logger.info("Conexión restablecida silenciosamente con el proveedor.")

                        # Reiniciar backoff tras conexión exitosa
                        backoff = self.reconnect_initial_backoff

                        # Resetear banderas de control para la nueva sesión
                        if self.recorder:
                            self.recorder.waiting_for_model = False
                            self.recorder.processing_tool = False
                        if self.player:
                            self.player.is_speaking = False

                        # Limpiar colas de audio previas
                        while not q_in.empty():
                            try:
                                q_in.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        while not q_out.empty():
                            try:
                                q_out.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                        try:
                            async with asyncio.TaskGroup() as tg:
                                tg.create_task(self.send_realtime(session, q_in))
                                tg.create_task(self.receive_and_route(session, q_out, q_in))
                        except ExceptionGroup as eg:
                            is_idle_timeout = False
                            for exc in eg.exceptions:
                                if isinstance(exc, asyncio.CancelledError):
                                    return
                                err_str = str(exc).lower()
                                # "go_away" = el servidor avisa que cerrará la
                                # sesión (TTL ~15 min): reconexión transparente.
                                if "1008" in err_str or "1011" in err_str or "1006" in err_str or "abnormal closure" in err_str or "aborted" in err_str or "closed" in err_str or "internal error" in err_str or "go_away" in err_str:
                                    is_idle_timeout = True
                                else:
                                    logger.error(f"Fallo en tarea concurrente: {exc}")

                            if not is_idle_timeout and any(not isinstance(exc, asyncio.CancelledError) for exc in eg.exceptions):
                                raise eg
                except (KeyboardInterrupt, asyncio.CancelledError):
                    logger.info("Sesión finalizada por el usuario.")
                    break
                except Exception as e:
                    session_duration = time.time() - session_start_time
                    err_str = str(e).lower()
                    is_idle_timeout = ("1008" in err_str or "1011" in err_str or "1006" in err_str
                                       or "abnormal closure" in err_str or "aborted" in err_str
                                       or "closed" in err_str or "internal error" in err_str
                                       or "go_away" in err_str)

                    # Si la sesión estuvo viva y saludable por más de 15 segundos (ej. idle timeout),
                    # reiniciar el contador para no morir por inactividad prolongada natural.
                    if session_duration > 15.0:
                        attempt = 1

                    # Los handles de session resumption CADUCAN en el servidor. Si una
                    # reconexión con handle falló (o la sesión apenas vivió), el próximo
                    # intento debe abrir sesión limpia — reutilizarlo en bucle mataba a
                    # Atlas con 5 fallos rápidos tras ~15 min de inactividad.
                    if attempt > 1 and hasattr(self.provider, "reset_session_handle"):
                        self.provider.reset_session_handle()

                    if is_idle_timeout:
                        logger.info("Sesión con el proveedor reiniciada (corte o error transitorio). Reconectando...")
                        reconnect_wait = 0.5
                    else:
                        logger.warning(f"Conexión con el proveedor interrumpida (intento {attempt}): {e}")
                        reconnect_wait = backoff
                        backoff = min(backoff * 2.0, self.reconnect_max_backoff)

                    if (self.max_reconnect_attempts is not None
                            and attempt >= self.max_reconnect_attempts):
                        logger.critical(f"Se excedió el número máximo de reconexiones ({self.max_reconnect_attempts}).")
                        break

                    await asyncio.sleep(reconnect_wait)
                finally:
                    self._active_session = None

        finally:
            terminal_task.cancel()
            recorder_task.cancel()
            player_task.cancel()
            await self.cleanup_async()

    async def cleanup_async(self):
        """Limpieza asíncrona de recursos de audio, HUD, recordatorios y MCP."""
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            old_stderr = os.dup(2)
            sys.stderr.flush()
            os.dup2(devnull, 2)
            try:
                if self.in_stream and self.in_stream.is_active():
                    self.in_stream.stop_stream()
                if self.out_stream and self.out_stream.is_active():
                    self.out_stream.stop_stream()
                if self.in_stream:
                    self.in_stream.close()
                if self.out_stream:
                    self.out_stream.close()
                if self.p:
                    self.p.terminate()
            finally:
                os.dup2(old_stderr, 2)
                os.close(devnull)
                os.close(old_stderr)
        except Exception:
            pass

        if self.reminder_scheduler:
            try:
                self.reminder_scheduler.stop()
            except Exception:
                pass

        if self.overlay_server:
            try:
                await self.overlay_server.stop()
            except Exception:
                pass

        if self.mcp_manager:
            try:
                await self.mcp_manager.shutdown_all()
            except Exception:
                pass

    def run(self):
        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            self.event_bus.publish(SessionEnded(self.conversation_context))
            os._exit(0)
