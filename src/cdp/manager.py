"""
Gestor del ciclo de vida del navegador controlado por Atlas vía CDP.

Estrategia (validada contra la restricción de Chrome/Brave >= 136):
1. Sonda `http://127.0.0.1:<port>/json/version`. Si responde, hay un navegador
   con depuración activa (lanzado antes por Atlas o manualmente) y nos
   adjuntamos sin tocar nada más.
2. Si no, lanzamos una instancia dedicada con perfil propio PERSISTENTE
   (`~/.local/share/atlas/browser-profile`): las sesiones/cookies que el usuario
   inicie ahí sobreviven entre reinicios de Atlas. Es el único modo soportado
   desde Chromium 136, que ignora `--remote-debugging-port` con el perfil por
   defecto.
3. Modo headless desechable (trabajo paralelo sin sesión): perfil temporal en
   /tmp que se borra al cerrar. Jamás se usa para las cuentas del usuario.

Seguridad: el puerto escucha solo en loopback (comportamiento por defecto de
Chromium) y la URL del socket lleva un token GUID no adivinable.
"""
import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from src.utils.logging import get_logger
from .client import CDPConnection, CDPError

logger = get_logger("cdp.manager")


class BrowserNotFoundError(RuntimeError):
    """No se encontró ningún binario Chromium/Brave/Chrome en el sistema."""


_BROWSER_BINARIES = [
    "brave", "brave-browser",
    "google-chrome", "google-chrome-stable",
    "chromium", "chromium-browser",
]

_DEFAULT_PROFILE = os.path.expanduser("~/.local/share/atlas/browser-profile")


@dataclass
class BrowserTab:
    target_id: str
    title: str
    url: str
    type: str = "page"


class BrowserManager:
    """Descubre, lanza y gobierna la instancia de navegador controlada."""

    def __init__(self, port: int = 9222, profile_dir: Optional[str] = None,
                 headless: bool = False):
        self.port = port
        self.headless = headless
        # En headless el perfil es desechable por diseño (plan Fase 4/5).
        if headless:
            self.profile_dir = tempfile.mkdtemp(prefix="atlas-cdp-headless-")
        else:
            self.profile_dir = profile_dir or _DEFAULT_PROFILE
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._conn: Optional[CDPConnection] = None
        self._launch_lock = asyncio.Lock()
        self._owns_browser = False  # True si el proceso lo lanzó Atlas

    # ------------------------------------------------------------------
    # Descubrimiento y lanzamiento
    # ------------------------------------------------------------------
    @property
    def _version_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/json/version"

    async def _probe_version(self, timeout: float = 1.5) -> Optional[dict]:
        import aiohttp
        try:
            async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.get(self._version_url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            return None
        return None

    @staticmethod
    def _find_binary() -> str:
        for name in _BROWSER_BINARIES:
            path = shutil.which(name)
            if path:
                return path
        raise BrowserNotFoundError(
            "No se encontró ningún navegador compatible (brave, google-chrome, chromium)."
        )

    async def ensure_connected(self) -> CDPConnection:
        """Devuelve una conexión viva al navegador, lanzándolo si hace falta."""
        if self._conn and self._conn.is_open:
            return self._conn

        async with self._launch_lock:
            if self._conn and self._conn.is_open:
                return self._conn

            info = await self._probe_version()
            if not info:
                await self._launch_browser()
                info = await self._wait_for_devtools()

            ws_url = (info or {}).get("webSocketDebuggerUrl")
            if not ws_url:
                raise CDPError("El navegador respondió /json/version sin webSocketDebuggerUrl.")

            conn = CDPConnection(ws_url)
            await conn.connect()
            self._conn = conn
            logger.info(
                f"CDP conectado ({'headless' if self.headless else 'visible'}, "
                f"{'propio' if self._owns_browser else 'preexistente'}): "
                f"{(info or {}).get('Browser', '?')}"
            )
            return conn

    async def _launch_browser(self) -> None:
        binary = self._find_binary()
        os.makedirs(self.profile_dir, exist_ok=True)
        args = [
            binary,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",  # clientes websocket no-Chrome (Origin check)
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
        ]
        if self.headless:
            args.append("--headless=new")
            args.append("--disable-gpu")
        args.append("about:blank")

        logger.info(f"Lanzando navegador CDP: {binary} (perfil: {self.profile_dir})")
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # no heredar señales del terminal de Atlas
        )
        self._owns_browser = True

    async def _wait_for_devtools(self, timeout: float = 25.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self._proc and self._proc.returncode is not None:
                raise CDPError(
                    f"El navegador terminó al arrancar (rc={self._proc.returncode}). "
                    "¿Otra instancia usando el mismo puerto o perfil?"
                )
            info = await self._probe_version()
            if info:
                return info
            await asyncio.sleep(0.4)
        raise CDPError(f"Timeout ({timeout}s) esperando el endpoint DevTools en :{self.port}.")

    # ------------------------------------------------------------------
    # Pestañas
    # ------------------------------------------------------------------
    async def list_tabs(self) -> List[BrowserTab]:
        conn = await self.ensure_connected()
        result = await conn.send("Target.getTargets")
        tabs = []
        for t in result.get("targetInfos", []):
            if t.get("type") != "page":
                continue  # ignorar service workers, iframes OOPIF, etc.
            tabs.append(BrowserTab(
                target_id=t.get("targetId", ""),
                title=t.get("title", "") or "(sin título)",
                url=t.get("url", ""),
            ))
        return tabs

    async def open_tab(self, url: str = "about:blank"):
        """Crea pestaña y devuelve (target_id, PageController)."""
        from .page import PageController

        conn = await self.ensure_connected()
        result = await conn.send("Target.createTarget", {"url": url})
        target_id = result.get("targetId")
        if not target_id:
            raise CDPError("Target.createTarget no devolvió targetId.")
        page = await self.attach(target_id)
        return target_id, page

    async def attach(self, target_id: str):
        """Adjunta la conexión a una pestaña (flatten) y devuelve su PageController."""
        from .page import PageController

        conn = await self.ensure_connected()
        result = await conn.send("Target.attachToTarget",
                                 {"targetId": target_id, "flatten": True})
        session_id = result.get("sessionId")
        if not session_id:
            raise CDPError("Target.attachToTarget no devolvió sessionId.")
        page = PageController(conn=conn, session_id=session_id, target_id=target_id)
        await page.enable()
        return page

    async def close_tab(self, target_id: str) -> bool:
        conn = await self.ensure_connected()
        result = await conn.send("Target.closeTarget", {"targetId": target_id})
        return bool(result.get("success"))

    async def activate_tab(self, target_id: str) -> None:
        conn = await self.ensure_connected()
        await conn.send("Target.activateTarget", {"targetId": target_id})

    # ------------------------------------------------------------------
    # Cierre
    # ------------------------------------------------------------------
    async def shutdown(self) -> None:
        """Cierra la conexión. Solo mata el proceso si lo lanzó Atlas."""
        if self._conn:
            await self._conn.close()
            self._conn = None
        if self._owns_browser and self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        if self.headless and self.profile_dir.startswith(tempfile.gettempdir()):
            shutil.rmtree(self.profile_dir, ignore_errors=True)
