import asyncio
import math
import struct
import sys
from src.voice.constants import AUDIO_IN_RATE, CHUNK_SIZE
from src.events.base import ConversationContext, VoiceListeningStarted, VoiceListeningStopped, SpeechRecognized, ModelThinkingStarted
from src.events.bus import EventBus
from src.voice.player import play_sound
from src.voice.vad import VoiceActivityDetector
from src.utils.logging import get_logger

logger = get_logger("voice.recorder")


class AudioRecorder:
    """Maneja la captura de micrófono y la detección inteligente de actividad de voz (VAD)."""

    def __init__(self, in_stream, event_bus: EventBus, conversation_context: ConversationContext):
        self.in_stream = in_stream
        self.event_bus = event_bus
        self.conversation_context = conversation_context

        self.silence_threshold: int | None = None
        self.is_paused = False
        self.processing_tool = False
        self.vad = VoiceActivityDetector(sample_rate=AUDIO_IN_RATE)

        # Modelo Wake Word
        self.oww_model = None
        self.np = None

    def _load_wake_word_sync(self):
        """Carga el modelo openwakeword para 'Hey Atlas' de forma síncrona."""
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                import openwakeword
                from openwakeword.model import Model
                import numpy as np
                self.np = np
                model_paths = [p for p in openwakeword.get_pretrained_model_paths() if 'hey_jarvis' in p]
                self.oww_model = Model(wakeword_model_paths=model_paths)
        except ImportError:
            logger.error("openwakeword o numpy no están instalados.")
            sys.exit(1)

    async def load_wake_word(self):
        """Carga el modelo openwakeword en segundo plano sin bloquear el loop de eventos."""
        await asyncio.to_thread(self._load_wake_word_sync)

    async def calibrate(self, max_drain_frames: int = 25, sample_frames: int = 15) -> int:
        """
        Calibra dinámicamente el umbral de silencio según el ruido ambiental real:
        1. Drena de forma adaptativa los transitorios de hardware de ALSA/AGC hasta que la señal se estabilice (< 5000 RMS).
        2. Muestrea 15 frames limpios de ruido ambiente real.
        3. Usa el percentil 25 para capturar el piso de ruido real de la habitación sin sesgo por ruidos.
        4. Calcula un umbral óptimo de voz (1.7x ruido base + 350) con clamp seguro entre 1200 y 3500 RMS.
        """
        # 1. Drenaje adaptativo de transitorios de hardware
        for _ in range(max_drain_frames):
            data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            shorts = struct.unpack('h' * (len(data) // 2), data)
            if shorts:
                rms = math.sqrt(sum(s * s for s in shorts) / len(shorts))
                if rms < 5000:
                    break

        # 2. Muestreo de ruido ambiente estabilizado
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

        # 3. Percentil 25 del piso de ruido ambiental
        samples.sort()
        p25_index = max(0, len(samples) // 4)
        noise_floor = samples[p25_index]

        # 4. Umbral de voz adaptado (1.7x ruido base + 350)
        threshold = int(noise_floor * 1.7 + 350)
        clamped_threshold = min(3500, max(1200, threshold))
        self.silence_threshold = clamped_threshold

        if hasattr(self, 'vad') and self.vad:
            self.vad._noise_energy = noise_floor

        logger.info(f"Micrófono calibrado (piso de ruido real: {int(noise_floor)} RMS) -> Umbral VAD: {clamped_threshold}")
        return clamped_threshold

    async def listen(self, audio_queue_input: asyncio.Queue, audio_queue_output: asyncio.Queue, player=None):
        """Bucle principal de escucha con VAD inteligente, wake word y detección de interrupciones (barge-in)."""
        silence_frames = 0
        barge_in_counter = 0
        frames_per_second = AUDIO_IN_RATE / CHUNK_SIZE
        max_silence_seconds = 0.9
        user_spoke = False

        while True:
            try:
                if not self.in_stream.is_active():
                    break

                data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)

                # Detección de Wake Word si está pausado
                if self.is_paused:
                    if self.oww_model and self.np is not None:
                        audio_np = self.np.frombuffer(data, dtype=self.np.int16)
                        prediction = self.oww_model.predict(audio_np)
                        max_score = max(prediction.values()) if prediction else 0
                        if max_score > 0.5:
                            self.is_paused = False
                            play_sound("resume")
                            print(f"\r[REANUDADO ▶️] - ¡'Hey Atlas' detectado! Micrófono activado.      \n", end='', flush=True)
                    await asyncio.sleep(0.001)
                    continue

                if not self.processing_tool:
                    is_speaking = player.is_speaking if player else False

                    if is_speaking:
                        # Mientras Atlas habla por los parlantes, elevar el umbral para evitar que el micrófono escuche los propios parlantes (Eco Acústico)
                        # y exigir al menos 6 frames consecutivos (~200ms) de voz fuerte para confirmar un barge-in intencional
                        barge_threshold = max(4500.0, (self.silence_threshold or 2500.0) * 2.2)
                        is_loud_speech = self.vad.is_speech(data, current_threshold=barge_threshold)

                        if is_loud_speech:
                            barge_in_counter += 1
                        else:
                            barge_in_counter = 0

                        if barge_in_counter >= 6:
                            logger.info("🎙️ Interrupción de usuario confirmada (Barge-in intencional)")
                            barge_in_counter = 0
                            if player:
                                player.stop_and_clear(audio_queue_output)
                            silence_frames = 0
                            user_spoke = True
                            await audio_queue_input.put(data)
                    else:
                        barge_in_counter = 0
                        is_voice = self.vad.is_speech(data, current_threshold=self.silence_threshold)
                        await audio_queue_input.put(data)

                        if not is_voice:
                            if user_spoke:
                                silence_frames += 1
                        else:
                            silence_frames = 0
                            if not user_spoke:
                                self.event_bus.publish(VoiceListeningStarted(self.conversation_context))
                            user_spoke = True

                        if user_spoke and silence_frames > (frames_per_second * max_silence_seconds):
                            await audio_queue_input.put("END_OF_TURN")
                            self.event_bus.publish(VoiceListeningStopped(self.conversation_context))
                            self.event_bus.publish(SpeechRecognized(self.conversation_context, text="[Audio enviado]"))
                            self.event_bus.publish(ModelThinkingStarted(self.conversation_context))
                            play_sound("processing")
                            silence_frames = 0
                            user_spoke = False
                else:
                    silence_frames = 0
                    user_spoke = False

                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en bucle de audio: {e}")
                break
