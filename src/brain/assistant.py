import asyncio
import json
import os
import signal
import sys
import time
import traceback
import contextlib

from typing import Callable, List, Optional
import pyaudio

from src.voice.constants import AUDIO_FORMAT, AUDIO_CHANNELS, AUDIO_IN_RATE, AUDIO_OUT_RATE, CHUNK_SIZE
from src.voice.recorder import AudioRecorder
from src.voice.player import AudioPlayer, play_sound
from src.providers.base import (
    BaseProvider, ProviderSession, AudioChunk, TextChunk, UserTextChunk,
    ToolCallRequest, Interrupted, TurnComplete, ToolResponseItem,
    ToolCallsCancelled
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
        reconnect_max_backoff: float = 30.0,
        provider_factory: Optional[Callable[[str], BaseProvider]] = None
    ):
        self.provider = provider
        # Fallback en caliente: si el primario queda no disponible (1011/503 del
        # backend de Live), se cambia a `provider.fallback_model` solo en runtime
        # (la config nunca se sobreescribe). `provider_factory` construye el
        # proveedor de respaldo con la misma voz/flags.
        self._provider_factory = provider_factory
        self._primary_provider = provider
        self._active_provider = provider
        self._fallback_provider = None
        self._fallback_active = False
        self._unavailable_streak = 0
        self._provider_switch_requested = False
        self._probe_task: Optional[asyncio.Task] = None
        # True mientras la sesión Live está suspendida por pausa prolongada del
        # micrófono: el loop de sesión espera en vez de reconectar.
        self._session_suspended = False
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
        # Marca temporal del último mensaje de TEXTO del usuario (HUD/terminal).
        # Sirve para el barge-in local por teclado y para validar interrupciones
        # del servidor que realmente provienen de una entrada del usuario.
        self._last_user_text_input: float = 0.0
        # Referencia a la cola de reproducción (asignada en run_async) para que
        # send_text_message pueda cortar el audio viejo al instante.
        self._audio_out_queue: Optional[asyncio.Queue] = None

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
        # Diagnóstico de voz: contar los PCM que llegan del proveedor por turno
        # permite distinguir "el modelo no envía audio" de "el audio se borra
        # antes de reproducirse" (el texto SIEMPRE se ve porque llega por la
        # transcripción, aunque el audio nunca llegue o sea vaciado por un
        # interrupted del VAD del servidor).
        audio_chunks_turn = 0
        audio_bytes_turn = 0
        audio_chunks_session = 0
        try:
            while True:
                async for event in session.receive():
                    if isinstance(event, Interrupted):
                        # Filtro anti falso-positivo (gemini-3.8, VAD agresivo):
                        # un barge-in REAL implica que el micrófono local captó
                        # voz del usuario en el último ~1.5s. Si no hay voz local
                        # reciente, el servidor reaccionó a ruido ambiente (o a
                        # estados transitorios) y vaciar la cola dejaba a Atlas
                        # MUDO — el texto se veía igual porque llega por la
                        # transcripción, mientras todo el audio se descartaba.
                        last_voice = getattr(self.recorder, "last_local_voice_time", 0.0) if self.recorder else 0.0
                        # Un `interrupted` también es legítimo si acaba de llegar
                        # texto del usuario por HUD/terminal (barge-in por teclado).
                        recent_voice = (time.time() - last_voice) <= 1.5
                        recent_typing = (time.time() - self._last_user_text_input) <= 1.5
                        if not recent_voice and not recent_typing:
                            logger.warning(
                                "⚡ Interrupción del servidor SIN voz local reciente → ignorada "
                                "(falso positivo del VAD); el audio en cola sigue reproduciéndose."
                            )
                            # No se vacía la cola ni el texto; solo se reabre el
                            # micrófono (el gate anti-ruido del recorder evita que
                            # el ambiente vuelva a disparar interrupciones).
                            if self.recorder:
                                self.recorder.waiting_for_model = False
                            continue
                        logger.warning(
                            f"⚡ INTERRUPTED confirmado (voz local o texto reciente = barge-in real): "
                            f"vaciando cola ({audio_chunks_turn} chunks / {audio_bytes_turn} bytes)."
                        )
                        audio_chunks_turn = 0
                        audio_bytes_turn = 0
                        current_response_text.clear()
                        last_user_text = None
                        if self.player:
                            self.player.stop_and_clear(audio_queue_output)
                        if self.recorder:
                            self.recorder.waiting_for_model = False
                            # Barge-in genuino: anula tanto el turno en curso como un
                            # cierre pendiente (el usuario cambió de idea a mitad de
                            # la despedida).
                            self.recorder.turn_in_flight = False
                            self.recorder.pending_sleep = False
                        self.event_bus.publish(UserInterrupted(self.conversation_context))
                        printed_prefix = False

                    elif isinstance(event, ToolCallsCancelled):
                        # Live API (docs oficiales): en un barge-in el servidor cancela
                        # los tool calls en vuelo (LiveServerToolCallCancellation) y su
                        # respuesta queda invalidada — NO hay que enviarla. Liberamos el
                        # gate del micrófono: la rama ToolCallRequest lo activa, pero si
                        # la cancelación llega después, nadie lo limpiaba y el sistema
                        # podía quedarse mudo esperando un cierre que nunca llega.
                        logger.info(f"Tool calls canceladas por el servidor (barge-in): {event.ids}")
                        if self.recorder:
                            self.recorder.processing_tool = False
                            self.recorder.turn_in_flight = False
                            self.recorder.pending_sleep = False

                    elif isinstance(event, AudioChunk):
                        audio_queue_output.put_nowait(event.data)
                        # Marca de turno vivo: impide que la máquina de estados de
                        # voz caiga a STANDBY en las pausas entre segmentos de audio
                        # (con server_vad no hay otro indicador de "respondiendo").
                        if self.recorder:
                            self.recorder.turn_in_flight = True
                        audio_chunks_turn += 1
                        audio_bytes_turn += len(event.data)
                        audio_chunks_session += 1
                        if audio_chunks_session == 1:
                            logger.info(
                                "🔊 Primer chunk PCM del proveedor recibido: la ruta de voz "
                                "(API → cliente) está viva."
                            )

                    elif isinstance(event, UserTextChunk):
                        text_clean = event.text.strip()
                        if text_clean and text_clean != last_user_text:
                            last_user_text = text_clean
                            self.event_bus.publish(SpeechRecognized(self.conversation_context, text=event.text))

                    elif isinstance(event, TextChunk):
                        if not printed_prefix:
                            printed_prefix = True
                        if self.recorder:
                            self.recorder.turn_in_flight = True
                        current_response_text.append(event.text)
                        self.event_bus.publish(AssistantTextChunk(self.conversation_context, text=event.text))

                    elif isinstance(event, TurnComplete):
                        full_text = "".join(current_response_text).strip()
                        if full_text:
                            self.event_bus.publish(ResponseGenerated(self.conversation_context, text=full_text))
                        current_response_text.clear()
                        last_user_text = None
                        self.event_bus.publish(TurnCompleted(self.conversation_context))
                        logger.info(
                            f"🔊 Fin de turno: audio del proveedor = {audio_chunks_turn} chunks / "
                            f"{audio_bytes_turn} bytes (~{audio_bytes_turn / 48000:.1f}s a 24kHz)"
                        )
                        audio_chunks_turn = 0
                        audio_bytes_turn = 0
                        # Reset de la deduplicación: la misma tool con los mismos args
                        # en un turno FUTURO es una petición legítima, no un duplicado.
                        self._duplicate_guard.clear()
                        if self.recorder:
                            self.recorder.waiting_for_model = False
                            self.recorder.turn_in_flight = False
                        printed_prefix = False


                    elif isinstance(event, ToolCallRequest):
                        if self.recorder:
                            self.recorder.processing_tool = True
                            self.recorder.turn_in_flight = True
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

                                    # Workaround documentado contra el "parametric
                                    # fallback" de modelos Live (ignoran tool results
                                    # y responden de memoria): para la búsqueda web,
                                    # entregar los datos junto a una INSTRUCCIÓN de
                                    # override explícita dentro del FunctionResponse.
                                    if fc.name == "buscar_en_internet" and tool_result.success:
                                        res_dict = {
                                            "datos_obtenidos_ahora_mismo_de_internet": tool_result.content,
                                            "instruccion_critica": (
                                                "Estos datos son la realidad ACTUAL obtenida en vivo. "
                                                "Responde ÚNICAMENTE con ellos. Si tu memoria de "
                                                "entrenamiento los contradice, tu memoria está "
                                                "desactualizada: IGNÓRALA por completo."
                                            ),
                                        }

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

                                    # Cierre de conversación aceptado por el modelo
                                    # ('entrar_en_espera'): dormir al TurnComplete del
                                    # turno de despedida, sin ventana follow-up.
                                    if fc.name == "entrar_en_espera" and self.recorder:
                                        self.recorder.request_sleep_after_turn()

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
            self._last_user_text_input = time.time()
            # Barge-in local por teclado: si Atlas seguía hablando de la respuesta
            # anterior, se corta al instante. Sin esto, el audio nuevo se encolaba
            # DETRÁS de la cola vieja y el retraso percibido se acumulaba turno a
            # turno (5s, 10s...: el usuario tecleaba la siguiente pregunta mientras
            # aún sonaba la cola de la anterior).
            if self.player and self._audio_out_queue is not None:
                self.player.stop_and_clear(self._audio_out_queue)
            if self.recorder:
                self.recorder.waiting_for_model = True
                # Nuevo mensaje por texto = el usuario sigue aquí: anula un cierre pendiente.
                self.recorder.pending_sleep = False
            await self._active_session.send_text(text, end_of_turn=True)
        else:
            # Sin sesión (reconectando o recién cambiado al fallback): el mensaje
            # se pierde. Antes se ignoraba en silencio y parecía que Atlas no
            # respondía a propósito.
            logger.warning(f"💬 Mensaje de texto descartado (sin sesión activa): {text[:120]}")
            self.event_bus.publish(SystemNotification(
                self.conversation_context,
                message="Mensaje no enviado: Atlas está reconectando con el proveedor. Reintenta en unos segundos.",
            ))

    def toggle_microphone_pause(self) -> None:
        """Pausa o reanuda el micrófono."""
        if self.recorder:
            self.recorder.is_paused = not self.recorder.is_paused
            state = "PAUSADO ⏸️" if self.recorder.is_paused else "REANUDADO ▶️"
            logger.info(f"Estado micrófono alternado: {state}")

    # ------------------------------------------------------------------
    # Fallback en caliente del proveedor (saturación del backend de Live)
    # ------------------------------------------------------------------

    @property
    def _primary_model(self) -> str:
        return getattr(self._primary_provider, "model_name", "") or "modelo primario"

    def get_voice_status(self) -> dict:
        """Estado del proveedor de voz para el HUD (modelo activo y respaldo)."""
        return {
            "model": self._primary_model,
            "fallback_model": self._fallback_model or "",
            "active_model": getattr(self._active_provider, "model_name", "") or self._primary_model,
            "fallback_active": self._fallback_active,
        }

    @property
    def _fallback_model(self) -> Optional[str]:
        provider_cfg = getattr(self.config, "provider", None) if self.config else None
        return getattr(provider_cfg, "fallback_model", None) if provider_cfg else None

    def _fallback_enabled(self) -> bool:
        return bool(self._provider_factory and self._fallback_model)

    @property
    def _fallback_failures_threshold(self) -> int:
        provider_cfg = getattr(self.config, "provider", None) if self.config else None
        value = getattr(provider_cfg, "fallback_after_failures", 2) if provider_cfg else 2
        try:
            return max(1, int(value))
        except Exception:
            return 2

    def _track_provider_health(self, session_start_time: float, error: BaseException,
                               provider: Optional[BaseProvider] = None) -> None:
        """Cuenta fallos cortos de disponibilidad del primario y activa el fallback.

        Un 1011 tras una sesión larga es el idle-timeout normal del servidor: no
        debe gatillar el fallback. Tampoco cuenta un fallo corto de una sesión
        abierta con handle de resumption: lo habitual es que el handle apunte a
        una sesión ya muerta (el server la cerró por idle) y el intento limpio
        siguiente funcione — el backend está sano (visto en logs reales: se
        reportaba "no disponible 1/2" en cada corte idle con el mic pausado).
        Solo cuentan fallos cortos de sesiones limpias.
        """
        if self._provider_switch_requested:
            # Cierre voluntario para volver al primario: no es un fallo.
            self._provider_switch_requested = False
            logger.info("Reconexión voluntaria para volver al modelo principal.")
            return

        if self._fallback_active or not self._fallback_enabled():
            return

        provider = provider or self._active_provider
        if getattr(provider, "_last_connect_used_handle", False):
            logger.debug("Fallo corto con handle de resumption caducado: no cuenta para el fallback.")
            return

        if isinstance(error, BaseExceptionGroup):
            err_str = " ".join(str(exc) for exc in error.exceptions).lower()
        else:
            err_str = str(error).lower()

        is_unavailable = any(k in err_str for k in ("1011", "503", "high demand", "unavailable", "internal error"))
        short_session = (time.time() - session_start_time) < 15.0

        if is_unavailable and short_session:
            self._unavailable_streak += 1
            umbral = self._fallback_failures_threshold
            logger.warning(
                f"{self._primary_model} no disponible (fallo corto {self._unavailable_streak}/{umbral}): {error}"
            )
            if self._unavailable_streak >= umbral:
                self._activate_fallback()
        elif not short_session:
            # Sesión larga: el primario estaba sano, la racha se descarta.
            self._unavailable_streak = 0

    def _is_session_idle(self) -> bool:
        """True si no hay turno, tool ni audio en curso (apto para reconectar)."""
        if self._active_session is None:
            return True
        if self.recorder and (self.recorder.turn_in_flight or self.recorder.processing_tool
                              or self.recorder.waiting_for_model):
            return False
        if self.player and getattr(self.player, "is_speaking", False):
            return False
        return True

    def _activate_fallback(self) -> bool:
        """Cambia el proveedor activo al modelo de respaldo (solo runtime)."""
        if not self._fallback_enabled() or self._fallback_active:
            return False
        if self._fallback_provider is None:
            try:
                self._fallback_provider = self._provider_factory(self._fallback_model)
            except Exception as e:
                logger.error(f"No se pudo construir el proveedor de fallback ({self._fallback_model}): {e}")
                return False
        if self._fallback_provider is None:
            return False

        self._active_provider = self._fallback_provider
        self._fallback_active = True
        self._unavailable_streak = 0
        mensaje = (
            f"Gemini {self._primary_model} no está disponible (saturación del servicio). "
            f"Usando {self._fallback_model} temporalmente; se volverá al principal en cuanto se recupere."
        )
        logger.warning(f"⚠️ {mensaje}")
        self.event_bus.publish(SystemNotification(self.conversation_context, message=mensaje, kind="model"))
        # La sesión del primario está muerta (o moribunda): soltarla ya para que
        # un mensaje del usuario en la ventana de reconexión no se envíe a un
        # websocket cerrándose (se pierde igual, pero sin errores fantasma).
        self._active_session = None

        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_primary_loop())
        return True

    async def _probe_primary_once(self) -> bool:
        """Sonda end-to-end: sesión desechable + un mensaje; True si responde."""
        if not self._provider_factory:
            return False
        probe = None
        try:
            probe = self._provider_factory(self._primary_model)
            async with probe.connect("Responde en una palabra.", []) as session:
                await session.send_text("ping", end_of_turn=True)

                async def _esperar_respuesta() -> bool:
                    async for event in session.receive():
                        if isinstance(event, (TextChunk, AudioChunk, TurnComplete)):
                            return True
                    return False

                return await asyncio.wait_for(_esperar_respuesta(), timeout=8.0)
        except Exception as e:
            logger.debug(f"Sonda al modelo primario falló: {e}")
            return False

    async def _probe_primary_loop(self) -> None:
        """Cada N segundos prueba si el primario volvió; si sí, retoma el control."""
        provider_cfg = getattr(self.config, "provider", None) if self.config else None
        interval = float(getattr(provider_cfg, "fallback_probe_interval", 300.0)) if provider_cfg else 300.0
        interval = max(10.0, interval)
        try:
            while self._fallback_active:
                await asyncio.sleep(interval)
                if not self._fallback_active:
                    break
                if await self._probe_primary_once():
                    await self._return_to_primary()
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Bucle de sonda del primario terminó: {e}")

    async def _return_to_primary(self) -> None:
        """El primario volvió: reactivarlo y refrescar la sesión si está idle."""
        self._active_provider = self._primary_provider
        self._fallback_active = False
        self._unavailable_streak = 0
        mensaje = f"Gemini {self._primary_model} disponible de nuevo. Volviendo al modelo principal."
        logger.info(f"✅ {mensaje}")
        self.event_bus.publish(SystemNotification(self.conversation_context, message=mensaje, kind="model"))

        # Volver YA si no hay nada en curso; si el usuario está hablando, esperar
        # a que quede idle (hasta 5 min; si no, se aplica en la próxima reconexión).
        deadline = time.monotonic() + 300.0
        while not self._is_session_idle() and time.monotonic() < deadline:
            await asyncio.sleep(15.0)
        if self._is_session_idle():
            await self._force_session_refresh()

    async def _force_session_refresh(self) -> None:
        """Cierra la sesión activa para que el loop reconecte con el primario."""
        session = self._active_session
        if session is None:
            return
        self._provider_switch_requested = True
        logger.info("Refrescando sesión para volver al modelo principal...")
        close = getattr(session, "close", None)
        if close is None:
            self._provider_switch_requested = False
            logger.debug("La sesión activa no soporta close(); se volverá al primario en la próxima reconexión.")
            return
        try:
            await close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Suspensión de la sesión Live por pausa prolongada del micrófono
    # ------------------------------------------------------------------

    async def _suspend_session(self) -> None:
        """Cierra la sesión Live si el micrófono lleva mucho en pausa.

        Sin tráfico de audio el servidor reapea la sesión idle cada ~50 min
        (1006/1011) y el loop reconectaba para nada. Suspendida, el loop espera;
        al reanudar se reconecta. Idempotente: si una sesión terminó de conectar
        justo al suspender, el próximo tick la vuelve a cerrar.
        """
        if not self._session_suspended:
            self._session_suspended = True
            logger.info("Sesión Live suspendida (micrófono en pausa prolongada); se reconectará al reanudar.")
        session = self._active_session
        close = getattr(session, "close", None) if session is not None else None
        if close is not None:
            try:
                await close()
            except Exception:
                pass

    def _resume_session(self) -> None:
        """Marca el fin de la suspensión: el loop de sesión reconectará solo."""
        if not self._session_suspended:
            return
        self._session_suspended = False
        logger.info("Micrófono reanudado: reconectando sesión Live suspendida...")

    async def _pause_watcher_loop(self) -> None:
        """Vigila la pausa del micrófono y suspende/reanuda la sesión Live.

        El cierre de la sesión se hace acá y no en el setter de pausa para no
        acoplar el recorder al ciclo de vida del proveedor (Tab, /mute y HUD
        cambian el mismo estado). El loop de sesión espera mientras
        `_session_suspended` y reconecta en cuanto se limpia.
        """
        voice_cfg = getattr(self.config, "voice", None) if self.config else None
        try:
            suspend_after = float(getattr(voice_cfg, "pause_suspend_after", 90.0))
        except (TypeError, ValueError):
            suspend_after = 90.0
        if suspend_after <= 0:
            return  # desactivado por config

        check_interval = max(0.05, min(2.0, suspend_after / 2.0))
        paused_since: Optional[float] = None
        try:
            while True:
                await asyncio.sleep(check_interval)
                pausado = getattr(self.recorder, "is_paused", False) is True
                if pausado:
                    if paused_since is None:
                        paused_since = time.monotonic()
                    if (time.monotonic() - paused_since) >= suspend_after:
                        await self._suspend_session()
                else:
                    paused_since = None
                    self._resume_session()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Watcher de pausa terminó: {e}")

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

        # Cierre controlado ante SIGTERM/SIGHUP: cerrar la ventana de la terminal
        # enviaba SIGHUP y el proceso moría sin pasar por cleanup_async. Cancelar
        # la task principal ejecuta el finally de run_async (cleanup_async).
        main_task = asyncio.current_task()
        if main_task is not None:
            loop = asyncio.get_running_loop()
            shutdown_signals = [signal.SIGTERM]
            if hasattr(signal, "SIGHUP"):
                shutdown_signals.append(signal.SIGHUP)
            for sig in shutdown_signals:
                try:
                    loop.add_signal_handler(sig, main_task.cancel)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass

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
            # El HUD consulta el modelo activo (incluye si el fallback está en uso).
            self.overlay_server.get_voice_status = self.get_voice_status
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
            self.in_stream = self._open_input_stream()
            self.out_stream = self.p.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_OUT_RATE,
                output=True,
                frames_per_buffer=1024
            )

        def open_input_stream():
            # Reapertura tras una pausa que liberó el micrófono (ver
            # AudioRecorder._release_capture): cada reanudación abre un stream
            # nuevo, así que el cleanup del asistente delega en el recorder.
            with suppress_stderr():
                return self._open_input_stream()

        voice_cfg = self.config.voice if self.config else None
        self.recorder = AudioRecorder(
            self.in_stream,
            self.event_bus,
            self.conversation_context,
            voice_config=voice_cfg,
            in_stream_factory=open_input_stream
        )
        # Inicializar modelo de wake word y calibración de micrófono en paralelo
        await asyncio.gather(
            self.recorder.load_wake_word(),
            self.recorder.calibrate()
        )

        self.player = AudioPlayer(self.out_stream)

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()
        self._audio_out_queue = q_out

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
            session = self._active_session
            if session:
                msg = f"[SISTEMA: {event.message}]"

                async def _enviar_notificacion():
                    try:
                        await session.send_text(msg, end_of_turn=False)
                    except Exception as e:
                        # La sesión puede estar muriendo (p. ej. justo al activar
                        # el fallback): no queremos task exceptions sin observar.
                        logger.debug(f"No se pudo enviar la notificación al modelo: {e}")

                asyncio.create_task(_enviar_notificacion())

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
        # Suspende la sesión Live si el micrófono queda en pausa prolongada.
        pause_task = asyncio.create_task(self._pause_watcher_loop())

        attempt = 0
        backoff = self.reconnect_initial_backoff
        is_first_connection = True

        try:
            # None = reintentos ilimitados: un asistente de voz no debe morir
            # permanentemente por una racha de fallos de conexión.
            while self.max_reconnect_attempts is None or attempt < self.max_reconnect_attempts:
                # Pausa prolongada: la sesión se suspendió a propósito; esperar
                # sin reconectar ni contar intentos (el watcher de pausa la
                # reactiva al reanudar el micrófono).
                while self._session_suspended:
                    await asyncio.sleep(0.5)
                attempt += 1
                session_start_time = time.time()
                if is_first_connection:
                    logger.info(f"Conectando al proveedor (intento {attempt}/{self.max_reconnect_attempts})...")
                else:
                    logger.debug(f"Reconectando al proveedor (intento {attempt}/{self.max_reconnect_attempts})...")

                sys_prompt, _ = build_system_prompt(self.knowledge_manager, trajectory_manager=self.trajectory_manager, config=self.config)

                # Proveedor de esta sesión: puede ser el primario o el fallback
                # en caliente si el backend del primario está saturado.
                provider = self._active_provider
                try:
                    async with provider.connect(
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
                            self.recorder.turn_in_flight = False
                            self.recorder.pending_sleep = False
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
                            else:
                                self._track_provider_health(session_start_time, eg, provider)
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
                    if attempt > 1 and hasattr(provider, "reset_session_handle"):
                        provider.reset_session_handle()

                    # Racha de fallos cortos de disponibilidad → fallback en caliente
                    self._track_provider_health(session_start_time, e, provider)

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
            if self._probe_task and not self._probe_task.done():
                self._probe_task.cancel()
            pause_task.cancel()
            terminal_task.cancel()
            recorder_task.cancel()
            player_task.cancel()
            await self.cleanup_async()

    def _open_input_stream(self):
        """Abre un stream de captura nuevo (se usa al reanudar tras una pausa).

        La pausa cierra el stream para soltar el micrófono y apagar el indicador
        de GNOME; como un stream cerrado no se reabre, cada reanudación crea uno.
        """
        return self.p.open(
            format=AUDIO_FORMAT,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_IN_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )

    async def cleanup_async(self):
        """Limpieza asíncrona de recursos de audio, HUD, recordatorios y MCP."""
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            old_stderr = os.dup(2)
            sys.stderr.flush()
            os.dup2(devnull, 2)
            try:
                # El recorder pudo reemplazar el stream al reanudar una pausa:
                # delegar en él el cierre de la captura actual.
                if self.recorder:
                    self.recorder.close_capture()
                elif self.in_stream:
                    self.in_stream.close()
                if self.out_stream and self.out_stream.is_active():
                    self.out_stream.stop_stream()
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
        except asyncio.CancelledError:
            # SIGTERM/SIGHUP: el handler canceló la task principal y el finally
            # de run_async ya ejecutó cleanup_async (recursos liberados).
            self.event_bus.publish(SessionEnded(self.conversation_context))
            os._exit(0)
