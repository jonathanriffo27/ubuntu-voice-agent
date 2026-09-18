import asyncio
import datetime
import os
import subprocess
import shutil
from typing import Dict, Any

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.utils.logging import get_logger

logger = get_logger("plugins.system")


def detect_user_location() -> str | None:
    _TZ_TO_COUNTRY = {
        "America/Santiago": "Chile", "America/Punta_Arenas": "Chile",
        "America/Argentina": "Argentina", "America/Buenos_Aires": "Argentina",
        "America/Sao_Paulo": "Brasil", "America/Fortaleza": "Brasil",
        "America/Mexico_City": "México", "America/Cancun": "México",
        "America/Bogota": "Colombia", "America/Lima": "Perú",
        "America/Caracas": "Venezuela", "America/Guayaquil": "Ecuador",
        "America/Montevideo": "Uruguay", "America/Asuncion": "Paraguay",
        "America/La_Paz": "Bolivia", "America/Panama": "Panamá",
        "America/Costa_Rica": "Costa Rica", "America/Havana": "Cuba",
        "America/Santo_Domingo": "República Dominicana",
        "Europe/Madrid": "España", "America/New_York": "Estados Unidos",
        "America/Los_Angeles": "Estados Unidos", "America/Chicago": "Estados Unidos",
    }
    tz = None
    try:
        with open('/etc/timezone') as f:
            tz = f.read().strip()
    except Exception:
        try:
            tz = subprocess.check_output(
                ['timedatectl', 'show', '-p', 'Timezone', '--value'],
                text=True
            ).strip()
        except Exception:
            pass
    if not tz:
        return None
    if tz in _TZ_TO_COUNTRY:
        return _TZ_TO_COUNTRY[tz]
    for prefix, country in _TZ_TO_COUNTRY.items():
        if tz.startswith(prefix.rsplit('/', 1)[0]):
            return country
    return tz


class EstadoSistemaTool(BaseTool):
    @property
    def name(self) -> str:
        return "obtener_estado_sistema"

    @property
    def description(self) -> str:
        return "Obtiene la hora actual."

    @property
    def parameters(self) -> Dict[str, Any]:
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        data = {
            "hora_actual": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "plataforma": os.uname().sysname,
            "usuario": os.getlogin(),
            "ubicacion": detect_user_location() or "desconocida"
        }
        return ToolResult(success=True, content=str(data), metadata=data)


class ImprimirConsolaTool(BaseTool):
    @property
    def name(self) -> str:
        return "imprimir_en_consola"

    @property
    def description(self) -> str:
        return (
            "Imprime bloques de código extenso, tablas complejas o reportes formateados en la terminal "
            "ÚNICAMENTE cuando el usuario lo solicite expresamente ('imprime en pantalla', 'muéstrame el código'). "
            "No usar para respuestas conversacionales normales, ya que tu voz ya se transcribe automáticamente en la consola."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "texto": {"type": "STRING", "description": "El texto a imprimir"}
            }, 
            "required": ["texto"]
        }

    async def execute(self, context: ToolContext, texto: str = None) -> ToolResult:
        if not texto:
            return ToolResult(success=False, content="Falta el texto a imprimir.")
        clean_text = texto.replace("\\n", "\n").replace("\\r", "").strip()
        print(f"\n┌── 📄 [Texto en pantalla] ──────────────────────────┐\n{clean_text}\n└───────────────────────────────────────────────────┘\n")
        return ToolResult(success=True, content="Texto impreso en la consola correctamente.")


DESKTOP_DIRS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
]

