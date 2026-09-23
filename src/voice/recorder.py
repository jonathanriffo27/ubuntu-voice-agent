import asyncio
import math
import struct
import sys
import time
from collections import deque
from enum import Enum, auto
from typing import Optional

from src.voice.constants import AUDIO_IN_RATE, CHUNK_SIZE
from src.events.base import (
    ConversationContext, VoiceListeningStarted, VoiceListeningStopped,
    SpeechRecognized, ModelThinkingStarted, WakeWordDetected, WakeWordStandby
)
from src.events.bus import EventBus
from src.voice.player import play_sound
from src.voice.vad import VoiceActivityDetector
from src.voice.wake_word import WakeWordDetector
from src.utils.logging import get_logger

logger = get_logger("voice.recorder")


class RecorderState(Enum):
    STANDBY = auto()    # Escucha local de wake word, 0 envío a Gemini Live
    ACTIVE = auto()     # Streaming activo a Gemini Live
    FOLLOW_UP = auto()  # Ventana post-respuesta esperando repregunta
    MUTED = auto()      # Silenciado manualmente (Tab / Mute)


class AudioRecorder:
    """Maneja la captura de micrófono, VAD y la máquina de estados de activación por voz."""

    def __init__(
        self,
        in_stream,
        event_bus: EventBus,
        conversation_context: ConversationContext,
        voice_config=None
    ):
        self.in_stream = in_stream
        self.event_bus = event_bus
        self.conversation_context = conversation_context
        self._voice_config = voice_config

        self.silence_threshold: int | None = None
        self.processing_tool = False
        self.waiting_for_model = False
        self.vad = VoiceActivityDetector(sample_rate=AUDIO_IN_RATE)
        # Última vez que el VAD LOCAL detectó voz humana real. El asistente la
        # usa para distinguir un barge-in genuino de un falso positivo del VAD
        # del servidor (gemini-3.8 lo dispara con ruido ambiente).
        self.last_local_voice_time: float = 0.0
        # Turno del modelo en curso: lo marca el asistente al recibir el primer
        # texto/audio/tool call tras la voz del usuario y lo limpia en
        # TurnComplete/Interrupción. Con server_vad el flag waiting_for_model
        # NUNCA se activa, así que sin esta marca el timeout de inactividad
        # mandaba a STANDBY en medio de una respuesta si el modelo hacía una
        # pausa larga entre segmentos de audio (bug: "¿En qué te puedo..." →
        # 💤 [En Espera] → el resto llegaba tras repetir la wake word).
        self.turn_in_flight: bool = False
        # Marca temporal de la última entrada a STANDBY. Ventana de gracia para
        # no evaluar la wake word mientras aún suena el chime "sleep": ese sonido
        # sale por los altavoces (pw-play, fuera del tracking del player) y
        # openWakeWord se auto-disparaba (~0.85 de confianza) al instante de dormir.
        self._standby_entered: float = 0.0
        # Cierre de conversación iniciado por el usuario ("no, por ahora no,
        # gracias"): el modelo llama a la tool 'entrar_en_espera' y el salto a
        # STANDBY se aplica al TurnComplete del turno de despedida (así la frase
        # termina de reproducirse). Sin ventana follow-up de N segundos.
        self.pending_sleep: bool = False
        # Timestamp del sueño deliberado: Gemini Live emite DOS TurnComplete por
        # turno (generation_complete + turn_complete, ver gemini_session.py), y el
        # duplicado no debe re-despertar a FOLLOW_UP justo después de dormir.
        self._deliberate_sleep_at: float = 0.0

        # Estado inicial según configuración
        mode = getattr(voice_config, 'mode', 'always_on') if voice_config else 'always_on'
        self._state = RecorderState.STANDBY if mode == 'wake_word' else RecorderState.ACTIVE

        self._follow_up_last_active: float = 0.0
        self._active_started: float = 0.0
        self.wake_detector = WakeWordDetector()

    @property
    def is_paused(self) -> bool:
        """Propiedad de compatibilidad con UI y atajos existentes."""
        return self._state == RecorderState.MUTED

    @is_paused.setter
    def is_paused(self, value: bool):
        if value:
            self._state = RecorderState.MUTED
        else:
            mode = getattr(self._voice_config, 'mode', 'always_on') if self._voice_config else 'always_on'
            self._state = RecorderState.STANDBY if mode == 'wake_word' else RecorderState.ACTIVE

    @property
    def state(self) -> RecorderState:
        return self._state

    async def load_wake_word(self, wake_word: Optional[str] = None):
        """Carga el modelo de wake word en background sin bloquear el event loop."""
        ww = wake_word or (getattr(self._voice_config, 'wake_word', 'hey_jarvis') if self._voice_config else 'hey_jarvis')
        await asyncio.to_thread(self.wake_detector.load_sync, ww)

    def request_sleep_after_turn(self):
        """El modelo aceptó cerrar la conversación (tool 'entrar_en_espera').
        El salto a STANDBY se aplica en el próximo enter_follow_up (TurnComplete),
        de modo que la despedida hablada termine de reproducirse antes de dormir."""
        self.pending_sleep = True

    def enter_follow_up(self):
        """Inicia la ventana de follow-up post-respuesta de Gemini."""
        self.waiting_for_model = False
        mode = getattr(self._voice_config, 'mode', 'always_on') if self._voice_config else 'always_on'
        if mode != 'wake_word' or self._state == RecorderState.MUTED:
            return
        if self.pending_sleep:
            # El usuario cerró la conversación: dormir de inmediato, sin follow-up.
            self.pending_sleep = False
            self._state = RecorderState.STANDBY
            now = time.time()
            self._standby_entered = now
            self._deliberate_sleep_at = now
            self.wake_detector.reset()
            play_sound("sleep")
            self.event_bus.publish(WakeWordStandby(self.conversation_context))
            return
        # El TurnComplete duplicado (generation_complete + turn_complete) no debe
        # reabrir la ventana justo después de un sueño deliberado.
        if self._state == RecorderState.STANDBY and (time.time() - self._deliberate_sleep_at) < 5.0:
            return
        self._state = RecorderState.FOLLOW_UP
        self._follow_up_last_active = time.time()

    async def calibrate(self, max_drain_frames: int = 25, sample_frames: int = 15) -> int:
        """
        Calibra dinámicamente el umbral de silencio según el ruido ambiental real:
        1. Drena de forma adaptativa los transitorios de hardware de ALSA/AGC (< 5000 RMS).
        2. Muestrea 15 frames limpios de ruido ambiente real.
        3. Usa el percentil 25 para capturar el piso de ruido real.
        4. Calcula un umbral óptimo de voz con clamp seguro entre 1200 y 3500 RMS.
        """
        for _ in range(max_drain_frames):
            data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            shorts = struct.unpack('h' * (len(data) // 2), data)
            if shorts:
                rms = math.sqrt(sum(s * s for s in shorts) / len(shorts))
                if rms < 5000:
                    break

        samples = []
        for _ in range(sample_frames):
            data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            shorts = struct.unpack('h' * (len(data) // 2), data)
            if shorts:
                rms = math.sqrt(sum(s * s for s in shorts) / len(shorts))
                samples.append(rms)

        if not samples:
            self.silence_threshold = 2200
            return 2200

        samples.sort()
        p25_index = max(0, len(samples) // 4)
        noise_floor = samples[p25_index]

        threshold = int(noise_floor * 1.7 + 350)
        clamped_threshold = min(3500, max(1200, threshold))
        self.silence_threshold = clamped_threshold

        if hasattr(self, 'vad') and self.vad:
            self.vad._noise_energy = noise_floor

        logger.info(f"Micrófono calibrado (piso de ruido: {int(noise_floor)} RMS) -> Umbral VAD: {clamped_threshold}")
        return clamped_threshold

    async def listen(self, audio_queue_input: asyncio.Queue, audio_queue_output: asyncio.Queue, player=None):
        """Bucle principal de escucha con máquina de estados de activación por voz y VAD."""
        silence_frames = 0
        frames_per_second = AUDIO_IN_RATE / CHUNK_SIZE
        max_silence_seconds = 0.9
        user_spoke = False
        # Pre-roll (~380ms a 512 samples/frame) para no recortar el inicio de la
        # locución cuando el gate anti-ruido abre el stream al detectar voz.
        pre_roll: deque = deque(maxlen=12)

        follow_up_timeout = float(getattr(self._voice_config, 'follow_up_timeout', 7.0)) if self._voice_config else 7.0
        activation_sound = bool(getattr(self._voice_config, 'activation_sound', True)) if self._voice_config else True
        # Si el VAD server-side está activo, el servidor decide el fin de turno:
        # no enviamos la señal local END_OF_TURN para evitar turnos duplicados.
        server_vad = bool(getattr(self._voice_config, 'server_vad', False)) if self._voice_config else False

        while True:
            try:
                if not self.in_stream.is_active():
                    break

                data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)

                # Umbral dinámico configurable en tiempo real
                ww_threshold = float(getattr(self._voice_config, 'wake_word_threshold', 0.35)) if self._voice_config else 0.35

                # 1. Estado MUTED: silenciado total
                if self._state == RecorderState.MUTED:
                    await asyncio.sleep(0.001)
                    continue

                # 2. Estado STANDBY: solo detección local de wake word (0 tráfico a Gemini)
                if self._state == RecorderState.STANDBY:
                    # Ventana de gracia post-sueño: el chime "sleep" sale por los
                    # altavoces vía pw-play (el player no lo rastrea) y su eco
                    # entraba directo al detector, auto-despertando a Atlas ~1s
                    # después de dormir (falso positivo de 0.85 observado).
                    if time.time() - self._standby_entered < 1.2:
                        await asyncio.sleep(0.001)
                        continue
                    # Guardia anti-eco: la voz de Atlas sale por los altavoces y entra
                    # al micrófono; sin este filtro openWakeWord se auto-dispara con la
                    # propia voz (falsos positivos 0.95+ tras cada respuesta hablada).
                    if player is not None:
                        time_since_speech = time.time() - getattr(player, 'last_speech_time', 0.0)
                        if player.is_speaking or time_since_speech < 0.8:
                            await asyncio.sleep(0.001)
                            continue
                    detected, name, score = self.wake_detector.predict(data, threshold=ww_threshold)
                    if detected:
                        self.wake_detector.reset()
                        self._state = RecorderState.ACTIVE
                        self._active_started = time.time()
                        self.waiting_for_model = False
                        user_spoke = False
                        silence_frames = 0
                        if activation_sound:
                            play_sound("wake_detected")
                        self.event_bus.publish(WakeWordDetected(
                            self.conversation_context, wake_word=name, confidence=score
                        ))
                    await asyncio.sleep(0.001)
                    continue

                # 3. Timeout en estado ACTIVE si el usuario no inicia ninguna consulta
                # OJO: nunca dormir mientras hay una herramienta ejecutándose; con
                # server_vad el flag waiting_for_model no se usa, y búsquedas largas
                # (15-45s) mandaban el sistema a STANDBY a mitad de la tool.
                # turn_in_flight: con server_vad el modelo puede pausar varios
                # segundos ENTRE segmentos de audio de una misma respuesta; dormir
                # ahí partía la frase a mitad y el resto llegaba en el siguiente
                # ciclo de wake word.
                if (self._state == RecorderState.ACTIVE and not user_spoke
                        and not self.waiting_for_model and not self.processing_tool
                        and not self.turn_in_flight):
                    if (time.time() - getattr(self, '_active_started', 0.0)) > follow_up_timeout:
                        self._state = RecorderState.STANDBY
                        self._standby_entered = time.time()
                        self.wake_detector.reset()
                        play_sound("sleep")
                        self.event_bus.publish(WakeWordStandby(self.conversation_context))
                        silence_frames = 0
                        user_spoke = False
                        continue

                # 4. Estado FOLLOW_UP: ventana de espera tras respuesta de Atlas
                # (processing_tool: turn_complete puede llegar CON la function
                #   call; sin esta guardia el sistema caía a STANDBY a mitad de
                #   una herramienta y la wake word se evaluaba durante ella)
                if self._state == RecorderState.FOLLOW_UP:
                    elapsed = time.time() - self._follow_up_last_active
                    if (elapsed > follow_up_timeout and not self.processing_tool
                            and not self.turn_in_flight):
                        self._state = RecorderState.STANDBY
                        self._standby_entered = time.time()
                        self.wake_detector.reset()
                        play_sound("sleep")
                        self.event_bus.publish(WakeWordStandby(self.conversation_context))
                        silence_frames = 0
                        user_spoke = False
                        continue
                    # Si no ha expirado, continúa al flujo de captura activa

                # 5. Estados ACTIVE y FOLLOW_UP: captura y streaming hacia Gemini Live
                if not self.processing_tool and not self.waiting_for_model:
                    is_speaking = player.is_speaking if player else False
                    time_since_speech = time.time() - getattr(player, 'last_speech_time', 0.0) if player else 999.0

                    # Aislamiento de Eco Acústico (Half-Duplex seguro):
                    # Mientras Atlas habla o en los 350ms posteriores, mantener vivo el temporizador
                    # para que la ventana de 7 segundos empiece estrictamente al terminar de hablar Atlas
                    if is_speaking or time_since_speech < 0.35:
                        silence_frames = 0
                        user_spoke = False
                        self._follow_up_last_active = time.time()
                        self._active_started = time.time()
                        continue

                    # Captura activa de voz con VAD
                    is_voice = self.vad.is_speech(data, current_threshold=self.silence_threshold)
                    if is_voice:
                        # Evidencia local de voz real: valida si un "interrupted"
                        # del servidor fue barge-in genuino o falso positivo.
                        self.last_local_voice_time = time.time()

                    # Gate anti-ruido (gemini-3.8, VAD del servidor agresivo):
                    # mientras se busca el inicio de la locución (user_spoke
                    # aún False) SOLO se envía audio con voz real al servidor.
                    # Antes se enviaba TODO el ambiente (TV, teclado, respiración)
                    # y el VAD remoto lo leía como "usuario hablando", generando
                    # interruptions fantasma que vaciaban la cola de audio de
                    # Atlas (síntoma: texto visible, voz muda).
                    if not user_spoke and not is_voice:
                        pre_roll.append(data)
                        await asyncio.sleep(0)
                        continue

                    # Onset de voz: vaciar el pre-roll (~380ms) para no cortar
                    # las primeras sílabas de la frase, y luego el frame actual.
                    if not user_spoke and is_voice:
                        while pre_roll:
                            await audio_queue_input.put(pre_roll.popleft())

                    await audio_queue_input.put(data)

                    if not is_voice:
                        if user_spoke:
                            silence_frames += 1
                    else:
                        silence_frames = 0
                        if not user_spoke:
                            self.event_bus.publish(VoiceListeningStarted(self.conversation_context))
                        user_spoke = True
                        # Si el usuario habló durante follow-up, regresar de inmediato a ACTIVE
                        if self._state == RecorderState.FOLLOW_UP:
                            self._state = RecorderState.ACTIVE

                    # Detección de fin de turno por silencio sostenido (solo con VAD local)
                    if not server_vad and user_spoke and silence_frames > (frames_per_second * max_silence_seconds):
                        await audio_queue_input.put("END_OF_TURN")
                        self.event_bus.publish(VoiceListeningStopped(self.conversation_context))
                        self.event_bus.publish(SpeechRecognized(self.conversation_context, text="[Audio enviado]"))
                        self.event_bus.publish(ModelThinkingStarted(self.conversation_context))
                        play_sound("processing")
                        silence_frames = 0
                        user_spoke = False
                        self.waiting_for_model = True
                else:
                    # Mientras una herramienta se ejecuta el sistema sigue "en
                    # turno": refrescar los contadores de inactividad para que ni
                    # el timeout de ACTIVE ni la ventana FOLLOW_UP caduquen.
                    silence_frames = 0
                    user_spoke = False
                    if self.processing_tool:
                        self._active_started = time.time()
                        self._follow_up_last_active = time.time()


                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en bucle de audio: {e}")
                break
