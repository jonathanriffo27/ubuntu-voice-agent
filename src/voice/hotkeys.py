"""
[DEPRECATED] Este módulo ha sido reemplazado por TerminalInteractionManager en src/ui/terminal_input.py,
el cual proporciona captura unificada de teclado, prompts de texto, edición en línea,
historial, aprobaciones HITL y atajos avanzados sin colisiones de stdin.
"""
import asyncio
import sys
from src.voice.player import play_sound


class HotkeyListener:
    """[LEGACY] Escucha teclas en modo raw terminal para controles del asistente."""

    def __init__(self, recorder, audio_queue_input: asyncio.Queue):
        self.recorder = recorder
        self.audio_queue_input = audio_queue_input

    async def listen(self):
        import termios, tty
        loop = asyncio.get_running_loop()

        def read_char():
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                return sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        while True:
            try:
                ch = await loop.run_in_executor(None, read_char)
                ch = ch.lower()
                if ch == ' ':
                    self.recorder.is_paused = not self.recorder.is_paused
                    play_sound("pause" if self.recorder.is_paused else "resume")
                    estado = "PAUSADO ⏸️" if self.recorder.is_paused else "REANUDADO ▶️"
                    print(f"\r[{estado}] - Micrófono {'desactivado' if self.recorder.is_paused else 'activado'}.\n", end='', flush=True)
                elif ch == 'l':
                    if self.recorder.silence_threshold is not None:
                        self.recorder.silence_threshold += 500
                    else:
                        self.recorder.silence_threshold = 12000
                    print(f"\r[🎤 UMBRAL] Aumentado a: {self.recorder.silence_threshold}\n", end='', flush=True)
                elif ch == 'j':
                    if self.recorder.silence_threshold is not None:
                        self.recorder.silence_threshold = max(0, self.recorder.silence_threshold - 500)
                    else:
                        self.recorder.silence_threshold = 12000
                    print(f"\r[🎤 UMBRAL] Reducido a: {self.recorder.silence_threshold}\n", end='', flush=True)
                elif ch == '\n' or ch == '\r':
                    try:
                        await self.audio_queue_input.put("END_OF_TURN")
                        play_sound("processing")
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error en hotkey listener: {e}")
                break
