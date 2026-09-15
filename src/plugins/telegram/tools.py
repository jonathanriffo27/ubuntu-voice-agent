import asyncio
import os
import shutil
import time
from typing import Dict, Any, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.utils.a11y import AccessibilitySensor
from src.utils.logging import get_logger

logger = get_logger("plugins.telegram")

# Rutas conocidas de Telegram Desktop
_TELEGRAM_BINS = [
    os.path.expanduser("~/Applications/Telegram/Telegram"),
    "/usr/bin/telegram-desktop",
    "/usr/bin/telegram",
    "/opt/telegram/Telegram",
]

_TELEGRAM_SEARCH_PATHS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
]

# Códigos de teclas Linux (input-event-codes.h)
KEY_ESC = "1"
KEY_ENTER = "28"
KEY_CTRL = "29"
KEY_F = "33"
KEY_K = "37"
KEY_V = "47"
KEY_DOWN = "108"
KEY_SUPER = "125"


def _find_telegram_desktop() -> Optional[str]:
    """Busca el archivo .desktop o ejecutable de Telegram Desktop en el sistema."""
    for search_dir in _TELEGRAM_SEARCH_PATHS:
        if not os.path.isdir(search_dir):
            continue
        for fname in os.listdir(search_dir):
            if "telegram" in fname.lower() and fname.endswith(".desktop"):
                return os.path.join(search_dir, fname)
    for bpath in _TELEGRAM_BINS:
        if os.path.isfile(bpath) and os.access(bpath, os.X_OK):
            return bpath
    return None


def _get_ydotool_env() -> Dict[str, str]:
    """Prepara el entorno con el socket de ydotoold."""
    env = os.environ.copy()
    uid = os.getuid()
    env["YDOTOOL_SOCKET"] = f"/run/user/{uid}/.ydotool_socket"
    return env


async def _emit_keys(*key_combos: str) -> bool:
    """Emite secuencias de teclas usando ydotool."""
    env = _get_ydotool_env()
    args = ["ydotool", "key"]
    for combo in key_combos:
        args.extend(combo.split())
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Error ejecutando ydotool key: {e}")
        return False


async def _type_text(text: str) -> bool:
    """Escribe texto simulando pulsaciones con ydotool."""
    env = _get_ydotool_env()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ydotool", "type", "-d", "15", text,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Error ejecutando ydotool type: {e}")
        return False