APP_ALIASES: Dict[str, list] = {
    "whatsapp": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "whatsapp web": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "whatsapp-web": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "whatsapp-pwa": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "whatsapp pwa": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "whatsappweb": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "pwa whatsapp": ["whatsapp", "brave-hnpfjngllnobngcgfapefoaidbinmjnm"],
    "gmail": ["gmail", "brave-mail.google.com__mail_"],
    "gmail-pwa": ["gmail", "brave-mail.google.com__mail_"],
    "correo": ["gmail", "thunderbird"],
    "mail": ["gmail", "thunderbird"],
    "calculadora": ["calculator", "gnome-calculator"],
    "calc": ["calculator", "gnome-calculator"],
    "terminal": ["terminal", "ptyxis"],
    "consola": ["terminal", "ptyxis"],
    "archivos": ["files", "nautilus", "org.gnome.nautilus"],
    "carpetas": ["files", "nautilus", "org.gnome.nautilus"],
    "explorador": ["files", "nautilus", "org.gnome.nautilus"],
    "nautilus": ["files", "nautilus", "org.gnome.nautilus"],
    "vscode": ["code", "visual studio code"],
    "code": ["code", "visual studio code"],
    "documentos": ["onlyoffice", "libreoffice-writer"],
    "texto": ["onlyoffice", "libreoffice-writer", "gedit", "text-editor"],
    "navegador": ["brave-browser", "google-chrome", "firefox"],
    "browser": ["brave-browser", "google-chrome", "firefox"],
    "musica": ["spotify", "rhythmbox"],
    "spotify": ["spotify"],
    "telegram": ["telegram", "telegram-desktop", "org.telegram.desktop"],
    "telegram-desktop": ["telegram", "telegram-desktop", "org.telegram.desktop"],
    "discord": ["discord"],
    "obsidian": ["obsidian"],
    "slack": ["slack"],
}

PURE_WEB_SERVICES = {
    "youtube": "https://youtube.com",
    "netflix": "https://netflix.com",
}


def scan_installed_desktop_apps() -> list:
    """Escanea las carpetas estándar de aplicaciones de escritorio (.desktop) en Linux."""
    apps = []
    seen = set()
    for d in DESKTOP_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            entries = sorted(os.listdir(d))
        except Exception:
            continue
        for fname in entries:
            if not fname.endswith(".desktop"):
                continue
            desktop_id = fname[:-8]
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            path = os.path.join(d, fname)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                name = None
                generic_name = None
                exec_cmd = None
                nodisplay = False
                for line in content.splitlines():
                    if line.startswith("Name=") and not name:
                        name = line.split("=", 1)[1].strip().replace("\u00a0", " ")
                    elif line.startswith("GenericName=") and not generic_name:
                        generic_name = line.split("=", 1)[1].strip().replace("\u00a0", " ")
                    elif line.startswith("Exec=") and not exec_cmd:
                        exec_cmd = line.split("=", 1)[1].strip()
                    elif line.strip().lower() == "nodisplay=true":
                        nodisplay = True
                if name and not nodisplay and exec_cmd:
                    apps.append({
                        "id": desktop_id,
                        "name": name,
                        "generic_name": generic_name or "",
                        "path": path,
                        "exec": exec_cmd
                    })
            except Exception:
                pass
    return apps


