import asyncio
import os
import sys
import termios
import tty
from typing import Optional, List
from src.voice.player import play_sound
from src.utils.logging import get_logger

logger = get_logger("ui.terminal_input")


class TerminalInteractionManager:
    """
    Gestor unificado de interacción por teclado en la terminal de Atlas.
    Permite:
    1. Escribir prompts largos de múltiples líneas sin duplicación ni desbordamiento:
       - Escritura directa y fluida con auto-wrap nativo de terminal.
       - Flechas (←, →, Home, End, Delete) para edición en línea.
       - Flechas (↑, ↓) para historial de prompts anteriores.
    2. Atajos directos para Mute / Pausa:
       - Shift+Espacio
       - Tab (acceso rápido de 1 tecla)
       - Ctrl+Espacio (código \x00)
       - F2 (fila superior)
    3. Atajos para Sensibilidad VAD:
       - Ctrl+Arriba / Ctrl+Abajo (+500 / -500).
    4. Aprobaciones HITL y Comandos Slash (/mute, /help, /clear).
    """

    def __init__(
        self,
        assistant,
        recorder=None,
        approval_manager=None,
        audio_queue_input: Optional[asyncio.Queue] = None
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
        """Redibuja la línea de entrada actual de forma segura sin romper el auto-wrap de pantalla."""
        try:
            cols = os.get_terminal_size().columns if sys.stdout.isatty() else 80
            prefix = "│ "
            total_len = len(prefix) + len(self._buffer)

            # Si el texto entra en una sola fila
            if total_len < cols:
                sys.stdout.write("\r\033[36m│\033[0m ")
                sys.stdout.write("".join(self._buffer))
                sys.stdout.write("\033[K")
                moves_back = len(self._buffer) - self._cursor_pos
                if moves_back > 0:
                    sys.stdout.write(f"\033[{moves_back}D")
                sys.stdout.flush()
            else:
                # En textos muy largos multilínea, emitir el buffer completo limpiamente
                sys.stdout.write("\r\033[K\033[36m│\033[0m ")
                sys.stdout.write("".join(self._buffer))
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
                resolved_id = self.approval_manager.resolve_latest(True, resolver="terminal")
                if resolved_id:
                    return
            elif lower in ("n", "no", "rechazar", "rechazo", "cancelar", "0", "c"):
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
            elif cmd in ("/clear", "/limpiar", "/cls"):
                os.system("clear")
                if self.assistant and self.assistant.event_bus:
                    from src.events.base import SessionStarted, ConversationContext
                    self.assistant.event_bus.publish(SessionStarted(ConversationContext()))
                return
            elif cmd in ("/help", "/ayuda", "/?"):
                print(f"\n\033[36m┌── 📖 [Comandos de Terminal y Atajos] ────────────────────────┐\033[0m")
                print(f"\033[36m│\033[0m \033[1mShift+Espacio / Tab / Ctrl+Espacio\033[0m : Silenciar / Reanudar Micrófono")
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
            print(f"\n\033[36m│\033[0m \033[1m👤 Tú:\033[0m {line}")
            await self.assistant.send_text_message(line)
        else:
            print()

    async def listen(self):
        """Bucle asíncrono no bloqueante de captura y decodificación de teclado."""
        if not sys.stdin.isatty():
            return

        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        # Habilitar reporting de modificadores extendidos (modifyOtherKeys para Shift+Space)
        try:
            sys.stdout.write("\033[>4;2m")
            sys.stdout.flush()
        except Exception:
            pass

        def getch_blocking():
            try:
                tty.setcbreak(fd)
                return sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        try:
            while True:
                ch = await loop.run_in_executor(None, getch_blocking)
                if not ch:
                    break

                # 1. Atajo rápido Mute: Ctrl+Espacio (\x00) o Tab (\t / \x09)
                if ch in ('\x00', '\t'):
                    self._toggle_mute()
                    continue

                # 2. Detectar secuencias de escape ANSI (Flechas, Modificadores, Navegación)
                if ch == '\x1b':
                    def read_seq():
                        tty.setcbreak(fd)
                        seq = ""
                        try:
                            import select
                            while select.select([sys.stdin], [], [], 0.03)[0]:
                                seq += sys.stdin.read(1)
                        finally:
                            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                        return seq

                    seq = await loop.run_in_executor(None, read_seq)

                    # Shift+Space / Modificadores extendidos de espacio
                    if seq in ("[27;2;32~", "[32;2u", "[32;2~", "[200~", " [200~"):
                        self._toggle_mute()
                        continue

                    # Tecla F2 (Mute rápido alternativo)
                    elif seq in ("OQ", "[12~", "[OQ"):
                        self._toggle_mute()
                        continue

                    # Flecha Izquierda (←): mover cursor atrás
                    elif seq in ("[D", "OD"):
                        if self._cursor_pos > 0:
                            self._cursor_pos -= 1
                            sys.stdout.write("\033[D")
                            sys.stdout.flush()
                        continue

                    # Flecha Derecha (→): mover cursor adelante
                    elif seq in ("[C", "OC"):
                        if self._cursor_pos < len(self._buffer):
                            self._cursor_pos += 1
                            sys.stdout.write("\033[C")
                            sys.stdout.flush()
                        continue

                    # Flecha Arriba (↑): Historial previo o Ctrl+Up sensibilidad
                    elif seq in ("[1;5A", "[5A"):
                        self._adjust_sensitivity(500)
                        continue
                    elif seq in ("[A", "OA"):
                        if self._history:
                            if self._history_idx == -1:
                                self._history_idx = len(self._history) - 1
                            elif self._history_idx > 0:
                                self._history_idx -= 1
                            self._buffer = list(self._history[self._history_idx])
                            self._cursor_pos = len(self._buffer)
                            self._redraw_line()
                        continue

                    # Flecha Abajo (↓): Historial siguiente o Ctrl+Down sensibilidad
                    elif seq in ("[1;5B", "[5B"):
                        self._adjust_sensitivity(-500)
                        continue
                    elif seq in ("[B", "OB"):
                        if self._history and self._history_idx != -1:
                            if self._history_idx < len(self._history) - 1:
                                self._history_idx += 1
                                self._buffer = list(self._history[self._history_idx])
                            else:
                                self._history_idx = -1
                                self._buffer = []
                            self._cursor_pos = len(self._buffer)
                            self._redraw_line()
                        continue

                    # Tecla Inicio (Home)
                    elif seq in ("[H", "[1~", "[7~", "OH"):
                        self._cursor_pos = 0
                        self._redraw_line()
                        continue

                    # Tecla Fin (End)
                    elif seq in ("[F", "[4~", "[8~", "OF"):
                        self._cursor_pos = len(self._buffer)
                        self._redraw_line()
                        continue

                    # Tecla Supr (Delete: \x1b[3~)
                    elif seq in ("[3~",):
                        if self._cursor_pos < len(self._buffer):
                            self._buffer.pop(self._cursor_pos)
                            self._redraw_line()
                        continue

                    continue

                # 3. Detectar Enter (\r o \n)
                if ch in ('\r', '\n'):
                    await self._handle_enter()
                    continue

                # 4. Detectar Backspace (\x7f o \x08)
                if ch in ('\x7f', '\x08'):
                    if self._cursor_pos > 0:
                        if self._cursor_pos == len(self._buffer):
                            self._buffer.pop()
                            self._cursor_pos -= 1
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                        else:
                            self._buffer.pop(self._cursor_pos - 1)
                            self._cursor_pos -= 1
                            self._redraw_line()
                    continue

                # 5. Detectar Ctrl+C / Ctrl+D
                if ch == '\x03':
                    sys.exit(0)

                # 6. Caracter estándar (insertar y emitir)
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

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"TerminalInteractionManager finalizado: {e}")
        finally:
            try:
                # Restaurar modo terminal normal
                sys.stdout.write("\033[>4;0m")
                sys.stdout.flush()
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
