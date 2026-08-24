import asyncio
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

    def stop_and_clear(self, audio_queue_output: asyncio.Queue):
        """Detiene la reproducción y vacía la cola de audio pendiente (para interrupciones / barge-in)."""
        self.is_speaking = False
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
                # Esperar chunk con timeout para detectar silencio final y no dejar is_speaking colgado
                try:
                    data = await asyncio.wait_for(audio_queue_output.get(), timeout=0.25)
                    self.is_speaking = True
                    await asyncio.to_thread(self.out_stream.write, data, exception_on_underflow=False)
                except asyncio.TimeoutError:
                    self.is_speaking = False
            except asyncio.CancelledError:
                self.is_speaking = False
                break
            except Exception as e:
                logger.debug(f"Error en reproducción de audio: {e}")
                self.is_speaking = False
                await asyncio.sleep(0.01)
