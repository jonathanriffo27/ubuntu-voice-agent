import asyncio
import os
import sys
import termios
import tty
from typing import List, Optional
from src.voice.player import play_sound
from src.utils.logging import get_logger

logger = get_logger("ui.terminal_input")


class TerminalInteractionManager:
    """
    Gestor interactivo de teclado para la consola de Atlas.
    Permite:
    1. Escritura libre de prompts con auto-wrap nativo sin duplicación.
    2. Edición en línea completa: mover cursor con flechas (← / →), Inicio, Fin, Supr, Backspace.
    3. Historial de prompts (↑ / ↓).
    4. Atajos directos de 1 tecla para Mute (Tab, Ctrl+Espacio, Shift+Espacio, F2).
    5. Ajuste del umbral de silencio VAD (Ctrl+Arriba / Ctrl+Abajo).
    6. Aprobación rápida HITL al presionar Enter en una línea vacía.
    7. Comandos rápidos de control (/mute, /help, /clear, /sensibilidad).
    """

    def __init__(
        self,
        assistant,
        recorder=None,
        approval_manager=None,
        audio_queue_input=None
    ):
        self.assistant = assistant
        self.recorder = recorder
        self.approval_manager = approval_manager
        self.audio_queue_input = audio_queue_input

        self._buffer: List[str] = []
        self._cursor_pos: int = 0
        self._history: List[str] = []
        self._history_idx: int = -1

    def _redraw_line(self):
        """Redibuja la línea de entrada actual y reposiciona el cursor exactamente."""
        try:
            sys.stdout.write("\r\033[K\033[36m│\033[0m ")
            content = "".join(self._buffer)
            sys.stdout.write(content)

            chars_from_end = len(self._buffer) - self._cursor_pos
            if chars_from_end > 0:
                sys.stdout.write(f"\033[{chars_from_end}D")
            sys.stdout.flush()
        except Exception:
            pass

    def _toggle_mute(self):
        """Alterna el estado del micrófono con sonido de confirmación."""
        if self.recorder:
            self.recorder.is_paused = not self.recorder.is_paused
            play_sound("pause" if self.recorder.is_paused else "resume")
            estado = "PAUSADO ⏸️" if self.recorder.is_paused else "REANUDADO ▶️"
            print(f"\r\033[36m│\033[0m \033[33m[🎤 {estado}]\033[0m Micrófono {'desactivado' if self.recorder.is_paused else 'activado'}.\n\033[36m│\033[0m ", end="", flush=True)
            self._redraw_line()

    def _adjust_sensitivity(self, delta: int):
        """Ajusta el umbral de silencio del VAD."""
        if self.recorder:
            if self.recorder.silence_threshold is None:
                self.recorder.silence_threshold = 12000
            self.recorder.silence_threshold = max(500, self.recorder.silence_threshold + delta)
            print(f"\r\033[36m│\033[0m \033[35m[🎤 UMBRAL VAD]\033[0m Ajustado a: {self.recorder.silence_threshold}\n\033[36m│\033[0m ", end="", flush=True)
            self._redraw_line()

    async def _handle_enter(self):
        """Procesa el fin de línea al presionar Enter."""
        line = "".join(self._buffer).strip()
        self._buffer.clear()
        self._cursor_pos = 0
        self._history_idx = -1

        # 1. Verificar si hay autorización HITL pendiente
        if self.approval_manager and self.approval_manager.list_pending():
            lower = line.lower()
            if lower in ("", "y", "s", "si", "sí", "apruebo", "a", "1", "ok"):
                sys.stdout.write(f"\r\033[K\033[36m│\033[0m \033[32m[🛡️ APROBADO]\033[0m Autorización concedida.\n")
                sys.stdout.flush()
                resolved_id = self.approval_manager.resolve_latest(True, resolver="terminal")
                if resolved_id:
                    return
            elif lower in ("n", "no", "rechazar", "rechazo", "cancelar", "0", "c"):
                sys.stdout.write(f"\r\033[K\033[36m│\033[0m \033[31m[🛡️ RECHAZADO]\033[0m Autorización denegada.\n")
                sys.stdout.flush()
                resolved_id = self.approval_manager.resolve_latest(False, resolver="terminal")
                if resolved_id:
                    return

        # 2. Comandos slash rápidos
        if line.startswith("/"):
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            cmd = line.lower().strip()
            if cmd in ("/mute", "/pausa", "/m"):
                self._toggle_mute()
                return
            elif cmd.startswith("/sensibilidad"):
                if "+" in cmd or "subir" in cmd:
                    self._adjust_sensitivity(500)
                elif "-" in cmd or "bajar" in cmd:
                    self._adjust_sensitivity(-500)
                return
            elif cmd in ("/clear", "/limpiar", "/cls"):
                os.system("clear")
                if self.assistant and self.assistant.event_bus:
                    from src.events.base import SessionStarted, ConversationContext
                    self.assistant.event_bus.publish(SessionStarted(ConversationContext()))
                return
            elif cmd in ("/help", "/ayuda", "/?"):
                print(f"\n\033[36m┌── 📖 [Comandos de Terminal y Atajos] ────────────────────────┐\033[0m")
                print(f"\033[36m│\033[0m \033[1mTab / Shift+Espacio / Ctrl+Espacio\033[0m: Silenciar / Reanudar Micrófono")
                print(f"\033[36m│\033[0m \033[1m← / →\033[0m                              : Mover cursor y editar texto en línea")
                print(f"\033[36m│\033[0m \033[1m↑ / ↓\033[0m                              : Navegar historial de prompts")
                print(f"\033[36m│\033[0m \033[1mCtrl+Arriba / Ctrl+Abajo\033[0m           : Ajustar umbral de sensibilidad")
                print(f"\033[36m│\033[0m \033[1m[Enter] en autorización\033[0m            : Aprobar acción pendiente")
                print(f"\033[36m│\033[0m \033[1mEscribir texto + [Enter]\033[0m           : Enviar prompt escrito a Atlas")
                print(f"\033[36m│\033[0m \033[1m/mute, /clear, /help\033[0m               : Comandos rápidos de control")
                print(f"\033[36m└────────────────────────────────────────────────────────┘\033[0m\n\033[36m│\033[0m ", end="", flush=True)
                return

        # 3. Enviar prompt de texto a Atlas
        if line:
            if not self._history or self._history[-1] != line:
                self._history.append(line)
            # Reemplazar la línea tipeada en el mismo lugar sin duplicar renglón
            sys.stdout.write(f"\r\033[K\033[36m│\033[0m \033[1m👤 Tú:\033[0m {line}\n")
            sys.stdout.flush()
            if self.assistant:
                await self.assistant.send_text_message(line)
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()

    async def _process_chunk(self, raw: bytes):
        """Procesa un fragmento de bytes leído de stdin en modo cbreak."""
        if not raw:
            return

        # 1. Atajos de Mute directos (Tab o Ctrl+Space)
        if raw in (b'\t', b'\x00'):
            self._toggle_mute()
            return

        # 2. Secuencias de Escape ANSI
        if raw.startswith(b'\x1b'):
            # Flecha Izquierda (←): b'\x1b[D' o b'\x1bOD'
            if raw in (b'\x1b[D', b'\x1bOD'):
                if self._cursor_pos > 0:
                    self._cursor_pos -= 1
                    self._redraw_line()
                return

            # Flecha Derecha (→): b'\x1b[C' o b'\x1bOC'
            elif raw in (b'\x1b[C', b'\x1bOC'):
                if self._cursor_pos < len(self._buffer):
                    self._cursor_pos += 1
                    self._redraw_line()
                return

            # Flecha Arriba (↑): Historial previo o Ctrl+Up sensibilidad
            elif raw in (b'\x1b[1;5A', b'\x1b[5A'):
                self._adjust_sensitivity(500)
                return
            elif raw in (b'\x1b[A', b'\x1bOA'):
                if self._history:
                    if self._history_idx == -1:
                        self._history_idx = len(self._history) - 1
                    elif self._history_idx > 0:
                        self._history_idx -= 1
                    self._buffer = list(self._history[self._history_idx])
                    self._cursor_pos = len(self._buffer)
                    self._redraw_line()
                return

            # Flecha Abajo (↓): Historial siguiente o Ctrl+Down sensibilidad
            elif raw in (b'\x1b[1;5B', b'\x1b[5B'):
                self._adjust_sensitivity(-500)
                return
            elif raw in (b'\x1b[B', b'\x1bOB'):
                if self._history and self._history_idx != -1:
                    if self._history_idx < len(self._history) - 1:
                        self._history_idx += 1
                        self._buffer = list(self._history[self._history_idx])
                    else:
                        self._history_idx = -1
                        self._buffer = []
                    self._cursor_pos = len(self._buffer)
                    self._redraw_line()
                return

            # Tecla Inicio (Home)
            elif raw in (b'\x1b[H', b'\x1b[1~', b'\x1b[7~', b'\x1bOH'):
                self._cursor_pos = 0
                self._redraw_line()
                return

            # Tecla Fin (End)
            elif raw in (b'\x1b[F', b'\x1b[4~', b'\x1b[8~', b'\x1bOF'):
                self._cursor_pos = len(self._buffer)
                self._redraw_line()
                return

            # Tecla Supr (Delete)
            elif raw in (b'\x1b[3~',):
                if self._cursor_pos < len(self._buffer):
                    self._buffer.pop(self._cursor_pos)
                    self._redraw_line()
                return

            # Shift+Space / Modificadores extendidos
            elif raw in (b'\x1b[27;2;32~', b'\x1b[32;2u', b'\x1b[32;2~', b'\x1b[200~', b' \x1b[200~'):
                self._toggle_mute()
                return

            # Tecla F2 (Mute alternativo)
            elif raw in (b'\x1bOQ', b'\x1b[12~', b'\x1b[OQ'):
                self._toggle_mute()
                return

            return

        # 3. Enter (\r o \n)
        if raw in (b'\r', b'\n'):
            await self._handle_enter()
            return

        # 4. Backspace (\x7f o \x08)
        if raw in (b'\x7f', b'\x08'):
            if self._cursor_pos > 0:
                self._buffer.pop(self._cursor_pos - 1)
                self._cursor_pos -= 1
                self._redraw_line()
            return

        # 5. Ctrl+C
        if raw == b'\x03':
            sys.exit(0)

        # 6. Caracteres de texto estándar
        try:
            text = raw.decode('utf-8', errors='ignore')
            for ch in text:
                if ch >= ' ':
                    if self._cursor_pos == len(self._buffer):
                        self._buffer.append(ch)
                        self._cursor_pos += 1
                        sys.stdout.write(ch)
                        sys.stdout.flush()
                    else:
                        self._buffer.insert(self._cursor_pos, ch)
                        self._cursor_pos += 1
                        self._redraw_line()
        except Exception:
            pass

    async def listen(self):
        """Bucle asíncrono no bloqueante de captura y decodificación de teclado."""
        if not sys.stdin.isatty():
            return

        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        # Habilitar reporting de modificadores extendidos (modifyOtherKeys)
        try:
            sys.stdout.write("\033[>4;2m")
            sys.stdout.flush()
        except Exception:
            pass

        # Establecer modo cbreak continuo durante toda la sesión
        tty.setcbreak(fd)

        try:
            def read_chunk():
                return os.read(fd, 64)

            while True:
                raw_bytes = await loop.run_in_executor(None, read_chunk)
                if not raw_bytes:
                    break
                await self._process_chunk(raw_bytes)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Terminal listen loop finalizado: {e}")
        finally:
            try:
                sys.stdout.write("\033[>4;0m")  # Restaurar modificadores normales
                sys.stdout.flush()
            except Exception:
                pass
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
