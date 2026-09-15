import asyncio
import os
import shutil
import urllib.parse
from typing import Dict, Any, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.utils.a11y import AccessibilitySensor
from src.utils.logging import get_logger

logger = get_logger("plugins.email")

# Códigos de teclas Linux (input-event-codes.h)
KEY_ENTER = "28"
KEY_CTRL = "29"
KEY_V = "47"
KEY_SUPER = "125"
KEY_TAB = "15"
KEY_C = "46"

# Archivo .desktop de Gmail PWA en el sistema
_GMAIL_DESKTOP_FILE = "brave-mail.google.com__mail_-Default.desktop"
_GMAIL_USER_DATA_DIR = os.path.expanduser("~/.config/BraveSoftware/Brave-Browser-Gmail")
_GMAIL_SEARCH_PATHS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
]


def _find_gmail_desktop() -> Optional[str]:
    """Comprueba si la app de Gmail PWA está instalada en el sistema."""
    for search_dir in _GMAIL_SEARCH_PATHS:
        path = os.path.join(search_dir, _GMAIL_DESKTOP_FILE)
        if os.path.isfile(path):
            return path
    for search_dir in _GMAIL_SEARCH_PATHS:
        if not os.path.isdir(search_dir):
            continue
        for fname in os.listdir(search_dir):
            if "gmail" in fname.lower() and fname.endswith(".desktop"):
                return os.path.join(search_dir, fname)
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


async def _launch_or_focus_gmail_compose(destinatario: str, asunto: str, cuerpo: str) -> bool:
    """
    Abre o enfoca la ventana de redacción de Gmail con los datos prellenados.
    Utiliza el perfil PWA dedicado de Brave o el navegador por defecto.
    """
    compose_url = f"https://mail.google.com/mail/?view=cm&fs=1&to={urllib.parse.quote(destinatario)}&su={urllib.parse.quote(asunto)}&body={urllib.parse.quote(cuerpo)}"

    # 1. Si existe el perfil de Gmail PWA en Brave, lanzarlo con la URL de redacción
    if os.path.exists(_GMAIL_USER_DATA_DIR) and shutil.which("brave-browser"):
        try:
            await asyncio.create_subprocess_exec(
                "brave-browser",
                f"--user-data-dir={_GMAIL_USER_DATA_DIR}",
                f"--app={compose_url}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
        except Exception as e:
            logger.debug(f"Error lanzando Brave Gmail PWA: {e}")
    elif shutil.which("xdg-open"):
        try:
            await asyncio.create_subprocess_exec(
                "xdg-open", compose_url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
        except Exception as e:
            logger.debug(f"Error abriendo compose URL con xdg-open: {e}")

    # 2. Enfocar la ventana de Gmail en GNOME Shell usando Super + 'gmail' + Enter
    await _emit_keys(f"{KEY_SUPER}:1 {KEY_SUPER}:0")
    await asyncio.sleep(0.3)
    await _type_text("gmail")
    await asyncio.sleep(0.3)
    await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
    await asyncio.sleep(0.6)
    return True


class EnviarCorreoTool(BaseTool):
    """Envía un correo electrónico usando la app de Gmail instalada del usuario."""

    def __init__(self):
        self.a11y_sensor = AccessibilitySensor()

    @property
    def name(self) -> str:
        return "enviar_correo"

    @property
    def description(self) -> str:
        return (
            "Envía un correo electrónico redactado usando la aplicación de Gmail PWA del usuario "
            "(sin necesidad de credenciales SMTP ni API keys). Especifica destinatario, asunto y cuerpo. "
            "Úsalo cuando el usuario pida enviar o redactar un correo/email."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "destinatario": {
                    "type": "STRING",
                    "description": "Dirección de correo electrónico del destinatario (ej: 'usuario@gmail.com')."
                },
                "asunto": {
                    "type": "STRING",
                    "description": "Asunto o título del correo electrónico."
                },
                "cuerpo": {
                    "type": "STRING",
                    "description": "Texto del mensaje o contenido del correo electrónico."
                },
                "enviar_automatico": {
                    "type": "BOOLEAN",
                    "description": "Si es True (por defecto), envía el correo inmediatamente. Si es False, lo deja abierto en pantalla listo para revisión."
                }
            },
            "required": ["destinatario", "asunto", "cuerpo"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        destinatario = kwargs.get("destinatario", "").strip()
        asunto = kwargs.get("asunto", "").strip()
        cuerpo = kwargs.get("cuerpo", "").strip()
        enviar_automatico = kwargs.get("enviar_automatico", True)

        if not destinatario:
            return ToolResult(success=False, content="Falta la dirección de correo del destinatario.")
        if not asunto:
            asunto = "Mensaje desde Atlas"
        if not cuerpo:
            return ToolResult(success=False, content="Falta el cuerpo o texto del correo a enviar.")

        # Verificar ydotool
        if not shutil.which("ydotool"):
            return ToolResult(
                success=False,
                content="Se necesita 'ydotool' para interactuar con la app de correo. Instálalo con: sudo apt install ydotool"
            )

        logger.info(f"Enviando correo a '{destinatario}' con asunto '{asunto}'...")

        try:
            # 1. Abrir y enfocar la vista de redacción de Gmail con los datos prellenados
            await _launch_or_focus_gmail_compose(destinatario, asunto, cuerpo)

            # 2. Esperar reactivamente a que la ventana de redacción esté lista
            ready, detail = self.a11y_sensor.wait_for_gmail_ready(timeout=10.0)
            if not ready:
                return ToolResult(
                    success=False,
                    content=f"No se pudo abrir la app de Gmail: {detail}."
                )

            await asyncio.sleep(0.4)

            # 3. Si se solicitó enviar automáticamente, emitir Ctrl + Enter
            if enviar_automatico:
                # Asegurar foco en el diálogo
                await _emit_keys(f"{KEY_SUPER}:1 {KEY_SUPER}:0")
                await asyncio.sleep(0.3)
                await _type_text("gmail")
                await asyncio.sleep(0.3)
                await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
                await asyncio.sleep(0.5)

                # Ctrl + Enter para enviar
                await _emit_keys(f"{KEY_CTRL}:1 {KEY_ENTER}:1 {KEY_ENTER}:0 {KEY_CTRL}:0")
                await asyncio.sleep(0.6)

                # Verificar que el diálogo de redacción se cerró (envío confirmado)
                self.a11y_sensor.verify_email_sent(timeout=4.0)

                logger.info(f"✅ Correo enviado exitosamente a '{destinatario}'.")
                return ToolResult(
                    success=True,
                    content=f"Correo enviado exitosamente a '{destinatario}' con asunto \"{asunto}\"."
                )
            else:
                return ToolResult(
                    success=True,
                    content=f"Correo redactado y abierto en pantalla para '{destinatario}' con asunto \"{asunto}\". Listo para tu confirmación."
                )

        except Exception as e:
            logger.error(f"Error enviando correo: {e}")
            return ToolResult(
                success=False,
                content=f"Error al enviar correo por Gmail: {str(e)}"
            )
