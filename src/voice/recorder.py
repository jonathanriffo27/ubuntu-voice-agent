import asyncio
import struct
import sys
from src.voice.constants import AUDIO_IN_RATE, CHUNK_SIZE
from src.events.base import ConversationContext, VoiceListeningStarted, VoiceListeningStopped, SpeechRecognized
from src.events.bus import EventBus
from src.voice.player import play_sound


class AudioRecorder:
    """Maneja la captura de micrófono y la detección de actividad de voz (VAD)."""

    def __init__(self, in_stream, event_bus: EventBus, conversation_context: ConversationContext):
        self.in_stream = in_stream
        self.event_bus = event_bus
        self.conversation_context = conversation_context

        self.silence_threshold: int | None = None
        self.is_paused = False
        self.processing_tool = False

        # Modelo Wake Word
        self.oww_model = None
        self.np = None

    def load_wake_word(self):
        """Carga el modelo openwakeword para 'Hey Atlas'."""
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
            print("❌ ERROR: openwakeword o numpy no están instalados.")
            sys.exit(1)

    async def calibrate(self, duration: float = 0.5) -> int:
        """Calibra dinámicamente el umbral de silencio según el ruido ambiental."""
        samples = []
        total_frames = int(AUDIO_IN_RATE / CHUNK_SIZE * duration)
        for _ in range(total_frames):
            data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            shorts = struct.unpack('h' * (len(data) // 2), data)
            rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
            samples.append(rms)

        if not samples:
            self.silence_threshold = 12000
            return 12000

        avg_rms = sum(samples) / len(samples)
        threshold = max(800, int(avg_rms * 2.5))
        self.silence_threshold = threshold
        return threshold

    async def listen(self, audio_queue_input: asyncio.Queue, audio_queue_output: asyncio.Queue, player=None):
        """Bucle principal de escucha con VAD, wake word y detección de interrupciones (barge-in)."""
        silence_frames = 0
        frames_per_second = AUDIO_IN_RATE / CHUNK_SIZE
        max_silence_seconds = 1.0
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
                    shorts = struct.unpack('h' * (len(data) // 2), data)
                    rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0

                    is_speaking = player.is_speaking if player else False

                    if is_speaking:
                        barge_in_threshold = self.silence_threshold or 12000
                        if rms > barge_in_threshold:
                            print("\n[🎙️ Interrupción de voz detectada]", flush=True)
                            if player:
                                player.is_speaking = False

                            while not audio_queue_output.empty():
                                try:
                                    audio_queue_output.get_nowait()
                                except asyncio.QueueEmpty:
                                    break

                            silence_frames = 0
                            user_spoke = True
                            await audio_queue_input.put(data)
                    else:
                        await audio_queue_input.put(data)

                        current_threshold = self.silence_threshold or 12000
                        if rms < current_threshold:
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
                print(f"Error micro: {e}")
                break
