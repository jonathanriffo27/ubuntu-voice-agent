"""
Backends de inyección de input para computer-use en Linux (Wayland/GNOME).

Cadena de determinismo (de mejor a peor):
  0. AT-SPI2 semántico (vive en src/utils/a11y.py, lo orquesta ElementResolver).
  1. YdotoolBackend         -> /dev/uinput vía ydotoold (probado en producción por Atlas).
  2. RemoteDesktopPortal    -> org.freedesktop.portal.RemoteDesktop (GNOME 45+), sin daemon.

Regla de oro: el texto largo/Unicode NUNCA se teclea tecla a tecla.
Se usa wl-copy + Ctrl+V (funciona idéntico en ambos backends).
"""
import os
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger("input.backends")

# Keycodes evdev (/usr/include/linux/input-event-codes.h) - compartidos por
# ydotool y por NotifyKeyboardKeycode del portal RemoteDesktop.
KEYCODES = {
    "esc": 1, "escape": 1, "enter": 28, "return": 28, "tab": 15, "space": 57,
    "backspace": 14, "delete": 111, "supr": 111,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "inicio": 102, "end": 107, "fin": 107,
    "pageup": 104, "re pag": 104, "pagedown": 109, "av pag": 109,
    "ctrl": 29, "control": 29, "alt": 56, "shift": 42, "super": 125, "win": 125,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44, "slash": 53,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}

# Botones de ratón (evdev BTN_*)
MOUSE_BUTTONS = {"left": 272, "right": 273, "middle": 274}


def parse_key_combo(combo: str) -> Optional[list]:
    """
    Convierte "ctrl+v" / "super" / "ctrl+alt+t" en lista de keycodes evdev
    [modificadores..., tecla]. Devuelve None si alguna tecla es desconocida.
    """
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    codes = []
    for p in parts:
        code = KEYCODES.get(p)
        if code is None:
            return None
        codes.append(code)
    return codes


def copy_to_clipboard(text: str) -> bool:
    """Copia texto al portapapeles de Wayland con wl-copy (sin bloquear en segundo plano)."""
    if not shutil.which("wl-copy"):
        logger.error("wl-copy no instalado: no se puede inyectar texto Unicode.")
        return False
    try:
        proc = subprocess.run(["wl-copy"], input=text.encode("utf-8"), timeout=4)
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"wl-copy falló: {e}")
        return False


def process_alive(name: str) -> bool:
    """
    True si hay un proceso vivo con ese nombre exacto (comm), leyendo /proc.
    Sin dependencias externas. Pensado para daemons de input como 'ydotoold'.
    """
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm", "r") as f:
                    if f.read().strip() == name:
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


class InputBackend(ABC):
    """Backend de inyección de input crudo (puntero/teclado) a nivel de sesión."""

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """True si el backend puede inyectar input ahora mismo (rápido, sin side-effects)."""

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        """Clic en coordenadas absolutas de pantalla."""

    @abstractmethod
    def press_key(self, combo: str) -> bool:
        """Pulsa una tecla o combinación: 'enter', 'ctrl+v', 'super', 'alt+tab'..."""

    def type_text(self, text: str) -> bool:
        """
        Escribe texto arbitrario (Unicode/acentos/emoji/multilínea) de forma segura:
        wl-copy + Ctrl+V. Idéntico en cualquier backend; solo cambia el combo de pegado.
        """
        if not text:
            return True
        if not copy_to_clipboard(text):
            return False
        time.sleep(0.12)  # asentar el portapapeles antes del paste
        return self.press_key("ctrl+v")


