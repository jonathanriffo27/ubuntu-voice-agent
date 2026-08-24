import asyncio
import subprocess


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
    """Reproduce audio recibido del modelo LLM."""

    def __init__(self, out_stream):
        self.out_stream = out_stream
        self.is_speaking = False

    async def play(self, audio_queue_output: asyncio.Queue):
        """Bucle de reproducción de audio desde la cola."""
        while True:
            try:
                data = await asyncio.wait_for(audio_queue_output.get(), timeout=1.5)
                if not self.is_speaking:
                    continue
                await asyncio.to_thread(self.out_stream.write, data)
                await asyncio.sleep(0.001)
            except asyncio.TimeoutError:
                self.is_speaking = False
            except Exception as e:
                print(f"Error speaker: {e}")
                break
