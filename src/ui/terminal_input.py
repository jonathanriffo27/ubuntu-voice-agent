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
        self._last_cursor_row: int = 0
        self._last_rendered_rows: int = 1

    def _clear_input_area(self):
        """Limpia completamente todas las filas ocupadas por el buffer de entrada actual."""
        try:
            if self._last_cursor_row > 0:
                sys.stdout.write(f"\033[{self._last_cursor_row}A")
            sys.stdout.write("\r")
            for r in range(self._last_rendered_rows):
                sys.stdout.write("\033[2K")
                if r < self._last_rendered_rows - 1:
                    sys.stdout.write("\033[1B")
            if self._last_rendered_rows > 1:
                sys.stdout.write(f"\033[{self._last_rendered_rows - 1}A")
            sys.stdout.write("\r")
            sys.stdout.flush()
        except Exception:
            pass
        self._last_cursor_row = 0
        self._last_rendered_rows = 1

    def _redraw_line(self):
        """Redibuja la línea de entrada actual y reposiciona el cursor exactamente."""
        try:
            import shutil
            cols = shutil.get_terminal_size((80, 24)).columns
            if cols < 10:
                cols = 80

            # 1. Limpiar completamente lo renderizado anteriormente desde la fila 0
            self._clear_input_area()

            prompt_prefix = "\033[36m│\033[0m "
            prefix_len = 2
            content = "".join(self._buffer)

            # 2. Escribir el prompt completo
            sys.stdout.write(f"{prompt_prefix}{content}")

            total_chars = prefix_len + len(self._buffer)
            if total_chars == 0:
                last_char_row = 0
            else:
                last_char_row = (total_chars - 1) // cols

            # 3. Calcular posición deseada del cursor (0-indexed relativo al inicio del prompt)
            cursor_idx = prefix_len + self._cursor_pos
            target_row = cursor_idx // cols
            target_col = cursor_idx % cols

            # 4. Ajustar fila si es necesario
            if target_row < last_char_row:
                rows_up = last_char_row - target_row
                sys.stdout.write(f"\033[{rows_up}A")
            elif target_row > last_char_row:
                rows_down = target_row - last_char_row
                sys.stdout.write(f"\033[{rows_down}B")

            # 5. Ajustar columna (ANSI CHA es 1-indexed)
            sys.stdout.write(f"\033[{target_col + 1}G")

            self._last_cursor_row = target_row
            self._last_rendered_rows = max(last_char_row + 1, target_row + 1)
            sys.stdout.flush()
        except Exception:
            pass

    def _toggle_mute(self):
        """Alterna el estado del micrófono con sonido de confirmación."""
        if self.recorder:
            self._clear_input_area()
            self.recorder.is_paused = not self.recorder.is_paused
            play_sound("pause" if self.recorder.is_paused else "resume")
            estado = "PAUSADO ⏸️" if self.recorder.is_paused else "REANUDADO ▶️"
            print(f"\033[36m│\033[0m \033[33m[🎤 {estado}]\033[0m Micrófono {'desactivado' if self.recorder.is_paused else 'activado'}.")
            self._redraw_line()

    def _adjust_sensitivity(self, delta: int):
        """Ajusta el umbral de silencio del VAD."""
        if self.recorder:
            self._clear_input_area()
            if self.recorder.silence_threshold is None:
                self.recorder.silence_threshold = 12000
            self.recorder.silence_threshold = max(500, self.recorder.silence_threshold + delta)
            print(f"\033[36m│\033[0m \033[35m[🎤 UMBRAL VAD]\033[0m Ajustado a: {self.recorder.silence_threshold}")
            self._redraw_line()

    async def _handle_enter(self):
        """Procesa el fin de línea al presionar Enter."""
        line = "".join(self._buffer).strip()
        self._clear_input_area()
        self._buffer.clear()
        self._cursor_pos = 0
        self._history_idx = -1

        # 1. Verificar si hay autorización HITL pendiente
        if self.approval_manager and self.approval_manager.list_pending():
            lower = line.lower()
            if lower in ("", "y", "s", "si", "sí", "apruebo", "a", "1", "ok"):
                sys.stdout.write(f"\033[36m│\033[0m \033[32m[🛡️ APROBADO]\033[0m Autorización concedida.\n")
                sys.stdout.flush()
                resolved_id = self.approval_manager.resolve_latest(True, resolver="terminal")
                if resolved_id:
                    return
            elif lower in ("n", "no", "rechazar", "rechazo", "cancelar", "0", "c"):
                sys.stdout.write(f"\033[36m│\033[0m \033[31m[🛡️ RECHAZADO]\033[0m Autorización denegada.\n")
                sys.stdout.flush()
                resolved_id = self.approval_manager.resolve_latest(False, resolver="terminal")
                if resolved_id:
                    return

        # 2. Comandos slash rápidos
        if line.startswith("/"):
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
            elif cmd in ("/mode", "/modo"):
                if self.recorder and hasattr(self.recorder, '_voice_config'):
                    vc = self.recorder._voice_config
                    from src.voice.recorder import RecorderState
                    if vc:
                        old = getattr(vc, 'mode', 'always_on')
                        vc.mode = "always_on" if old == "wake_word" else "wake_word"
                        if vc.mode == "wake_word":
                            self.recorder._state = RecorderState.STANDBY
                            ww_name = getattr(vc, 'wake_word', 'alexa').capitalize()
                            estado_msg = f"Wake Word (en espera de '{ww_name}')"
                        else:
                            self.recorder._state = RecorderState.ACTIVE
                            estado_msg = "Always-On (micrófono siempre activo)"
                        print(f"\033[36m│\033[0m \033[35m[🔄 MODO DE VOZ]\033[0m Cambiado a: {estado_msg}")
                    else:
                        print(f"\033[36m│\033[0m \033[33m[MODO]\033[0m Configuración de voz no disponible.")
                self._redraw_line()
                return
            elif cmd.startswith("/umbral"):
                if self.recorder and hasattr(self.recorder, '_voice_config') and self.recorder._voice_config:
                    vc = self.recorder._voice_config
                    parts = cmd.split()
                    if len(parts) > 1:
                        val_str = parts[1]
                        if val_str in ("+", "subir", "up"):
                            vc.wake_word_threshold = min(0.95, round(vc.wake_word_threshold + 0.05, 2))
                        elif val_str in ("-", "bajar", "down"):
                            vc.wake_word_threshold = max(0.10, round(vc.wake_word_threshold - 0.05, 2))
                        else:
                            try:
                                new_val = float(val_str)
                                vc.wake_word_threshold = max(0.05, min(0.95, round(new_val, 2)))
                            except ValueError:
                                pass
                    print(f"\033[36m│\033[0m \033[35m[🎚️ UMBRAL WAKE WORD]\033[0m Configurado a: {vc.wake_word_threshold:.2f} (rango: 0.10 a 0.90)")
                else:
                    print(f"\033[36m│\033[0m \033[33m[UMBRAL]\033[0m Configuración de voz no disponible.")
                self._redraw_line()
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
                print(f"\033[36m│\033[0m \033[1mCtrl+Arriba / Ctrl+Abajo\033[0m           : Ajustar umbral de silencio VAD")
                print(f"\033[36m│\033[0m \033[1m[Enter] en autorización\033[0m            : Aprobar acción pendiente")
                print(f"\033[36m│\033[0m \033[1mEscribir texto + [Enter]\033[0m           : Enviar prompt escrito a Atlas")
                print(f"\033[36m│\033[0m \033[1m/mode\033[0m                              : Alternar modo (Wake Word / Always-On)")
                print(f"\033[36m│\033[0m \033[1m/umbral <valor>\033[0m                     : Ajustar sensibilidad Wake Word (ej: /umbral 0.35)")
                print(f"\033[36m│\033[0m \033[1m/mute, /clear, /help\033[0m               : Comandos rápidos de control")
                print(f"\033[36m└────────────────────────────────────────────────────────┘\033[0m\n\033[36m│\033[0m ", end="", flush=True)
                return

        # 3. Enviar prompt de texto a Atlas
        if line:
            if not self._history or self._history[-1] != line:
                self._history.append(line)
            # Reemplazar la línea tipeada en el mismo lugar sin duplicar renglón
            sys.stdout.write(f"\033[36m│\033[0m \033[1m👤 Tú:\033[0m {line}\n")
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

        # 5. Ctrl+C: eleva SIGINT en el hilo principal para que el runtime
        # ejecute la rutina de apagado limpio (micrófono, HUD, MCP, túnel SSH).
        import signal
        if raw == b'\x03':
            os.kill(os.getpid(), signal.SIGINT)

        # 6. Caracteres de texto estándar
        try:
            text = raw.decode('utf-8', errors='ignore')
            changed = False
            for ch in text:
                if ch >= ' ':
                    if self._cursor_pos == len(self._buffer):
                        self._buffer.append(ch)
                    else:
                        self._buffer.insert(self._cursor_pos, ch)
                    self._cursor_pos += 1
                    changed = True
            if changed:
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