class YdotoolBackend(InputBackend):
    """Inyección vía ydotoold (/dev/uinput). Probada en producción por Atlas."""

    name = "ydotool"

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or f"/run/user/{os.getuid()}/.ydotool_socket"

    def _env(self) -> dict:
        env = os.environ.copy()
        env["YDOTOOL_SOCKET"] = self.socket_path
        return env

    def _run(self, args: list, timeout: float = 4.0) -> bool:
        try:
            proc = subprocess.run(["ydotool", *args], env=self._env(),
                                  capture_output=True, text=True, timeout=timeout)
            return proc.returncode == 0
        except Exception as e:
            logger.debug(f"ydotool {' '.join(args)} falló: {e}")
            return False

    def is_available(self) -> bool:
        # Socket presente Y daemon vivo. El socket de ydotoold es DGRAM (no se
        # puede sondear conectando); os.path.exists a solas da falsos positivos
        # con sockets obsoletos de un daemon muerto (detectado el 2026-09-14).
        return (
            shutil.which("ydotool") is not None
            and os.path.exists(self.socket_path)
            and process_alive("ydotoold")
        )

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        if not self._run(["mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))]):
            return False
        time.sleep(0.05)  # asentar el puntero antes del click
        btn_code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
        args = ["click"]
        if double:
            args += ["-r", "2", "-D", "50"]
        return self._run(args + [btn_code])

    def press_key(self, combo: str) -> bool:
        codes = parse_key_combo(combo)
        if not codes:
            logger.warning(f"Combinación de teclas desconocida para ydotool: '{combo}'")
            return False
        events = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
        return self._run(["key", *events])


class _PortalDBusAdapter:
    """
    Capa D-Bus real para org.freedesktop.portal.RemoteDesktop usando Gio (GLib).
    Mantiene un GLib.MainLoop en hilo daemon para recibir las señales Response
    del portal. Aislada del backend para poder inyectar un fake en tests.
    """

    PORTAL_NAME = "org.freedesktop.portal.Desktop"
    PORTAL_PATH = "/org/freedesktop/portal/desktop"
    RD_IFACE = "org.freedesktop.portal.RemoteDesktop"
    REQ_IFACE = "org.freedesktop.portal.Request"

    def __init__(self):
        from gi.repository import Gio, GLib  # gi ya es dependencia del proyecto (Atspi)
        self._Gio = Gio
        self._GLib = GLib
        self._conn = None
        self._loop = None
        self._responses = {}
        self._cond = threading.Condition()
        self._started = threading.Event()

    # ---- infraestructura --------------------------------------------------
    def start(self) -> bool:
        """Levanta el hilo GLib y crea la conexión al bus de sesión. Idempotente."""
        if self._conn is not None:
            return True
        self._loop = self._GLib.MainLoop()
        threading.Thread(target=self._loop_thread, daemon=True, name="atlas-portal-glib").start()
        return self._started.wait(timeout=5)

    def _loop_thread(self):
        ctx = self._loop.get_context()
        ctx.push_thread_default()
        try:
            self._conn = self._Gio.bus_get_sync(self._Gio.BusType.SESSION, None)
            self._conn.signal_subscribe(
                self.PORTAL_NAME, self.REQ_IFACE, "Response", None, None,
                self._Gio.DBusSignalFlags.NONE, self._on_response, None,
            )
        except Exception as e:
            logger.error(f"No se pudo conectar al bus de sesión para el portal: {e}")
        finally:
            self._started.set()
        self._loop.run()

    def _on_response(self, conn, sender, path, iface, signal, params, user_data):
        try:
            response_code, results = params.unpack()
        except Exception:
            return
        with self._cond:
            self._responses[path] = (int(response_code), dict(results or {}))
            self._cond.notify_all()

    # ---- empaquetado de variantes (D-Bus es estricto con los tipos) --------
    def _to_variant(self, value):
        V = self._GLib.Variant
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and value[0].isalpha() and len(value[0]) <= 2:
            # (firma, valor) explícito para tipos ambiguos, ej. ("u", 3)
            return V(value[0], value[1])
        if isinstance(value, dict):
            return V("a{sv}", {k: self._to_variant(v) for k, v in value.items()})
        if isinstance(value, bool):
            return V("b", value)
        if isinstance(value, str):
            return V("s", value)
        if isinstance(value, int):
            return V("i", value)
        if isinstance(value, float):
            return V("d", value)
        raise TypeError(f"Tipo no soportado para variante D-Bus: {type(value)!r}")

    # ---- API usada por el backend ------------------------------------------
    def call(self, method: str, *args) -> Optional[tuple]:
        """Llamada síncrona al portal. Devuelve la tupla de salida (vacía si void) o None."""
        try:
            children = [self._to_variant(a) for a in args]
            params = self._GLib.Variant.new_tuple(*children) if children else None
            res = self._conn.call_sync(
                self.PORTAL_NAME, self.PORTAL_PATH, self.RD_IFACE, method,
                params, None, self._Gio.DBusCallFlags.NONE, 5000, None,
            )
            return res.unpack()
        except Exception as e:
            logger.error(f"Portal {method} falló: {e}")
            return None

    def wait_response(self, request_path: str, timeout: float) -> Optional[dict]:
        """Bloquea hasta la señal Response del request y devuelve results.

        Devuelve un dict (posiblemente vacío) en éxito, o None si el usuario
        canceló el diálogo / hubo timeout. El backend trata None como fallo.
        """
        deadline = time.time() + timeout
        with self._cond:
            while time.time() < deadline:
                if request_path in self._responses:
                    code, results = self._responses.pop(request_path)
                    if code != 0:
                        logger.info("El usuario canceló (o falló) el diálogo del portal.")
                        return None
                    return results
                self._cond.wait(timeout=0.1)
        logger.warning(f"Timeout esperando respuesta del portal para {request_path}")
        return None


class RemoteDesktopPortalBackend(InputBackend):
    """
    Inyección de input vía XDG Portal RemoteDesktop (GNOME 45+), sin daemon
    ni permisos sobre /dev/uinput. La primera vez, GNOME muestra UN diálogo de
    autorización ("Permitir control remoto"); el restore_token se persiste para
    no volver a preguntar.

    EXPERIMENTAL: validar en vivo antes de promoverlo a backend primario.
    """

    name = "portal"

    DEVICE_KEYBOARD = 1
    DEVICE_POINTER = 2

    def __init__(self, dbus_adapter=None, token_store_path: Optional[str] = None,
                 dialog_timeout: float = 90.0):
        self._adapter = dbus_adapter  # None -> se crea el real (Gio) bajo demanda
        self._session_handle: Optional[str] = None
        self._dialog_timeout = dialog_timeout
        self._token_path = token_store_path or os.path.expanduser("~/.config/atlas/portal_restore_token")

    # ---- ciclo de vida -----------------------------------------------------
    def is_available(self) -> bool:
        """El portal existe en el bus; la sesión se establece bajo demanda (lazy)."""
        if self._session_handle:
            return True
        try:
            proc = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                 "--object-path", "/org/freedesktop/DBus",
                 "--method", "org.freedesktop.DBus.NameHasOwner",
                 "org.freedesktop.portal.Desktop"],
                capture_output=True, text=True, timeout=3)
            return proc.returncode == 0 and "true" in proc.stdout
        except Exception:
            return False

    def _get_adapter(self):
        if self._adapter is None:
            self._adapter = _PortalDBusAdapter()
            if not self._adapter.start():
                raise RuntimeError("No se pudo iniciar la conexión D-Bus para el portal.")
        return self._adapter

    def _load_token(self) -> Optional[str]:
        try:
            with open(self._token_path, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _save_token(self, token: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
            with open(self._token_path, "w", encoding="utf-8") as f:
                f.write(token)
            os.chmod(self._token_path, 0o600)
        except OSError as e:
            logger.debug(f"No se pudo persistir el token del portal: {e}")

    def ensure_session(self) -> bool:
        """Establece la sesión RemoteDesktop (la 1ª vez con diálogo del sistema)."""
        if self._session_handle:
            return True
        adapter = self._get_adapter()
        token = self._load_token()

        import secrets
        suffix = secrets.token_hex(4)

        # 1) CreateSession
        res = adapter.call("CreateSession", {
            "handle_token": f"atlas_{suffix}",
            "session_handle_token": f"atlas_sess_{suffix}",
        })
        if not res:
            return False
        results = adapter.wait_response(res[0], timeout=10)
        if not results or "session_handle" not in results:
            return False
        self._session_handle = str(results["session_handle"])

        # 2) SelectDevices (teclado + puntero); con token previo pedimos persistencia
        opts = {"types": ("u", self.DEVICE_KEYBOARD | self.DEVICE_POINTER)}
        if token:
            opts["restore_token"] = token
            opts["persist_mode"] = ("u", 2)  # recordar para futuras sesiones
        res = adapter.call("SelectDevices", self._session_handle, opts)
        if not res or adapter.wait_response(res[0], timeout=10) is None:
            self._session_handle = None
            return False

        # 3) Start — puede mostrar el diálogo del sistema la primera vez
        if not token:
            logger.info("🛡️ El sistema mostrará un diálogo autorizando el control remoto de Atlas (solo la 1ª vez).")
        res = adapter.call("Start", self._session_handle, "", {})
        if not res:
            self._session_handle = None
            return False
        results = adapter.wait_response(res[0], timeout=self._dialog_timeout)
        if results is None:
            self._session_handle = None
            return False
        new_token = results.get("restore_token")
        if new_token:
            self._save_token(str(new_token))
        logger.info("✅ Sesión RemoteDesktop establecida (inyección de input por portal).")
        return True

    # ---- input -------------------------------------------------------------
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        if not self.ensure_session():
            return False
        adapter = self._get_adapter()
        btn = MOUSE_BUTTONS.get(button, 272)
        # stream "" = sin screencast asociado (sesión input-only)
        if adapter.call("NotifyPointerMotionAbsolute",
                        self._session_handle, {}, "", float(x), float(y)) is None:
            return False
        time.sleep(0.05)
        clicks = 2 if double else 1
        for _ in range(clicks):
            ok = adapter.call("NotifyPointerButton", self._session_handle, {}, btn, ("u", 1)) is not None
            ok = adapter.call("NotifyPointerButton", self._session_handle, {}, btn, ("u", 0)) is not None and ok
            if not ok:
                return False
            if double:
                time.sleep(0.06)
        return True

    def press_key(self, combo: str) -> bool:
        if not self.ensure_session():
            return False
        codes = parse_key_combo(combo)
        if not codes:
            logger.warning(f"Combinación desconocida para portal: '{combo}'")
            return False
        adapter = self._get_adapter()
        try:
            for c in codes:  # presionar en orden (mods primero)
                if adapter.call("NotifyKeyboardKeycode", self._session_handle, {}, c, ("u", 1)) is None:
                    return False
            for c in reversed(codes):  # soltar en orden inverso
                if adapter.call("NotifyKeyboardKeycode", self._session_handle, {}, c, ("u", 0)) is None:
                    return False
            return True
        except Exception as e:
            logger.debug(f"press_key por portal falló: {e}")
            return False


class InputRouter:
    """
    Selecciona el primer backend crudo disponible y delega en él. Orden por
    defecto: ydotool (probado) -> portal RemoteDesktop (sin daemon).
    Las acciones semánticas AT-SPI2 NO pasan por aquí: las decide el
    ElementResolver antes de recurrir a coordenadas.
    """

    def __init__(self, backends: Optional[list] = None):
        self.backends = backends or [YdotoolBackend(), RemoteDesktopPortalBackend()]

    def active_backend(self) -> Optional[InputBackend]:
        for b in self.backends:
            try:
                if b.is_available():
                    return b
            except Exception:
                continue
        return None

    def _with_fallback(self, op_name: str, *args, **kwargs) -> Tuple[bool, str]:
        for b in self.backends:
            try:
                if not b.is_available():
                    continue
                if getattr(b, op_name)(*args, **kwargs):
                    return True, b.name
            except Exception as e:
                logger.debug(f"Backend {b.name} falló en {op_name}: {e}")
        logger.error(f"Ningún backend de input pudo ejecutar {op_name}.")
        return False, "none"

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> Tuple[bool, str]:
        return self._with_fallback("click", x, y, button=button, double=double)

    def press_key(self, combo: str) -> Tuple[bool, str]:
        return self._with_fallback("press_key", combo)

    def type_text(self, text: str) -> Tuple[bool, str]:
        return self._with_fallback("type_text", text)