async def _copy_to_clipboard(text: str) -> bool:
    """Copia texto al portapapeles de Wayland (wl-copy) o X11 (xclip)."""
    try:
        if shutil.which("wl-copy"):
            proc = await asyncio.create_subprocess_exec(
                "wl-copy", text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            return proc.returncode == 0
        elif shutil.which("xclip"):
            proc = await asyncio.create_subprocess_exec(
                "xclip", "-selection", "clipboard",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate(input=text.encode("utf-8"))
            return proc.returncode == 0
    except Exception as e:
        logger.error(f"Error copiando al portapapeles: {e}")
    return False


async def _launch_or_focus_telegram(desktop_path: Optional[str] = None) -> bool:
    """Lanza o enfoca la aplicación Telegram Desktop asegurando que esté en primer plano."""
    # 1. Si se detectó archivo .desktop y gtk-launch está disponible
    if desktop_path and desktop_path.endswith(".desktop") and shutil.which("gtk-launch"):
        desktop_id = os.path.splitext(os.path.basename(desktop_path))[0]
        try:
            proc = await asyncio.create_subprocess_exec(
                "gtk-launch", desktop_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except Exception as e:
            logger.debug(f"gtk-launch con {desktop_id}: {e}")
    elif desktop_path and os.path.isfile(desktop_path):
        # Lanzar binario directamente
        try:
            await asyncio.create_subprocess_exec(
                desktop_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
        except Exception as e:
            logger.debug(f"Ejecución directa de Telegram: {e}")

    # 2. Enfoque garantizado en GNOME Shell mediante atajo Super + 'telegram'
    await _emit_keys(f"{KEY_SUPER}:1 {KEY_SUPER}:0")
    await asyncio.sleep(0.4)
    await _type_text("telegram")
    await asyncio.sleep(0.4)
    await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
    await asyncio.sleep(0.8)
    return True


class EnviarTelegramTool(BaseTool):
    """Envía un mensaje por Telegram usando la app de escritorio instalada del usuario."""

    def __init__(self, a11y_sensor: Optional[AccessibilitySensor] = None):
        self.a11y_sensor = a11y_sensor or AccessibilitySensor()

    @property
    def name(self) -> str:
        return "enviar_telegram"

    @property
    def description(self) -> str:
        return (
            "Envía un mensaje de texto por Telegram usando la aplicación de escritorio "
            "instalada del usuario (sin necesidad de API keys). Puede enviar a 'mensajes guardados', "
            "a un contacto por nombre o @username, o al chat que indiques. "
            "Úsalo cuando el usuario pida enviar un mensaje por Telegram."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "mensaje": {
                    "type": "STRING",
                    "description": "El texto del mensaje a enviar."
                },
                "destino": {
                    "type": "STRING",
                    "description": (
                        "A quién enviar: 'mensajes guardados' (Saved Messages), "
                        "'@username' de un contacto, o el nombre del chat. "
                        "Por defecto: 'mensajes guardados'."
                    )
                }
            },
            "required": ["mensaje"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        mensaje = kwargs.get("mensaje", "").strip()
        destino = kwargs.get("destino", "mensajes guardados").strip()

        if not mensaje:
            return ToolResult(success=False, content="Falta el mensaje a enviar.")

        # 1. Comprobar que ydotool está disponible
        if not shutil.which("ydotool"):
            return ToolResult(
                success=False,
                content="Se necesita 'ydotool' para interactuar con la app de Telegram. Instálalo con: sudo apt install ydotool"
            )

        # 2. Comprobar que Telegram está instalado
        desktop_path = _find_telegram_desktop()
        if not desktop_path and not shutil.which("telegram-desktop"):
            return ToolResult(
                success=False,
                content="No se encontró la aplicación de escritorio de Telegram instalada en el sistema."
            )

        is_saved = destino.lower() in ("mensajes guardados", "saved messages", "guardados", "me", "self")

        logger.info(f"Iniciando envío por Telegram a '{destino}': \"{mensaje[:50]}...\"")

        try:
            # 1. Lanzar o enfocar Telegram Desktop
            await _launch_or_focus_telegram(desktop_path)

            # 2. Espera reactiva con AT-SPI2 / D-Bus hasta que la app esté cargada y activa
            ready, detail = self.a11y_sensor.wait_for_telegram_ready(timeout=8.0)
            if not ready:
                logger.warning(f"Telegram no confirmó apertura reactiva: {detail}. Continuando con precaución.")
            await asyncio.sleep(0.5)

            # 3. Limpiar cualquier menú o diálogo previo con Escape
            await _emit_keys(f"{KEY_ESC}:1 {KEY_ESC}:0")
            await asyncio.sleep(0.2)

            # 4. Abrir la barra de búsqueda con Ctrl+K (atajo universal en Telegram Desktop)
            await _emit_keys(f"{KEY_CTRL}:1 {KEY_K}:1 {KEY_K}:0 {KEY_CTRL}:0")
            await asyncio.sleep(0.4)

            # 5. Escribir el destinatario o chat
            busqueda = "Mensajes guardados" if is_saved else destino.lstrip("@").strip()
            await _type_text(busqueda)
            # Esperar a que Telegram complete la búsqueda local y de red
            await asyncio.sleep(0.8)

            # 6. Seleccionar el primer resultado y abrir el chat: Down + Enter
            await _emit_keys(f"{KEY_DOWN}:1 {KEY_DOWN}:0")
            await asyncio.sleep(0.3)
            await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
            await asyncio.sleep(0.6)

            # 7. Copiar mensaje al portapapeles y pegar con Ctrl+V (soporta acentos, emojis, saltos de línea)
            await _copy_to_clipboard(mensaje)
            await asyncio.sleep(0.2)
            await _emit_keys(f"{KEY_CTRL}:1 {KEY_V}:1 {KEY_V}:0 {KEY_CTRL}:0")
            await asyncio.sleep(0.3)

            # 8. Presionar Enter para enviar el mensaje
            await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
            await asyncio.sleep(0.4)

            logger.info(f"✅ Mensaje enviado a '{destino}' por Telegram exitosamente.")
            return ToolResult(
                success=True,
                content=f"Mensaje enviado exitosamente por Telegram a '{destino}': \"{mensaje}\""
            )

        except Exception as e:
            logger.error(f"Error enviando mensaje por Telegram: {e}")
            return ToolResult(
                success=False,
                content=f"Error al enviar mensaje por Telegram: {str(e)}"
            )
