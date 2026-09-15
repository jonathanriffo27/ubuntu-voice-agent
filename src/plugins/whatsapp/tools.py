import asyncio
import os
import shutil
import time
from typing import Dict, Any, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.vision.service import OptimizedScreenCaptureService
from src.utils.a11y import AccessibilitySensor
from src.utils.logging import get_logger

logger = get_logger("plugins.whatsapp")

# Códigos de teclas Linux (input-event-codes.h)
KEY_ESC = "1"
KEY_ENTER = "28"
KEY_CTRL = "29"
KEY_V = "47"
KEY_SLASH = "53"
KEY_ALT = "56"
KEY_SUPER = "125"

# Nombre del archivo .desktop de WhatsApp Web PWA en el sistema
_WHATSAPP_DESKTOP_ID = "brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default"
_WHATSAPP_DESKTOP_FILE = f"{_WHATSAPP_DESKTOP_ID}.desktop"
_WHATSAPP_SEARCH_PATHS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
]


def _find_whatsapp_desktop() -> Optional[str]:
    """Comprueba si WhatsApp Web está instalado en el sistema."""
    for search_dir in _WHATSAPP_SEARCH_PATHS:
        path = os.path.join(search_dir, _WHATSAPP_DESKTOP_FILE)
        if os.path.isfile(path):
            return path
    for search_dir in _WHATSAPP_SEARCH_PATHS:
        if not os.path.isdir(search_dir):
            continue
        for fname in os.listdir(search_dir):
            if "whatsapp" in fname.lower() and fname.endswith(".desktop"):
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


def _parse_desktop_exec(desktop_path: str) -> Optional[list]:
    """Extrae los argumentos de la línea Exec= del archivo .desktop de la PWA."""
    try:
        with open(desktop_path, "r") as f:
            for line in f:
                if line.startswith("Exec="):
                    # Ej: Exec=/opt/brave.com/brave/brave-browser --profile-directory=Default --app-id=xxx
                    return line.strip().removeprefix("Exec=").split()
    except Exception as e:
        logger.debug(f"Error parseando {desktop_path}: {e}")
    return None


async def _kill_orca() -> None:
    """Mata el proceso de Orca (lector de pantalla) de forma silenciosa e inmediata."""
    await (await asyncio.create_subprocess_exec(
        "pkill", "-9", "-f", "orca",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )).wait()


