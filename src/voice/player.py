import asyncio
import time
import subprocess
from src.utils.logging import get_logger

logger = get_logger("voice.player")


def play_sound(sound_type: str):
    """Reproduce sonidos del sistema via PipeWire."""
    sounds = {
        "ready": "/usr/share/sounds/freedesktop/stereo/service-login.oga",
        "pause": "/usr/share/sounds/freedesktop/stereo/device-removed.oga",
        "resume": "/usr/share/sounds/freedesktop/stereo/device-added.oga",
        "processing": "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga"
    }
    if sound_type in sounds:
        try:
            subprocess.Popen(["pw-play", sounds[sound_type]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


class AudioPlayer:
    """Reproduce audio recibido del modelo LLM de forma fluida y continua sin microcortes."""

    def __init__(self, out_stream):
        self.out_stream = out_stream
        self.is_speaking = False
        self.last_speech_time = 0.0

    def stop_and_clear(self, audio_queue_output: asyncio.Queue):
        """Detiene la reproducción y vacía la cola de audio pendiente."""
        self.is_speaking = False
        self.last_speech_time = time.time()
        while not audio_queue_output.empty():
            try:
                audio_queue_output.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def play(self, audio_queue_output: asyncio.Queue):
        """
        Bucle continuo de reproducción de audio.
        Escribe directamente en el stream de PyAudio en un thread dedicado sin retrasos
        artificiales para evitar buffer underruns y voz entrecortada.
        """
        while True:
            try:
                data = await audio_queue_output.get()
                self.is_speaking = True

                # Escribir chunk al hardware de audio de forma fluida
                await asyncio.to_thread(self.out_stream.write, data, exception_on_underflow=False)
                self.last_speech_time = time.time()

                # Si no quedan más chunks en la cola inmediata, mantener is_speaking activo
                # durante un breve margen para evitar falsas transiciones entre chunks seguidos
                if audio_queue_output.empty():
                    try:
                        next_data = await asyncio.wait_for(audio_queue_output.get(), timeout=0.20)
                        await asyncio.to_thread(self.out_stream.write, next_data, exception_on_underflow=False)
                        self.last_speech_time = time.time()
                    except asyncio.TimeoutError:
                        self.is_speaking = False
                        self.last_speech_time = time.time()
            except asyncio.CancelledError:
                self.is_speaking = False
                self.last_speech_time = time.time()
                break
            except Exception as e:
                logger.debug(f"Error en reproducción de audio: {e}")
                self.is_speaking = False
                self.last_speech_time = time.time()
                await asyncio.sleep(0.01)
