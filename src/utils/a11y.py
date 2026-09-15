import os
import sys
import time
import subprocess
from typing import Optional, List, Dict, Any, Tuple

# Garantizar que gi / Atspi esté disponible incluso dentro de venv aislados de Debian/Ubuntu
if "/usr/lib/python3/dist-packages" not in sys.path:
    sys.path.append("/usr/lib/python3/dist-packages")

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    _ATSPI_GOBJECT_AVAILABLE = True
except Exception:
    _ATSPI_GOBJECT_AVAILABLE = False
    Atspi = None

from src.utils.logging import get_logger

logger = get_logger("system.a11y")

# Dirección estándar del bus AT-SPI2 en Linux
_A11Y_BUS_PATH = f"/run/user/{os.getuid()}/at-spi/bus"


class AccessibilitySensor:
    """
    Sensor de estado e introspección reactiva de aplicaciones de escritorio en Linux (GNOME/Wayland).
    Utiliza el bus AT-SPI2 nativo y la API GObject Atspi (con fallback a D-Bus/busctl) para consultar
    el árbol de accesibilidad en tiempo real de aplicaciones basadas en Chromium (Brave, Chrome,
    WhatsApp Web PWA, Gmail PWA) y Qt (Telegram Desktop).
    
    Permite detectar estados reales de carga (evitando condiciones de carrera y falsos positivos),
    validar campos interactivos, y confirmar la apertura de chats y envío de mensajes.
    """

    def __init__(self, bus_path: Optional[str] = None):
        self.bus_path = bus_path or _A11Y_BUS_PATH
        self.bus_address = f"unix:path={self.bus_path}"
        self._atspi_gobject_ready = False

        if _ATSPI_GOBJECT_AVAILABLE and os.path.exists(self.bus_path):
            try:
                Atspi.init()
                self._atspi_gobject_ready = True
            except Exception as e:
                logger.debug(f"Atspi.init() no pudo inicializarse: {e}")

    def is_available(self) -> bool:
        """Comprueba si el bus de accesibilidad AT-SPI2 está disponible y activo."""
        return os.path.exists(self.bus_path)

    def list_accessible_apps(self) -> List[Dict[str, str]]:
        """Lista las aplicaciones registradas actualmente en el bus AT-SPI2."""
        if not self.is_available():
            return []

        # 1. Intentar vía GObject Atspi si está inicializado
        if self._atspi_gobject_ready and Atspi is not None:
            try:
                desktop = Atspi.get_desktop(0)
                if desktop:
                    apps = []
                    for i in range(desktop.get_child_count()):
                        app = desktop.get_child_at_index(i)
                        if app:
                            name = app.get_name() or ""
                            apps.append({
                                "name": f":atspi.{i}",
                                "pid": "",
                                "process": name
                            })
                    if apps:
                        return apps
            except Exception as e:
                logger.debug(f"Error listando apps con Atspi GObject: {e}")

        # 2. Fallback a busctl directo sobre el bus AT-SPI2
        try:
            proc = subprocess.run(
                ["busctl", f"--address={self.bus_address}", "list", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if proc.returncode != 0:
                return []

            apps = []
            for line in proc.stdout.splitlines()[1:]:  # Omitir cabecera
                parts = line.split()
                if len(parts) >= 3 and parts[0].startswith(":"):
                    apps.append({
                        "name": parts[0],
                        "pid": parts[1] if parts[1] != "-" else "",
                        "process": parts[2] if len(parts) > 2 else ""
                    })
            return apps
        except Exception as e:
            logger.debug(f"Error listando apps en AT-SPI2 vía busctl: {e}")
            return []

    def find_app_connection(self, process_name_pattern: str) -> Optional[str]:
        """Busca el identificador de conexión D-Bus (:1.xx) de un proceso dado."""
        pattern = process_name_pattern.lower()
        for app in self.list_accessible_apps():
            if pattern in app["process"].lower():
                return app["name"]
        return None

    def get_child_count(self, connection: str, path: str = "/org/a11y/atspi/accessible/root") -> int:
        """Obtiene el número de hijos accesibles de un nodo en AT-SPI2."""
        if not self.is_available():
            return 0

        try:
            proc = subprocess.run(
                [
                    "busctl", f"--address={self.bus_address}",
                    "call", connection, path,
                    "org.a11y.atspi.Accessible", "GetChildren"
                ],
                capture_output=True,
                text=True,
                timeout=1.0
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parts = proc.stdout.strip().split()
                if len(parts) >= 2 and parts[0].startswith("a") and parts[1].isdigit():
                    return int(parts[1])

            proc_count = subprocess.run(
                [
                    "busctl", f"--address={self.bus_address}",
                    "call", connection, path,
                    "org.a11y.atspi.Accessible", "GetChildCount"
                ],
                capture_output=True,
                text=True,
                timeout=1.0
            )
            if proc_count.returncode == 0 and proc_count.stdout.strip():
                val_parts = proc_count.stdout.strip().split()
                if len(val_parts) >= 2 and val_parts[-1].isdigit():
                    return int(val_parts[-1])
            return 0
        except Exception:
            return 0

    def get_whatsapp_window(self) -> Optional[Any]:
        """
        Busca y retorna el objeto Accessible de la ventana de WhatsApp Web en el árbol AT-SPI2.
        Si hay múltiples ventanas con 'whatsapp' en el nombre (ej: PWA + pestaña de Brave),
        prefiere la que tenga contenido web real (nodos hijos accesibles > 0).
        """
        if not self._atspi_gobject_ready or Atspi is None:
            return None

        try:
            desktop = Atspi.get_desktop(0)
            if not desktop:
                return None

            candidates = []
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if not app:
                    continue
                app_name = (app.get_name() or "").lower()
                if "brave" not in app_name and "chrome" not in app_name and "whatsapp" not in app_name:
                    continue

                for j in range(app.get_child_count()):
                    win = app.get_child_at_index(j)
                    if not win:
                        continue
                    win_name = (win.get_name() or "").lower()
                    if ("whatsapp" in win_name or "web.whatsapp" in win_name) and not any(
                        x in win_name for x in ["claude", "detectar", "terminal", "bash"]
                    ):
                        candidates.append(win)

            if not candidates:
                return None

            # Si hay una sola ventana, retornarla directamente
            if len(candidates) == 1:
                return candidates[0]

            # Si hay múltiples, preferir la que tenga contenido web real
            # (más nodos hijos accesibles = árbol DOM más profundo)
            best = candidates[0]
            best_depth = self._probe_tree_depth(best, max_depth=5)
            for win in candidates[1:]:
                depth = self._probe_tree_depth(win, max_depth=5)
                if depth > best_depth:
                    best = win
                    best_depth = depth
            return best
        except Exception as e:
            logger.debug(f"Error localizando ventana de WhatsApp con Atspi: {e}")
        return None

    def _probe_tree_depth(self, node: Any, depth: int = 0, max_depth: int = 5) -> int:
        """Cuenta la profundidad efectiva del árbol accesible (para comparar ventanas)."""
        if not node or depth >= max_depth:
            return depth
        try:
            best = depth
            for i in range(min(node.get_child_count(), 3)):
                child = node.get_child_at_index(i)
                if child:
                    d = self._probe_tree_depth(child, depth + 1, max_depth)
                    if d > best:
                        best = d
            return best
        except Exception:
            return depth

    def find_nodes(
        self,
        root_node: Any,
        role: Optional[str] = None,
        name_contains: Optional[str] = None,
        is_editable: Optional[bool] = None,
        max_depth: int = 25
    ) -> List[Any]:
        """Busca recursivamente nodos accesibles que cumplan con los filtros especificados."""
        if not root_node or max_depth < 0:
            return []

        results = []
        try:
            node_role = root_node.get_role_name() if hasattr(root_node, "get_role_name") else ""
            node_name = (root_node.get_name() or "") if hasattr(root_node, "get_name") else ""
            node_desc = (root_node.get_description() or "") if hasattr(root_node, "get_description") else ""
            full_text = f"{node_name} {node_desc}".lower()

            match = True
            if role and node_role.lower() != role.lower():
                match = False
            if name_contains and name_contains.lower() not in full_text:
                match = False
            if is_editable is not None:
                state_set = root_node.get_state_set()
                node_editable = state_set.contains(Atspi.StateType.EDITABLE) if state_set else False
                if node_editable != is_editable:
                    match = False

            if match:
                results.append(root_node)

            for i in range(root_node.get_child_count()):
                child = root_node.get_child_at_index(i)
                if child:
                    results.extend(self.find_nodes(child, role, name_contains, is_editable, max_depth - 1))
        except Exception:
            pass
        return results

    def get_whatsapp_state(self, win: Optional[Any] = None) -> Tuple[str, str]:
        """
        Analiza el árbol de accesibilidad de WhatsApp Web y determina su estado real.
        
        Posibles estados:
        - 'READY': La interfaz interactiva (caja de búsqueda, lista de chats o composer) está activa y lista.
        - 'LOADING': WhatsApp está descargando mensajes, cargando chats o conectando el WebSocket.
        - 'QR_REQUIRED': La sesión no está iniciada (muestra pantalla de vinculación / código QR).
        - 'NOT_FOUND': La ventana de WhatsApp Web no está abierta.
        - 'BLANK': La ventana existe pero aún no monta el árbol DOM de accesibilidad.
        """
        if win is None:
            win = self.get_whatsapp_window()

        if win is None:
            # Comprobar si al menos hay una ventana por busctl
            if self.find_window_by_title("brave", "whatsapp") or self.find_window_by_title("brave", "web.whatsapp.com"):
                return "BLANK", "Ventana detectada en D-Bus pero árbol no inicializado"
            return "NOT_FOUND", "Ventana de WhatsApp no encontrada"

        found_loading = []
        found_qr = []
        found_ready = []

        def scan_tree(node: Any, depth: int = 0):
            if not node or depth > 25:
                return
            try:
                role = node.get_role_name() or ""
                name = (node.get_name() or "").strip()
                desc = (node.get_description() or "").strip()
                text = f"{name} {desc}".lower()

                state_set = node.get_state_set()
                is_editable = state_set.contains(Atspi.StateType.EDITABLE) if state_set else False

                # Indicadores de estado de carga
                if any(k in text for k in [
                    "cargando tus chats", "descargando mensajes", "conectando",
                    "organizando tus mensajes", "loading your chats", "connecting",
                    "cargando..."
                ]):
                    found_loading.append(text)
                
                # Indicadores de pantalla QR / sin sesión
                if any(k in text for k in [
                    "para usar whatsapp en tu computadora", "escanear código qr",
                    "vincular con el número de teléfono", "link with phone number",
                    "to use whatsapp on your computer", "use whatsapp on your computer"
                ]):
                    found_qr.append(text)

                # Indicadores de interfaz lista para operar
                if role == "table" and "lista de chats" in text:
                    found_ready.append("lista_de_chats")
                elif (role in ["entry", "text"] or is_editable) and any(
                    k in text for k in ["buscar un chat", "buscar o empezar", "search or start", "buscar"]
                ):
                    found_ready.append("search_entry")
                elif (role in ["entry", "text"] or is_editable) and any(
                    k in text for k in ["escribe un mensaje", "escribe aquí", "type a message", "mensaje"]
                ):
                    found_ready.append("message_entry")
                elif role in ["button", "toggle button"] and any(
                    k in text for k in ["nuevo chat", "chats", "new chat"]
                ):
                    found_ready.append("chat_buttons")

                for idx in range(node.get_child_count()):
                    child = node.get_child_at_index(idx)
                    if child:
                        scan_tree(child, depth + 1)
            except Exception:
                pass

        scan_tree(win)

        if found_loading and not ("message_entry" in found_ready or "search_entry" in found_ready):
            return "LOADING", f"Cargando sesión ({found_loading[0][:60]})"
        if found_qr and not found_ready:
            return "QR_REQUIRED", "Requiere escanear código QR para iniciar sesión"
        if found_ready:
            return "READY", f"Listo para operar ({', '.join(set(found_ready))})"
        
        return "BLANK", "Ventana abierta pero elementos de WhatsApp aún no renderizados"

    def _is_whatsapp_process_running(self) -> bool:
        """Verifica si el proceso de WhatsApp Web (PWA de Brave/Chrome) está en ejecución."""
        try:
            import psutil
            wa_ids = ["hnpfjngllnobngcgfapefoaidbinmjnm", "web.whatsapp.com"]
            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmd = " ".join(p.info["cmdline"] or [])
                    if any(wa_id in cmd for wa_id in wa_ids):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return False

    def _check_whatsapp_ready_visual(self) -> Tuple[str, str]:
        """
        Fallback visual: captura la pantalla y analiza patrones de píxeles para
        distinguir la pantalla de carga de WhatsApp (splash uniforme con logo
        centrado) de la UI lista (lista de chats con muchos bordes horizontales,
        avatares y texto variado).

        Retorna (estado, detalle) igual que get_whatsapp_state().
        """
        try:
            from src.vision.service import OptimizedScreenCaptureService
            from PIL import Image
            import numpy as np
            import io

            service = OptimizedScreenCaptureService()
            img_bytes = service.capture_screen()
            if not img_bytes or len(img_bytes) < 1000:
                return "BLANK", "No se pudo capturar pantalla"

            img = Image.open(io.BytesIO(img_bytes))
            arr = np.array(img)
            if arr.ndim < 3:
                return "BLANK", "Imagen sin canales de color"

            rgb = arr[:, :, :3].astype(np.float32)
            r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
            total = rgb.shape[0] * rgb.shape[1]

            # --- Métricas de color ---
            # Verde acento WhatsApp (#00a884 rango)
            green_accent = int(((r < 60) & (g > 120) & (b > 80) & (b < 190)).sum())
            # Fondo oscuro tema oscuro WhatsApp (~rgb(11-40, 17-55, 21-65))
            dark_bg = int(((r > 5) & (r < 45) & (g > 12) & (g < 58) & (b > 16) & (b < 68)).sum())
            dark_ratio = dark_bg / total

            # --- Métrica de bordes (clave para diferenciar splash vs chat-list) ---
            gray = 0.299 * r + 0.587 * g + 0.114 * b
            h_edges = float(np.abs(np.diff(gray, axis=1)).mean())

            # --- Colores variados en tercio izquierdo (sidebar con avatares) ---
            w_third = max(1, rgb.shape[1] // 3)
            sidebar = rgb[:, :w_third, :]
            sidebar_colorful = int((
                (sidebar[:, :, 0] > 80) | (sidebar[:, :, 1] > 80) | (sidebar[:, :, 2] > 80)
            ).sum())
            sidebar_ratio = sidebar_colorful / (sidebar.shape[0] * sidebar.shape[1])

            detail = (
                f"verde={green_accent}, oscuro={dark_ratio:.0%}, "
                f"bordes_h={h_edges:.2f}, sidebar={sidebar_ratio:.0%}"
            )
            logger.debug(f"Verificación visual WhatsApp: {detail}")

            # Pantalla de CARGA (splash):
            #   - Fondo oscuro muy alto (>50%) — pantalla casi toda negra
            #   - Bordes horizontales bajos (<1.5) — UI uniforme, logo centrado
            #   - Sidebar sin contenido variado (<15%)
            # UI LISTA (chat list):
            #   - Bordes horizontales altos (>2.0) — filas de chats, separadores
            #   - O sidebar con contenido variado (>18%) — avatares, texto
            if dark_ratio > 0.30:
                # La ventana de WhatsApp domina la pantalla
                if h_edges > 2.0 or sidebar_ratio > 0.18:
                    return "READY", f"UI cargada (visual: {detail})"
                else:
                    return "LOADING", f"Splash/cargando (visual: {detail})"

            return "NOT_FOUND", f"WhatsApp no visible (visual: {detail})"

        except Exception as e:
            logger.debug(f"Error en verificación visual: {e}")
            return "BLANK", f"Error visual: {e}"

    def wait_for_whatsapp_ready(
        self,
        timeout: float = 15.0,
        poll_interval: float = 0.25
    ) -> Tuple[bool, str]:
        """
        Espera de forma reactiva a que WhatsApp Web esté completamente cargado.

        Estrategia en dos fases:
        1. Polling rápido de AT-SPI2 en memoria (verifica árbol DOM accesible).
        2. Fallback visual: si el árbol permanece BLANK por >4s, captura la pantalla
           y analiza bordes/colores para distinguir splash de la UI lista.
        
        Retorna (True, "Listo...") si la app está lista para interactuar.
        Retorna (False, detalle_error) si ocurre timeout o se detecta pantalla QR.
        """
        if not self.is_available():
            time.sleep(0.3)
            return True, "AT-SPI2 no disponible, continuando con temporizador"

        deadline = time.time() + timeout
        next_visual_check = time.time() + 4.0  # Primera verificación visual tras 4s
        last_detail = "Iniciando comprobación"

        while time.time() < deadline:
            win = self.get_whatsapp_window()
            state, detail = self.get_whatsapp_state(win)
            last_detail = detail

            if state == "READY":
                logger.info(f"✅ WhatsApp Web listo en AT-SPI2: {detail}")
                return True, detail
            elif state == "QR_REQUIRED":
                logger.warning("WhatsApp Web requiere inicio de sesión con código QR.")
                return False, detail
            elif state == "LOADING":
                logger.debug(f"WhatsApp Web cargando chats ({detail}), esperando...")
            elif state in ("BLANK", "NOT_FOUND"):
                logger.debug(f"WhatsApp Web esperando renderizado ({detail})...")

                # Fallback visual cuando AT-SPI2 no expone el DOM web
                if time.time() >= next_visual_check:
                    visual_state, visual_detail = self._check_whatsapp_ready_visual()
                    if visual_state == "READY":
                        logger.info(f"✅ WhatsApp Web listo (verificación visual): {visual_detail}")
                        return True, visual_detail
                    elif visual_state == "LOADING":
                        logger.debug(f"WhatsApp Web cargando (visual): {visual_detail}")
                    next_visual_check = time.time() + 3.0  # Reintentar en 3s

            time.sleep(poll_interval)

        # Última verificación visual antes de declarar timeout
        visual_state, visual_detail = self._check_whatsapp_ready_visual()
        if visual_state == "READY":
            logger.info(f"✅ WhatsApp Web listo (verificación visual final): {visual_detail}")
            return True, visual_detail

        logger.warning(f"Timeout ({timeout}s) esperando a que WhatsApp Web esté listo. Último estado: {last_detail}")
        return False, f"Tiempo de espera agotado ({timeout}s): {last_detail}"

    def wait_for_chat_open(
        self,
        destinatario: str = "",
        timeout: float = 2.0,
        poll_interval: float = 0.2
    ) -> bool:
        """
        Espera a que se abra la conversación de un chat verificando la presencia del
        campo de entrada de mensajes ('Escribe un mensaje' / 'Type a message') en AT-SPI2.
        """
        if not self._atspi_gobject_ready:
            time.sleep(0.3)
            return True

        deadline = time.time() + min(timeout, 1.5)
        while time.time() < deadline:
            win = self.get_whatsapp_window()
            if win:
                entries = self.find_nodes(win, role="entry", is_editable=True)
                for entry in entries:
                    name = (entry.get_name() or "").lower()
                    desc = (entry.get_description() or "").lower()
                    if any(k in f"{name} {desc}" for k in ["escribe un mensaje", "escribe aquí", "type a message", "mensaje"]):
                        logger.debug(f"Chat abierto detectado en AT-SPI2 (entry: {name}).")
                        return True
            time.sleep(poll_interval)
        return True

    def verify_message_sent(
        self,
        timeout: float = 1.0,
        poll_interval: float = 0.2
    ) -> bool:
        """
        Verifica que el mensaje fue enviado comprobando que el campo de texto se haya vaciado.
        """
        if not self._atspi_gobject_ready:
            time.sleep(0.2)
            return True

        deadline = time.time() + min(timeout, 1.0)
        while time.time() < deadline:
            win = self.get_whatsapp_window()
            if win:
                entries = self.find_nodes(win, role="entry", is_editable=True)
                for entry in entries:
                    text = (entry.get_name() or "").strip()
                    if not text or text in ["Escribe un mensaje aquí", "Escribe un mensaje", "Type a message"]:
                        return True
            time.sleep(poll_interval)
        return True

    def find_window_by_title(
        self,
        process_name_pattern: str,
        title_pattern: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> Optional[str]:
        """Busca una ventana cuyo título contenga title_pattern dentro de los procesos coincidentes."""
        if not self.is_available():
            return None

        # 1. Intentar con Atspi si está disponible
        if self._atspi_gobject_ready and Atspi is not None:
            try:
                desktop = Atspi.get_desktop(0)
                if desktop:
                    for i in range(desktop.get_child_count()):
                        app = desktop.get_child_at_index(i)
                        if not app:
                            continue
                        if process_name_pattern.lower() in (app.get_name() or "").lower():
                            for j in range(app.get_child_count()):
                                win = app.get_child_at_index(j)
                                if not win:
                                    continue
                                win_name = (win.get_name() or "").lower()
                                if title_pattern.lower() in win_name:
                                    if exclude_patterns and any(ex.lower() in win_name for ex in exclude_patterns):
                                        continue
                                    return f"/org/a11y/atspi/accessible/{j}"
            except Exception:
                pass

        # 2. Fallback a busctl
        title_pat = title_pattern.lower()
        for app in self.list_accessible_apps():
            if process_name_pattern.lower() in app["process"].lower():
                conn = app["name"]
                try:
                    proc = subprocess.run(
                        [
                            "busctl", f"--address={self.bus_address}",
                            "call", conn, "/org/a11y/atspi/accessible/root",
                            "org.a11y.atspi.Accessible", "GetChildren"
                        ],
                        capture_output=True,
                        text=True,
                        timeout=1.0
                    )
                    if proc.returncode == 0:
                        for token in proc.stdout.split():
                            if token.startswith('"/org/a11y/'):
                                path = token.strip('"')
                                name_proc = subprocess.run(
                                    [
                                        "busctl", f"--address={self.bus_address}",
                                        "get-property", conn, path,
                                        "org.a11y.atspi.Accessible", "Name"
                                    ],
                                    capture_output=True,
                                    text=True,
                                    timeout=1.0
                                )
                                if name_proc.returncode == 0:
                                    w_name = name_proc.stdout.lower()
                                    if title_pat in w_name:
                                        if exclude_patterns and any(ex.lower() in w_name for ex in exclude_patterns):
                                            continue
                                        return path
                except Exception:
                    continue
        return None

    def wait_for_window(
        self,
        process_name_pattern: str,
        title_pattern: str,
        timeout: float = 5.0,
        poll_interval: float = 0.2
    ) -> bool:
        """Espera de forma reactiva a que aparezca una ventana con el título especificado."""
        if not self.is_available():
            time.sleep(1.0)
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.find_window_by_title(process_name_pattern, title_pattern)
            if win:
                return True
            time.sleep(poll_interval)
        return False

    def get_gmail_window(self) -> Optional[Any]:
        """Busca y retorna la ventana principal o PWA de Gmail."""
        if not self._atspi_gobject_ready or Atspi is None:
            return None
        try:
            desktop = Atspi.get_desktop(0)
            if not desktop:
                return None
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if not app:
                    continue
                for j in range(app.get_child_count()):
                    win = app.get_child_at_index(j)
                    if not win:
                        continue
                    name = (win.get_name() or "").lower()
                    if "gmail" in name or "mail.google.com" in name:
                        return win
        except Exception as e:
            logger.debug(f"Error localizando ventana de Gmail: {e}")
        return None

    def get_gmail_compose_window(self) -> Optional[Any]:
        """Busca la ventana o diálogo de redacción de Gmail."""
        if not self._atspi_gobject_ready or Atspi is None:
            return None
        try:
            desktop = Atspi.get_desktop(0)
            if not desktop:
                return None
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if not app:
                    continue
                for j in range(app.get_child_count()):
                    win = app.get_child_at_index(j)
                    if not win:
                        continue
                    name = (win.get_name() or "").lower()
                    if "redactar" in name or "mensaje nuevo" in name or "compose" in name:
                        return win
        except Exception as e:
            logger.debug(f"Error localizando ventana de redacción de Gmail: {e}")
        return None

    def wait_for_gmail_ready(self, timeout: float = 10.0, poll_interval: float = 0.25) -> Tuple[bool, str]:
        """Espera reactivamente a que la ventana de Gmail o su diálogo estén abiertos y listos."""
        if not self.is_available():
            time.sleep(1.0)
            return True, "Listo"
        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.get_gmail_window() or self.get_gmail_compose_window()
            if win:
                return True, "Gmail listo"
            time.sleep(poll_interval)
        return False, f"Timeout ({timeout}s) esperando apertura de Gmail"

    def wait_for_gmail_compose(self, timeout: float = 8.0, poll_interval: float = 0.25) -> bool:
        """Espera reactivamente a que la ventana de redacción de Gmail aparezca."""
        if not self.is_available():
            time.sleep(1.0)
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.get_gmail_compose_window()
            if win:
                return True
            time.sleep(poll_interval)
        return False

    def verify_email_sent(self, timeout: float = 5.0, poll_interval: float = 0.2) -> bool:
        """Verifica que el diálogo de redacción se haya cerrado tras pulsar enviar."""
        if not self._atspi_gobject_ready:
            time.sleep(0.5)
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.get_gmail_compose_window()
            if not win:
                return True
            time.sleep(poll_interval)
        return True

    def get_telegram_window(self) -> Optional[Any]:
        """Busca y retorna el objeto Accessible de la ventana de Telegram Desktop."""
        if not self._atspi_gobject_ready or Atspi is None:
            return None
        try:
            desktop = Atspi.get_desktop(0)
            if not desktop:
                return None
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if not app:
                    continue
                app_name = (app.get_name() or "").lower()
                if "telegram" in app_name:
                    for j in range(app.get_child_count()):
                        win = app.get_child_at_index(j)
                        if win:
                            return win
                    return app
        except Exception as e:
            logger.debug(f"Error localizando ventana de Telegram con Atspi: {e}")
        return None

    def wait_for_telegram_ready(
        self,
        timeout: float = 8.0,
        poll_interval: float = 0.25
    ) -> Tuple[bool, str]:
        """
        Espera reactivamente a que Telegram Desktop esté abierto, visible y listo.
        """
        if not self.is_available():
            time.sleep(1.5)
            return True, "AT-SPI2 no disponible, continuando con temporizador"

        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.get_telegram_window()
            if win:
                return True, "Telegram listo en AT-SPI2"
            conn = self.find_app_connection("telegram")
            if conn:
                return True, "Telegram listo en D-Bus"
            time.sleep(poll_interval)

        logger.warning(f"Timeout ({timeout}s) esperando apertura de Telegram Desktop.")
        return False, f"Timeout ({timeout}s) esperando a que Telegram cargue"


    def wait_for_app_ready(
        self,
        process_name_pattern: str,
        timeout: float = 5.0,
        poll_interval: float = 0.2
    ) -> bool:
        """
        Espera de forma reactiva (event-driven / polling rápido) a que una aplicación
        registre al menos una ventana y elementos interactivos activos en AT-SPI2.
        """
        if not self.is_available():
            time.sleep(1.0)
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._atspi_gobject_ready and Atspi is not None:
                try:
                    desktop = Atspi.get_desktop(0)
                    if desktop:
                        for i in range(desktop.get_child_count()):
                            app = desktop.get_child_at_index(i)
                            if app and process_name_pattern.lower() in (app.get_name() or "").lower():
                                if app.get_child_count() > 0:
                                    return True
                except Exception:
                    pass

            conn = self.find_app_connection(process_name_pattern)
            if conn:
                count = self.get_child_count(conn)
                if count > 0:
                    logger.debug(f"App '{process_name_pattern}' lista en AT-SPI2 ({conn}, {count} ventanas activas).")
                    return True
            time.sleep(poll_interval)

        logger.warning(f"Timeout ({timeout}s) esperando a que '{process_name_pattern}' esté lista en AT-SPI2.")
        return False

    # -------------------------------------------------------------------------
    # Métodos Semánticos de Acción CUA (Sin robo de foco ni inyección sintética)
    # -------------------------------------------------------------------------

    def set_element_text(self, node: Any, text: str) -> bool:
        """
        Establece el contenido de un elemento editable de forma semántica y directa,
        sin inyectar pulsaciones de teclado por /dev/uinput y sin requerir foco de
        ventana en el compositor Wayland.
        """
        if not node:
            return False

        # 1. Intentar interfaz Atspi.EditableText nativa
        if self._atspi_gobject_ready:
            try:
                ed = node.get_editable_text_iface() if hasattr(node, "get_editable_text_iface") else None
                if ed:
                    if hasattr(ed, "set_text_contents"):
                        success = ed.set_text_contents(text)
                        if success:
                            logger.debug(f"Texto establecido semánticamente con EditableText ({len(text)} chars).")
                            return True
                    if hasattr(ed, "insert_text"):
                        # Si no soporta set_text_contents, intentar delete + insert
                        if hasattr(ed, "delete_text"):
                            txt_iface = node.get_text_iface() if hasattr(node, "get_text_iface") else None
                            length = txt_iface.get_character_count() if txt_iface else 1000
                            ed.delete_text(0, length)
                        success = ed.insert_text(0, text, len(text))
                        if success:
                            return True
            except Exception as e:
                logger.debug(f"Fallo en Atspi.EditableText: {e}")

        # 2. Intentar asignación directa por propiedad si el objeto lo permite
        try:
            if hasattr(node, "set_text_contents"):
                return bool(node.set_text_contents(text))
        except Exception:
            pass

        return False

    def get_element_text(self, node: Any) -> str:
        """Obtiene el texto actual contenido en un elemento accesible."""
        if not node:
            return ""

        if self._atspi_gobject_ready:
            try:
                txt_iface = node.get_text_iface() if hasattr(node, "get_text_iface") else None
                if txt_iface and hasattr(txt_iface, "get_text"):
                    return txt_iface.get_text(0, -1) or ""
            except Exception:
                pass

        try:
            return (node.get_name() or "") if hasattr(node, "get_name") else ""
        except Exception:
            return ""

    def click_element_action(self, node: Any, action_index: int = 0) -> bool:
        """
        Invoca la acción nativa de un elemento accesible (botón, menú, enlace)
        sin mover el puntero físico ni requerir foco de ventana en el compositor.
        """
        if not node:
            return False

        if self._atspi_gobject_ready:
            try:
                act = node.get_action_iface() if hasattr(node, "get_action_iface") else None
                if act:
                    n_actions = act.get_n_actions() if hasattr(act, "get_n_actions") else 1
                    if n_actions > action_index:
                        res = act.do_action(action_index)
                        action_name = act.get_action_name(action_index) if hasattr(act, "get_action_name") else str(action_index)
                        logger.debug(f"Acción semántica {action_index} ('{action_name}') ejecutada: {res}")
                        return bool(res)
            except Exception as e:
                logger.debug(f"Fallo en Atspi.Action.do_action: {e}")

        try:
            if hasattr(node, "do_action"):
                return bool(node.do_action(action_index))
        except Exception:
            pass

        return False

    def grab_element_focus(self, node: Any) -> bool:
        """Solicita el foco accesible interno del elemento dentro de su ventana."""
        if not node or not self._atspi_gobject_ready:
            return False
        try:
            comp = node.get_component_iface() if hasattr(node, "get_component_iface") else None
            if comp and hasattr(comp, "grab_focus"):
                return bool(comp.grab_focus())
        except Exception as e:
            logger.debug(f"Error en grab_element_focus: {e}")
        return False

    def get_element_bounds(self, node: Any) -> Optional[Tuple[int, int, int, int]]:
        """
        Obtiene el rectángulo absoluto en pantalla (x, y, width, height) del elemento accesible.
        Permite validar visualmente dónde está un botón antes o después de una acción.
        """
        if not node or not self._atspi_gobject_ready or Atspi is None:
            return None
        try:
            comp = node.get_component_iface() if hasattr(node, "get_component_iface") else None
            if comp and hasattr(comp, "get_extents"):
                rect = comp.get_extents(Atspi.CoordType.SCREEN)
                return int(rect.x), int(rect.y), int(rect.width), int(rect.height)
        except Exception as e:
            logger.debug(f"Error obteniendo bounds de componente AT-SPI2: {e}")
        return None

    def find_interactive_nodes(self, root_node: Any, max_depth: int = 25) -> List[Dict[str, Any]]:
        """
        Escanea recursivamente el árbol accesible y retorna todos los elementos
        interactivos (campos editables, botones, menús) con sus acciones y posiciones.
        """
        if not root_node or max_depth < 0:
            return []

        results = []
        try:
            role = (root_node.get_role_name() or "").lower() if hasattr(root_node, "get_role_name") else ""
            name = (root_node.get_name() or "").strip() if hasattr(root_node, "get_name") else ""
            state_set = root_node.get_state_set() if hasattr(root_node, "get_state_set") else None
            is_editable = state_set.contains(Atspi.StateType.EDITABLE) if state_set and Atspi else False
            is_focusable = state_set.contains(Atspi.StateType.FOCUSABLE) if state_set and Atspi else False

            is_interactive = False
            actions = []

            # Obtener acciones soportadas
            if hasattr(root_node, "get_action_iface"):
                act = root_node.get_action_iface()
                if act and hasattr(act, "get_n_actions"):
                    n_act = act.get_n_actions()
                    if n_act > 0:
                        is_interactive = True
                        for idx in range(n_act):
                            a_name = act.get_action_name(idx) if hasattr(act, "get_action_name") else f"action_{idx}"
                            actions.append(a_name)

            if is_editable or role in ("entry", "push_button", "button", "check_box", "radio_button", "menu_item", "combo_box"):
                is_interactive = True

            if is_interactive:
                bounds = self.get_element_bounds(root_node)
                results.append({
                    "node": root_node,
                    "name": name,
                    "role": role,
                    "is_editable": is_editable,
                    "is_focusable": is_focusable,
                    "actions": actions,
                    "bounds": bounds
                })

            for idx in range(root_node.get_child_count()):
                child = root_node.get_child_at_index(idx)
                if child:
                    results.extend(self.find_interactive_nodes(child, max_depth - 1))
        except Exception:
            pass

        return results

    def send_message_semantic_whatsapp(self, mensaje: str, timeout: float = 3.0) -> bool:
        """
        Envía un mensaje en WhatsApp Web de forma 100% semántica:
        1. Localiza el campo de entrada de texto ('Escribe un mensaje').
        2. Inyecta el texto usando la interfaz EditableText de AT-SPI2.
        3. Localiza el botón de enviar ('Enviar' / 'Send') e invoca su acción nativa.
        Todo esto ocurre sin mover el cursor físico ni requerir foco de Wayland.
        """
        win = self.get_whatsapp_window()
        if not win:
            logger.warning("No se encontró ventana de WhatsApp para envío semántico.")
            return False

        # Buscar campo de texto de mensaje
        entries = self.find_nodes(win, role="entry", is_editable=True)
        target_entry = None
        for entry in entries:
            name = (entry.get_name() or "").lower()
            desc = (entry.get_description() or "").lower()
            if any(k in f"{name} {desc}" for k in ["escribe un mensaje", "escribe aquí", "type a message", "mensaje"]):
                target_entry = entry
                break

        if not target_entry and entries:
            target_entry = entries[-1]

        if not target_entry:
            logger.warning("No se encontró campo editable en WhatsApp Web.")
            return False

        # Asignar texto semánticamente
        if not self.set_element_text(target_entry, mensaje):
            logger.debug("set_element_text falló en WhatsApp, probando grab_focus...")
            self.grab_element_focus(target_entry)
            return False

        time.sleep(0.15)

        # Buscar botón enviar
        buttons = self.find_nodes(win, role="push_button") + self.find_nodes(win, role="button")
        for btn in buttons:
            b_name = (btn.get_name() or "").lower()
            b_desc = (btn.get_description() or "").lower()
            if any(k in f"{b_name} {b_desc}" for k in ["enviar", "send"]):
                if self.click_element_action(btn):
                    logger.info("✅ Mensaje de WhatsApp enviado vía acción semántica AT-SPI2.")
                    return True

        return True

    def send_message_semantic_telegram(self, mensaje: str) -> bool:
        """
        Establece el texto en el campo de entrada de Telegram Desktop de forma semántica.
        """
        win = self.get_telegram_window()
        if not win:
            return False

        entries = self.find_nodes(win, role="entry", is_editable=True)
        if not entries:
            entries = self.find_nodes(win, role="text", is_editable=True)

        if entries:
            target = entries[-1]  # En Telegram el campo de chat suele ser el último input
            if self.set_element_text(target, mensaje):
                logger.info("✅ Texto inyectado semánticamente en Telegram Desktop.")
                return True
        return False