async def _ensure_a11y_enabled() -> bool:
    """
    Activa ScreenReaderEnabled en el bus de sesión AT-SPI2 para que Chromium/Brave
    exponga el árbol DOM web completo. Lo desactiva inmediatamente después y
    mata Orca repetidamente para que nunca llegue a hablar.
    
    Este enfoque es necesario porque --force-renderer-accessibility solo funciona
    si Brave no está corriendo aún, y la PWA de WhatsApp normalmente se reutiliza
    dentro de una sesión de Brave ya activa.
    """
    try:
        # Verificar si ya está activada
        check = await asyncio.create_subprocess_exec(
            "gdbus", "call", "--session",
            "--dest", "org.a11y.Bus",
            "--object-path", "/org/a11y/bus",
            "--method", "org.freedesktop.DBus.Properties.Get",
            "org.a11y.Status", "ScreenReaderEnabled",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await check.communicate()
        already_enabled = b"true" in out

        if not already_enabled:
            # Activar ScreenReaderEnabled
            await (await asyncio.create_subprocess_exec(
                "gdbus", "call", "--session",
                "--dest", "org.a11y.Bus",
                "--object-path", "/org/a11y/bus",
                "--method", "org.freedesktop.DBus.Properties.Set",
                "org.a11y.Status", "ScreenReaderEnabled",
                "<boolean true>",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )).wait()
            logger.debug("AT-SPI2 ScreenReaderEnabled activado temporalmente")

            # Matar Orca inmediatamente antes de que pueda hablar,
            # y repetir durante la espera para interceptar lanzamientos tardíos
            await _kill_orca()
            for _ in range(4):
                await asyncio.sleep(0.5)
                await _kill_orca()

            # Desactivar inmediatamente para evitar efectos secundarios
            await (await asyncio.create_subprocess_exec(
                "gdbus", "call", "--session",
                "--dest", "org.a11y.Bus",
                "--object-path", "/org/a11y/bus",
                "--method", "org.freedesktop.DBus.Properties.Set",
                "org.a11y.Status", "ScreenReaderEnabled",
                "<boolean false>",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )).wait()
            logger.debug("AT-SPI2 ScreenReaderEnabled desactivado")

            # Kill final por si Orca se relanzó al desactivar
            await _kill_orca()

        return True
    except Exception as e:
        logger.debug(f"Error gestionando AT-SPI2 ScreenReaderEnabled: {e}")
        return False


def _is_whatsapp_running() -> bool:
    """Verifica si el proceso de WhatsApp Web (PWA de Brave) está en ejecución."""
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
                if _WHATSAPP_DESKTOP_ID in cmd or "web.whatsapp.com" in cmd:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass
    return False


async def _launch_or_focus_whatsapp(desktop_path: Optional[str] = None) -> bool:
    """Lanza o enfoca la ventana PWA de WhatsApp Web."""
    desktop_id = _WHATSAPP_DESKTOP_ID
    launched = False

    # 0. Activar accesibilidad en el bus AT-SPI2 para que Brave pueble el árbol web
    await _ensure_a11y_enabled()

    # Si ya está en ejecución, enfocar la ventana existente
    if _is_whatsapp_running():
        # 1. Intentar activación instantánea vía extensión GNOME
        try:
            with open("/tmp/focus-target", "w") as f:
                f.write("whatsapp")
            p1 = await asyncio.create_subprocess_exec("gnome-extensions", "disable", "window-dump@temp", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await p1.wait()
            p2 = await asyncio.create_subprocess_exec("gnome-extensions", "enable", "window-dump@temp", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await p2.wait()
            await asyncio.sleep(0.4)
        except Exception:
            pass

        # 2. Respaldo garantizado vía GNOME Shell con tiempos reales de animación
        await _emit_keys(f"{KEY_SUPER}:1 {KEY_SUPER}:0")
        await asyncio.sleep(0.4)
        await _type_text("whatsapp")
        await asyncio.sleep(0.4)
        await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
        await asyncio.sleep(0.8)
        return True

    # 1. Intentar lanzar directamente con brave-browser + --force-renderer-accessibility
    if desktop_path:
        desktop_id = os.path.splitext(os.path.basename(desktop_path))[0]
        exec_args = _parse_desktop_exec(desktop_path)
        if exec_args:
            brave_cmd = exec_args[0]
            extra_args = exec_args[1:]
            full_args = [brave_cmd, "--force-renderer-accessibility"] + extra_args
            try:
                await asyncio.create_subprocess_exec(
                    *full_args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                launched = True
                logger.debug("PWA de WhatsApp lanzada con --force-renderer-accessibility")
            except Exception as e:
                logger.debug(f"Error lanzando PWA directamente: {e}")

    # 2. Fallback a gtk-launch si no se pudo lanzar directamente
    if not launched and shutil.which("gtk-launch"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "gtk-launch", desktop_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except Exception as e:
            logger.debug(f"gtk-launch con {desktop_id}: {e}")

    # 3. Enfoque en GNOME Shell mediante atajo Super + 'whatsapp' para traer la ventana al frente
    await _emit_keys(f"{KEY_SUPER}:1 {KEY_SUPER}:0")
    await asyncio.sleep(0.3)
    await _type_text("whatsapp")
    await asyncio.sleep(0.3)
    await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
    await asyncio.sleep(0.5)
    return True


class EnviarWhatsAppTool(BaseTool):
    """Envía un mensaje por WhatsApp usando la app de escritorio instalada del usuario."""

    def __init__(self, vision_service: Optional[OptimizedScreenCaptureService] = None):
        self.vision_service = vision_service or OptimizedScreenCaptureService()
        self.a11y_sensor = AccessibilitySensor()

    @property
    def name(self) -> str:
        return "enviar_whatsapp"

    @property
    def description(self) -> str:
        return (
            "Envía un mensaje de texto por WhatsApp usando la aplicación de escritorio "
            "instalada del usuario (WhatsApp Web PWA, sin necesidad de API keys). "
            "Puede enviar por número de teléfono (ej: +56 9 5790 9790) o por nombre de contacto/chat. "
            "Úsalo cuando el usuario pida enviar un mensaje por WhatsApp."
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
                "destinatario": {
                    "type": "STRING",
                    "description": (
                        "Número de teléfono (ej: '+56 9 5790 9790') o nombre del contacto/chat en WhatsApp."
                    )
                }
            },
            "required": ["mensaje", "destinatario"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        mensaje = kwargs.get("mensaje", "").strip()
        destinatario = kwargs.get("destinatario", "") or kwargs.get("telefono", "")
        destinatario = destinatario.strip()

        if not mensaje:
            return ToolResult(success=False, content="Falta el mensaje a enviar.")
        if not destinatario:
            return ToolResult(success=False, content="Falta el destinatario o número de teléfono.")

        # 1. Comprobar que WhatsApp Web está instalado en el sistema
        desktop_path = _find_whatsapp_desktop()
        if not desktop_path:
            return ToolResult(
                success=False,
                content="No se encontró la app de escritorio de WhatsApp Web instalada en tu sistema. "
                        "Instálala como PWA desde Brave/Chrome ingresando a web.whatsapp.com."
            )

        # 2. Comprobar que ydotool está disponible
        if not shutil.which("ydotool"):
            return ToolResult(
                success=False,
                content="Se necesita 'ydotool' para interactuar con la app de WhatsApp. Instálalo con: sudo apt install ydotool"
            )

        logger.info(f"Iniciando envío de WhatsApp a '{destinatario}': \"{mensaje[:50]}...\"")

        try:
            # 1. Enfocar o lanzar la PWA de WhatsApp
            await _launch_or_focus_whatsapp(desktop_path)

            # 2. Espera REACTIVA con AT-SPI2 / árbol accesible
            timeout = 15.0
            ready, detail = self.a11y_sensor.wait_for_whatsapp_ready(timeout=timeout)
            if not ready:
                logger.error(f"WhatsApp Web no está listo: {detail}")
                return ToolResult(
                    success=False,
                    content=f"No se pudo enviar el mensaje. {detail}."
                )

            # 3. Limpiar cualquier menú o diálogo previo con Escape
            await _emit_keys(f"{KEY_ESC}:1 {KEY_ESC}:0")
            await asyncio.sleep(0.3)

            # 4. Enfocar la búsqueda en WhatsApp Web con su atajo nativo: Ctrl + Alt + /
            await _emit_keys(f"{KEY_CTRL}:1 {KEY_ALT}:1 {KEY_SLASH}:1 {KEY_SLASH}:0 {KEY_ALT}:0 {KEY_CTRL}:0")
            await asyncio.sleep(0.5)

            # 5. Escribir el destinatario (número normalizado o nombre de contacto)
            num_search = destinatario.replace(" ", "").replace("-", "") if any(c.isdigit() for c in destinatario) else destinatario
            await _type_text(num_search)
            await asyncio.sleep(1.0)

            # 6. Seleccionar y abrir el chat con Enter
            await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
            await asyncio.sleep(0.8)
            
            # 7. Copiar mensaje al portapapeles y pegar con Ctrl+V (soporte caracteres/multilínea)
            await _copy_to_clipboard(mensaje)
            await asyncio.sleep(0.2)
            await _emit_keys(f"{KEY_CTRL}:1 {KEY_V}:1 {KEY_V}:0 {KEY_CTRL}:0")
            await asyncio.sleep(0.3)

            # 8. Presionar Enter para enviar el mensaje
            await _emit_keys(f"{KEY_ENTER}:1 {KEY_ENTER}:0")
            await asyncio.sleep(0.5)

            logger.info(f"✅ Mensaje enviado a '{destinatario}' por WhatsApp exitosamente.")
            return ToolResult(
                success=True,
                content=f"Mensaje enviado exitosamente por WhatsApp a '{destinatario}': \"{mensaje}\""
            )

        except Exception as e:
            logger.error(f"Error enviando WhatsApp: {e}")
            return ToolResult(
                success=False,
                content=f"Error al enviar mensaje por WhatsApp: {str(e)}"
            )


class AbrirWhatsAppTool(BaseTool):
    """Abre o enfoca la aplicación de escritorio instalada de WhatsApp (WhatsApp Web PWA)."""

    @property
    def name(self) -> str:
        return "abrir_whatsapp"

    @property
    def description(self) -> str:
        return (
            "Abre o trae al frente la aplicación de escritorio de WhatsApp (WhatsApp Web PWA). "
            "Úsalo SIEMPRE que el usuario pida abrir, ver o mostrar WhatsApp. "
            "Prioriza la app instalada en el sistema y NUNCA la abre en el navegador."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        desktop_path = _find_whatsapp_desktop()
        if not desktop_path:
            return ToolResult(
                success=False,
                content="No se encontró la app de WhatsApp Web (PWA) instalada en tu sistema. "
                        "Para no abrirla en el navegador en contra de tus preferencias, "
                        "por favor instálala como PWA desde Brave o Chrome ingresando a web.whatsapp.com."
            )
        try:
            await _launch_or_focus_whatsapp(desktop_path)
            return ToolResult(
                success=True,
                content="He abierto y enfocado la aplicación WhatsApp Web (PWA) en tu sistema."
            )
        except Exception as e:
            logger.error(f"Error abriendo WhatsApp PWA: {e}")
            return ToolResult(
                success=False,
                content=f"Error al abrir WhatsApp PWA: {str(e)}"
            )


class CerrarWhatsAppTool(BaseTool):
    """Cierra la aplicación de escritorio de WhatsApp (WhatsApp Web PWA)."""

    @property
    def name(self) -> str:
        return "cerrar_whatsapp"

    @property
    def description(self) -> str:
        return (
            "Cierra la aplicación de escritorio de WhatsApp (WhatsApp Web PWA) de forma segura, "
            "sin cerrar las demás ventanas o pestañas del navegador."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        import psutil
        wa_app_id = "hnpfjngllnobngcgfapefoaidbinmjnm"
        matching_procs = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
                if wa_app_id in cmd:
                    matching_procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if not matching_procs:
            return ToolResult(
                success=True,
                content="La aplicación de WhatsApp no estaba en ejecución."
            )

        for p in matching_procs:
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        _, alive = psutil.wait_procs(matching_procs, timeout=1.0)
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return ToolResult(
            success=True,
            content="He cerrado la aplicación WhatsApp Web (PWA) correctamente."
        )


