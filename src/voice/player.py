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
        "processing": "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
        "wake_detected": "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
        "sleep": "/usr/share/sounds/freedesktop/stereo/service-logout.oga"
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
        # Diagnóstico: bytes efectivamente escritos al hardware en el segmento
        # de habla actual (permite verificar que el audio no solo llega del
        # proveedor sino que se reproduce físicamente).
        self._seg_bytes = 0

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
                if not self.is_speaking:
                    self._seg_bytes = 0
                    logger.debug("▶️ Inicio de segmento de reproducción")
                self.is_speaking = True

                # Escribir chunk al hardware de audio de forma fluida
                await asyncio.to_thread(self.out_stream.write, data, exception_on_underflow=False)
                self._seg_bytes += len(data)
                self.last_speech_time = time.time()

                # Si no quedan más chunks en la cola inmediata, mantener is_speaking activo
                # durante un breve margen para evitar falsas transiciones entre chunks seguidos
                if audio_queue_output.empty():
                    try:
                        next_data = await asyncio.wait_for(audio_queue_output.get(), timeout=0.20)
                        await asyncio.to_thread(self.out_stream.write, next_data, exception_on_underflow=False)
                        self._seg_bytes += len(next_data)
                        self.last_speech_time = time.time()
                    except asyncio.TimeoutError:
                        self.is_speaking = False
                        logger.info(
                            f"🔊 Segmento escrito al hardware de audio: {self._seg_bytes} bytes "
                            f"(~{self._seg_bytes / 48000:.1f}s a 24kHz)"
                        )
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