def resolve_desktop_app(query: str, apps: Optional[list] = None) -> Optional[dict]:
    """Encuentra la mejor aplicación de escritorio instalada que coincida con la consulta."""
    import re
    if apps is None:
        apps = scan_installed_desktop_apps()

    q = query.lower().strip()

    def clean(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', s.lower())

    q_clean = clean(q)
    q_base = re.sub(r'\b(pwa|web|app)\b', '', q).strip()
    q_base_clean = clean(q_base)

    alias_targets = APP_ALIASES.get(q, [])
    if not alias_targets and q_base in APP_ALIASES:
        alias_targets = APP_ALIASES.get(q_base, [])

    candidates = []
    for a in apps:
        a_name = a["name"].lower()
        a_id = a["id"].lower()
        a_name_clean = clean(a["name"])
        a_id_clean = clean(a["id"])

        score = 0
        if q == a_name or q == a_id:
            score = 100
        elif any(target == a_name or target == a_id for target in alias_targets):
            score = 95
        elif q_clean == a_name_clean or q_clean == a_id_clean:
            score = 90
        elif q_base_clean and (q_base_clean == a_name_clean or q_base_clean == a_id_clean):
            score = 88
        elif any(clean(target) == a_name_clean or clean(target) == a_id_clean for target in alias_targets):
            score = 85
        elif any(target in a_id or target in a_name for target in alias_targets):
            score = 80
        elif q_clean and (q_clean in a_name_clean or q_clean in a_id_clean):
            score = 75
        elif q_base_clean and (q_base_clean in a_name_clean or q_base_clean in a_id_clean):
            score = 70
        elif a["generic_name"] and q_clean in clean(a["generic_name"]):
            score = 50

        if score > 0:
            if a["path"].startswith(os.path.expanduser("~/.local")):
                score += 5
            candidates.append((score, a))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def launch_desktop_app(app_info: dict) -> bool:
    """Lanza una aplicación de escritorio mediante gtk-launch o su comando Exec."""
    import re
    desktop_id = app_info["id"]
    if shutil.which("gtk-launch"):
        try:
            subprocess.Popen(
                ["gtk-launch", desktop_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            return True
        except Exception as e:
            logger.debug(f"gtk-launch con {desktop_id} falló: {e}")

    raw_exec = app_info.get("exec", "")
    if raw_exec:
        clean_exec = re.sub(r'%[a-zA-Z]', '', raw_exec).strip()
        try:
            import shlex as _shlex
            subprocess.Popen(
                _shlex.split(clean_exec),
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            return True
        except Exception as e:
            logger.debug(f"Ejecución directa de Exec falló: {e}")
    return False


class AbrirAplicacionTool(BaseTool):
    @property
    def name(self) -> str:
        return "abrir_aplicacion"

    @property
    def description(self) -> str:
        return (
            "Abre o enfoca una aplicación instalada en el sistema (ej. whatsapp, gmail, telegram, spotify, "
            "terminal, calculadora, code, obsidian). Prioriza SIEMPRE las aplicaciones locales instaladas "
            "y PWAs en lugar de abrirlas en el navegador web. IMPORTANTE: esta tool solo ABRE la ventana; "
            "para LEER u operar el contenido de un servicio web (correos de Gmail, mensajes de WhatsApp) "
            "usa directamente 'navegador_web' con la URL del servicio."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "nombre": {"type": "STRING", "description": "El nombre de la aplicación a abrir"}
            }, 
            "required": ["nombre"]
        }

    async def execute(self, context: ToolContext, nombre: str = None) -> ToolResult:
        if not nombre:
            return ToolResult(success=False, content="Falta el nombre de la aplicación.")
            
        nombre_lower = nombre.lower().strip()

        # 1. Caso especializado: WhatsApp (prioriza PWA con DOM/AT-SPI2 listo)
        if "whatsapp" in nombre_lower:
            try:
                from src.plugins.whatsapp.tools import _launch_or_focus_whatsapp, _find_whatsapp_desktop
                desktop_path = _find_whatsapp_desktop()
                if desktop_path:
                    await _launch_or_focus_whatsapp(desktop_path)
                    logger.info("Aplicación WhatsApp Web (PWA) abierta/enfocada exitosamente.")
                    return ToolResult(
                        success=True,
                        content="He abierto y enfocado la aplicación WhatsApp Web (PWA) instalada en tu sistema."
                    )
            except Exception as we:
                logger.warning(f"Fallback al resolvedor general para WhatsApp: {we}")

        # 2. Búsqueda de aplicaciones de escritorio instaladas (.desktop y PWAs)
        installed_app = resolve_desktop_app(nombre_lower)
        if installed_app:
            if launch_desktop_app(installed_app):
                logger.info(f"Aplicación instalada '{installed_app['name']}' ({installed_app['id']}) lanzada correctamente.")
                return ToolResult(
                    success=True,
                    content=f"He abierto la aplicación instalada '{installed_app['name']}' en tu sistema."
                )

        # 3. Búsqueda de binario ejecutable en el PATH
        import re
        clean_bin = re.sub(r'[^a-zA-Z0-9_-]', '', nombre_lower)
        if clean_bin:
            candidate = (
                shutil.which(clean_bin) or 
                shutil.which(f"{clean_bin}-desktop") or 
                shutil.which(f"gnome-{clean_bin}")
            )
            if candidate:
                try:
                    subprocess.Popen(
                        [candidate],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    logger.info(f"Binario '{candidate}' ejecutado correctamente.")
                    return ToolResult(success=True, content=f"He abierto {nombre} en tu sistema.")
                except Exception as e:
                    logger.error(f"Error al ejecutar binario {candidate}: {e}")
                    return ToolResult(success=False, content=f"Falló al intentar abrir {nombre}: {e}")

        # 4. Servicios estrictamente web o URLs directas
        if nombre_lower in PURE_WEB_SERVICES:
            url = PURE_WEB_SERVICES[nombre_lower]
            subprocess.Popen(
                ["xdg-open", url],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return ToolResult(success=True, content=f"He abierto {nombre} ({url}) en el navegador web.")

        if nombre_lower.startswith("http://") or nombre_lower.startswith("https://"):
            subprocess.Popen(
                ["xdg-open", nombre],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return ToolResult(success=True, content=f"He abierto la URL en el navegador: {nombre}")

        # 5. La app no está instalada y se evita abrir en navegador por directiva de usuario
        logger.warning(f"Aplicación '{nombre}' no encontrada entre las instaladas.")
        return ToolResult(
            success=False,
            content=f"No encontré la aplicación local '{nombre}' instalada en tu sistema. "
                    f"Para evitar abrirla en el navegador en contra de tus preferencias, por favor "
                    f"instálala primero (ej: como PWA desde Brave/Chrome) o indícame si deseas abrirla en la web."
        )


class EnfocarAplicacionTool(BaseTool):
    @property
    def name(self) -> str:
        return "enfocar_aplicacion"

    @property
    def description(self) -> str:
        return "Busca una aplicación que ya está abierta y la trae al frente de la pantalla (le da el foco). Úsalo cuando el usuario quiera interactuar con una app que quedó en segundo plano."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "nombre": {"type": "STRING", "description": "El nombre de la aplicación (ej. 'onlyoffice', 'firefox', 'terminal')"}
            }, 
            "required": ["nombre"]
        }

    async def execute(self, context: ToolContext, nombre: str = None) -> ToolResult:
        if not nombre:
            return ToolResult(success=False, content="Falta el nombre de la aplicación.")
            
        import asyncio
        
        try:
            env = os.environ.copy()
            uid = os.getuid()
            env["YDOTOOL_SOCKET"] = f"/run/user/{uid}/.ydotool_socket"

            proc1 = await asyncio.create_subprocess_exec(
                "ydotool", "key", "125:1", "125:0",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc1.wait()
            await asyncio.sleep(0.4)

            proc2 = await asyncio.create_subprocess_exec(
                "ydotool", "type", nombre,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc2.wait()
            await asyncio.sleep(0.4)

            proc3 = await asyncio.create_subprocess_exec(
                "ydotool", "key", "28:1", "28:0",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc3.wait()
            
            return ToolResult(success=True, content=f"He buscado y enfocado la aplicación '{nombre}' en la pantalla.")
        except Exception as e:
            return ToolResult(success=False, content=f"Falló al intentar enfocar {nombre}: {e}")


# Procesos protegidos del sistema que NUNCA deben cerrarse
PROTECTED_PROCESSES = {
    "systemd", "init", "gnome-shell", "wayland", "xorg", "pipewire",
    "wireplumber", "pulseaudio", "dbus", "atlas", "python", "python3",
    "ydotool", "ydotoold", "bash", "zsh", "sshd", "login", "sudo",
    "gdm", "gdm3", "systemd-logind", "dbus-daemon", "Xwayland"
}

APP_CLOSE_TARGETS: Dict[str, Dict[str, Any]] = {
    "whatsapp": {"cmd_substr": ["hnpfjngllnobngcgfapefoaidbinmjnm"], "names": ["whatsapp"]},
    "whatsapp web": {"cmd_substr": ["hnpfjngllnobngcgfapefoaidbinmjnm"], "names": ["whatsapp"]},
    "whatsapp-pwa": {"cmd_substr": ["hnpfjngllnobngcgfapefoaidbinmjnm"], "names": ["whatsapp"]},
    "whatsapp pwa": {"cmd_substr": ["hnpfjngllnobngcgfapefoaidbinmjnm"], "names": ["whatsapp"]},
    "whatsappweb": {"cmd_substr": ["hnpfjngllnobngcgfapefoaidbinmjnm"], "names": ["whatsapp"]},
    "gmail": {"cmd_substr": ["Brave-Browser-Gmail", "mail.google.com"], "names": ["gmail"]},
    "gmail-pwa": {"cmd_substr": ["Brave-Browser-Gmail"], "names": ["gmail"]},
    "correo": {"cmd_substr": ["Brave-Browser-Gmail"], "names": ["thunderbird", "gmail"]},
    "spotify": {"names": ["spotify"]},
    "telegram": {"names": ["telegram", "telegram-desktop", "Telegram"]},
    "telegram-desktop": {"names": ["telegram", "telegram-desktop", "Telegram"]},
    "calculadora": {"names": ["gnome-calculator", "calculator"]},
    "calc": {"names": ["gnome-calculator", "calculator"]},
    "terminal": {"names": ["ptyxis", "gnome-terminal", "alacritty", "kitty", "xterm"]},
    "consola": {"names": ["ptyxis", "gnome-terminal", "alacritty", "kitty", "xterm"]},
    "archivos": {"names": ["nautilus"]},
    "carpetas": {"names": ["nautilus"]},
    "nautilus": {"names": ["nautilus"]},
    "vscode": {"names": ["code"]},
    "code": {"names": ["code"]},
    "obsidian": {"names": ["obsidian"]},
    "discord": {"names": ["discord"]},
    "slack": {"names": ["slack"]},
    "onlyoffice": {"names": ["onlyoffice-desktopeditors", "DesktopEditors"]},
    "documentos": {"names": ["onlyoffice-desktopeditors", "DesktopEditors", "soffice.bin"]},
    "brave": {"names": ["brave", "brave-browser"]},
    "navegador": {"names": ["brave", "brave-browser", "google-chrome", "firefox"]},
    "chrome": {"names": ["google-chrome", "chrome"]},
    "firefox": {"names": ["firefox"]},
}


def find_processes_for_app(app_name: str) -> list:
    """
    Encuentra los procesos correspondientes a una aplicación en ejecución.
    Para PWAs (WhatsApp, Gmail), aísla exclusivamente los procesos con el ID de la app en cmdline
    para no cerrar la sesión principal del navegador Brave.
    """
    import psutil
    q = app_name.lower().strip()
    target_info = APP_CLOSE_TARGETS.get(q)

    target_names = [name.lower() for name in target_info["names"]] if target_info and "names" in target_info else []
    target_substrs = target_info.get("cmd_substr", []) if target_info else []

    # Si no está en el mapa estático, buscar en los .desktop instalados
    if not target_names and not target_substrs:
        installed_app = resolve_desktop_app(q)
        if installed_app:
            desktop_id = installed_app["id"].lower()
            exec_cmd = installed_app.get("exec", "").lower()
            binary_name = os.path.basename(exec_cmd.split()[0]) if exec_cmd else desktop_id
            target_names = [binary_name, desktop_id, q]
        else:
            target_names = [q]

    # Identificar los PIDs que NUNCA hay que tocar: el propio Atlas, sus
    # ancestros (shell/launcher) y sus hijos de INFRAESTRUCTURA (el túnel SSH
    # a CLIProxy). OJO: las apps que Atlas LANZA para el usuario (Spotify vía
    # plugin de música, Brave CDP, PWAs) también son hijas suyas en el árbol de
    # procesos — excluirlas hacía imposible cerrarlas (bug real: "cierra
    # Spotify" no mataba nada del árbol de Spotify y reportaba éxito).
    _INFRA_CHILD_MARKERS = ("ssh ",)  # túnel CLIProxy del subagente
    current_pid = os.getpid()
    atlas_pids = {current_pid}
    try:
        current_proc = psutil.Process(current_pid)
        for parent in current_proc.parents():
            atlas_pids.add(parent.pid)
        for child in current_proc.children(recursive=True):
            try:
                cmd = " ".join(child.cmdline() or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cmd = ""
            if any(m in cmd for m in _INFRA_CHILD_MARKERS):
                atlas_pids.add(child.pid)
    except Exception:
        pass

    matching_procs = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = p.info["pid"]
            if pid in atlas_pids:
                continue

            p_name = (p.info["name"] or "").lower()
            if p_name in PROTECTED_PROCESSES:
                continue

            p_cmd = " ".join(p.info["cmdline"] or [])
            p_cmd_lower = p_cmd.lower()

            # 1. PWA coincidencia específica por ID en cmdline
            if target_substrs:
                if any(s.lower() in p_cmd_lower for s in target_substrs):
                    matching_procs.append(p)
                    continue
                continue

            # 2. Coincidencia exacta por nombre de proceso
            if p_name in target_names:
                matching_procs.append(p)
                continue

            # 3. Coincidencia por prefijo o sufijo del nombre
            matched = False
            for tn in target_names:
                if len(tn) >= 3 and (p_name == tn or p_name.startswith(f"{tn}-") or p_name.endswith(f"-{tn}")):
                    matching_procs.append(p)
                    matched = True
                    break
            if matched:
                continue

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return matching_procs


def terminate_processes(procs: list, timeout: float = 1.5) -> int:
    """Termina una lista de procesos con SIGTERM y luego SIGKILL si persisten."""
    import psutil
    if not procs:
        return 0

    # 1. Enviar SIGTERM
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # 2. Esperar a que terminen
    gone, alive = psutil.wait_procs(procs, timeout=timeout)

    # 3. Forzar con SIGKILL los que sigan vivos
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return len(gone) + len(alive)


class CerrarAplicacionTool(BaseTool):
    """Cierra una aplicación de escritorio o PWA que esté en ejecución."""

    @property
    def name(self) -> str:
        return "cerrar_aplicacion"

    @property
    def description(self) -> str:
        return (
            "Cierra una aplicación de escritorio o PWA en ejecución (ej. spotify, whatsapp, telegram, "
            "calculadora, terminal, code, obsidian). Termina el proceso de forma directa, segura e instantánea "
            "sin requerir comandos de shell ni confirmaciones complejas."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "nombre": {
                    "type": "STRING",
                    "description": "El nombre de la aplicación a cerrar (ej. 'spotify', 'whatsapp', 'calculadora', 'telegram')"
                }
            },
            "required": ["nombre"]
        }

    async def execute(self, context: ToolContext, nombre: str = None) -> ToolResult:
        if not nombre:
            return ToolResult(success=False, content="Falta el nombre de la aplicación a cerrar.")

        nombre_clean = nombre.lower().strip()

        # Seguridad: evitar cerrar procesos protegidos o Atlas
        if nombre_clean in PROTECTED_PROCESSES:
            return ToolResult(
                success=False,
                content=f"Por seguridad no puedo cerrar el proceso del sistema '{nombre}'."
            )

        try:
            procs = find_processes_for_app(nombre_clean)
            if not procs:
                return ToolResult(
                    success=True,
                    content=f"La aplicación '{nombre}' no está en ejecución actualmente."
                )

            count = terminate_processes(procs)
            logger.info(f"Aplicación '{nombre}' cerrada ({count} procesos terminados).")

            # Verificación post-cierre: nunca declarar éxito sin comprobar que
            # ya no quedan procesos (bug real: se mataba 1 proceso satélite de
            # Spotify y se anunciaba "cerrada" con la ventana abierta).
            await asyncio.sleep(0.4)
            remaining = find_processes_for_app(nombre_clean)
            if remaining:
                return ToolResult(
                    success=False,
                    content=(
                        f"Intenté cerrar '{nombre}' pero aún quedan {len(remaining)} procesos vivos "
                        f"(la app podría estar reinciándose sola o protegida). Dile al usuario que "
                        f"puede que necesite cerrarla manualmente."
                    ),
                )
            return ToolResult(
                success=True,
                content=f"He cerrado '{nombre}' correctamente."
            )
        except Exception as e:
            logger.error(f"Error al cerrar la aplicación '{nombre}': {e}")
            return ToolResult(
                success=False,
                content=f"Error al intentar cerrar '{nombre}': {e}"
            )

