import asyncio
import os
import sys
import traceback
import contextlib
from typing import List, Optional
import pyaudio

from src.voice.constants import AUDIO_FORMAT, AUDIO_CHANNELS, AUDIO_IN_RATE, AUDIO_OUT_RATE, CHUNK_SIZE
from src.voice.recorder import AudioRecorder
from src.voice.player import AudioPlayer, play_sound
from src.providers.base import (
    BaseProvider, ProviderSession, AudioChunk, TextChunk,
    ToolCallRequest, Interrupted, TurnComplete, ToolResponseItem
)
from src.brain.prompts import build_system_prompt
from src.tools.registry import ToolRegistry
from src.tools.base import ToolContext
from src.knowledge.manager import KnowledgeManager
from src.events.base import (
    ConversationContext, SessionStarted, SessionEnded, ToolStarted,
    ToolSucceeded, ToolFailed, ResponseGenerated, ErrorOccurred
)
from src.events.bus import EventBus
from src.ui.terminal_input import TerminalInteractionManager
from src.utils.logging import get_logger

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
        max_reconnect_attempts: int = 5,
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

        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_initial_backoff = reconnect_initial_backoff
        self.reconnect_max_backoff = reconnect_max_backoff

        self._last_tool_call = {"name": None, "args": None, "count": 0}

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

    async def receive_and_route(
        self,
        session: ProviderSession,
        audio_queue_output: asyncio.Queue,
        audio_queue_input: asyncio.Queue
    ):
        """Recibe eventos normalizados del modelo y enruta audio, texto y ejecución de herramientas."""
        printed_prefix = False
        try:
            while True:
                async for event in session.receive():
                    if isinstance(event, Interrupted):
                        if self.player:
                            self.player.is_speaking = False
                        printed_prefix = False
                        while not audio_queue_output.empty():
                            try:
                                audio_queue_output.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                    elif isinstance(event, AudioChunk):
                        if self.player:
                            self.player.is_speaking = True
                            audio_queue_output.put_nowait(event.data)

                    elif isinstance(event, TextChunk):
                        if not printed_prefix:
                            printed_prefix = True
                        self.event_bus.publish(ResponseGenerated(self.conversation_context, text=event.text))

                    elif isinstance(event, TurnComplete):
                        if self.player:
                            self.player.is_speaking = False
                        if printed_prefix:
                            sys.stdout.write("\n")
                            printed_prefix = False

                    elif isinstance(event, ToolCallRequest):
                        if self.recorder:
                            self.recorder.processing_tool = True
                        responses: List[ToolResponseItem] = []

                        for fc in event.calls:
                            call_key = f"{fc.name}:{fc.args}"

                            if self._last_tool_call["name"] == call_key:
                                self._last_tool_call["count"] += 1
                            else:
                                self._last_tool_call["name"] = call_key
                                self._last_tool_call["args"] = fc.args
                                self._last_tool_call["count"] = 1

                            if self._last_tool_call["count"] > 1 and fc.name != "obtener_estado_sistema":
                                responses.append(
                                    ToolResponseItem(
                                        name=fc.name,
                                        id=fc.id,
                                        response={"status": "success", "message": "Ignorado por duplicado."}
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
                                    context = ToolContext(
                                        config=self.config,
                                        event_bus=self.event_bus,
                                        conversation_context=self.conversation_context
                                    )
                                    tool_result = await tool.execute(context, **fc.args)

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
            if "1008" not in err_msg and "aborted" not in err_msg.lower() and "connection" not in err_msg.lower():
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

        with suppress_stderr():
            self.p = pyaudio.PyAudio()

        logger.info("Cargando modelo de wake word (Hey Atlas)...")
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
                frames_per_buffer=CHUNK_SIZE
            )

        self.recorder = AudioRecorder(self.in_stream, self.event_bus, self.conversation_context)
        self.recorder.load_wake_word()
        await self.recorder.calibrate()

        self.player = AudioPlayer(self.out_stream)

        sys_prompt, _ = build_system_prompt(self.knowledge_manager, trajectory_manager=self.trajectory_manager)

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        attempt = 0
        backoff = self.reconnect_initial_backoff

        try:
            while attempt < self.max_reconnect_attempts:
                attempt += 1
                logger.info(f"Conectando al proveedor (intento {attempt}/{self.max_reconnect_attempts})...")

                try:
                    async with self.provider.connect(
                        system_prompt=sys_prompt,
                        tools=self.registry.get_all_tools()
                    ) as session:
                        self._active_session = session
                        self.event_bus.publish(SessionStarted(self.conversation_context))
                        play_sound("ready")
                        await session.send_text(
                            "[DIRECTIVA INICIAL: Di únicamente una frase corta confirmando que estás en línea y listo (ejemplo: 'Sistema Atlas en línea y listo'). Prohibido ejecutar herramientas o búsquedas.]",
                            end_of_turn=True
                        )

                        # Reiniciar backoff tras conexión exitosa
                        backoff = self.reconnect_initial_backoff
                        logger.info("Conexión establecida con el proveedor.")

                        terminal_manager = TerminalInteractionManager(
                            assistant=self,
                            recorder=self.recorder,
                            approval_manager=self.approval_manager,
                            audio_queue_input=q_in
                        )

                        try:
                            async with asyncio.TaskGroup() as tg:
                                tg.create_task(self.recorder.listen(q_in, q_out, self.player))
                                tg.create_task(self.send_realtime(session, q_in))
                                tg.create_task(self.player.play(q_out))
                                tg.create_task(self.receive_and_route(session, q_out, q_in))
                                tg.create_task(terminal_manager.listen())
                        except ExceptionGroup as eg:
                            for exc in eg.exceptions:
                                if isinstance(exc, asyncio.CancelledError):
                                    return
                                logger.error(f"Fallo en tarea concurrente: {exc}")
                                raise exc
                except (KeyboardInterrupt, asyncio.CancelledError):
                    logger.info("Sesión finalizada por el usuario.")
                    break
                except Exception as e:
                    logger.warning(f"Conexión con el proveedor interrumpida: {e}")
                    if attempt >= self.max_reconnect_attempts:
                        logger.critical(f"Se excedió el número máximo de reconexiones ({self.max_reconnect_attempts}).")
                        break
                    logger.info(f"Reintentando conexión en {backoff:.1f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, self.reconnect_max_backoff)
                finally:
                    self._active_session = None

        finally:
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
