import asyncio
import json
import os
import webbrowser
from typing import Set, Optional, Callable, Any
from aiohttp import web
from src.events.bus import EventBus
from src.events.base import Event
from src.utils.logging import get_logger

logger = get_logger("ui.overlay")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class WebOverlayServer:
    """
    Servidor Web y WebSocket para el HUD / Dashboard interactivo de Atlas en :7890.
    Sirve archivos estáticos (HTML/CSS/JS) y expone la API de Trayectoria en tiempo real.
    """

    def __init__(
        self,
        event_bus: EventBus,
        port: int = 7890,
        on_user_input_callback: Optional[Callable[[str], Any]] = None,
        on_toggle_pause_callback: Optional[Callable[[], Any]] = None,
        approval_manager=None,
        trajectory_manager=None,
        reminder_scheduler=None
    ):
        self.event_bus = event_bus
        self.port = port
        self.on_user_input = on_user_input_callback
        self.on_toggle_pause = on_toggle_pause_callback
        self.approval_manager = approval_manager
        self.trajectory_manager = trajectory_manager
        self.reminder_scheduler = reminder_scheduler

        self.app = web.Application()
        self.sockets: Set[web.WebSocketResponse] = set()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # Rutas HTTP y WebSocket
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/ws', self.handle_ws)
        self.app.router.add_get('/api/status', self.handle_api_status)
        self.app.router.add_get('/api/trajectory', self.handle_api_trajectory)
        self.app.router.add_get('/api/reminders', self.handle_api_reminders)

        # Servir estáticos
        if os.path.exists(STATIC_DIR):
            self.app.router.add_static('/static/', path=STATIC_DIR, name='static')

        # Suscribir al EventBus
        self.event_bus.subscribe_all(self._on_domain_event)

    async def handle_index(self, request: web.Request) -> web.Response:
        """Sirve el archivo index.html desde la carpeta estática."""
        index_file = os.path.join(STATIC_DIR, "index.html")
        if os.path.exists(index_file):
            with open(index_file, "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type='text/html')
        return web.Response(text="<h1>Atlas HUD</h1><p>Archivo index.html no encontrado.</p>", content_type='text/html')

    async def handle_api_status(self, request: web.Request) -> web.Response:
        """Endpoint de estado del sistema."""
        pending_hitl = len(self.approval_manager.list_pending()) if self.approval_manager else 0
        active_reminders = len(self.reminder_scheduler.list_pending()) if self.reminder_scheduler else 0
        return web.json_response({
            "status": "online",
            "clients_connected": len(self.sockets),
            "pending_hitl_approvals": pending_hitl,
            "active_reminders": active_reminders
        })

    async def handle_api_trajectory(self, request: web.Request) -> web.Response:
        """Endpoint de historial de pasos y trayectoria (ReAct timeline)."""
        steps = self.trajectory_manager.get_steps(limit=100) if self.trajectory_manager else []
        return web.json_response({
            "status": "ok",
            "total_steps": len(steps),
            "steps": steps
        })

    async def handle_api_reminders(self, request: web.Request) -> web.Response:
        """Endpoint de recordatorios activos."""
        reminders = [
            {"id": r.id, "mensaje": r.mensaje, "trigger_time": r.trigger_time, "is_cron": r.is_cron}
            for r in (self.reminder_scheduler.list_pending() if self.reminder_scheduler else [])
        ]
        return web.json_response({"reminders": reminders})

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Maneja la conexión WebSocket bidireccional con el navegador."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.add(ws)
        logger.info(f"Cliente HUD conectado desde {request.remote}. Total clientes: {len(self.sockets)}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")
                        if msg_type == "user_text":
                            text = data.get("text", "")
                            if self.on_user_input and text:
                                if asyncio.iscoroutinefunction(self.on_user_input):
                                    await self.on_user_input(text)
                                else:
                                    self.on_user_input(text)
                        elif msg_type == "toggle_pause":
                            if self.on_toggle_pause:
                                if asyncio.iscoroutinefunction(self.on_toggle_pause):
                                    await self.on_toggle_pause()
                                else:
                                    self.on_toggle_pause()
                        elif msg_type == "resolve_approval":
                            req_id = data.get("request_id")
                            approved = bool(data.get("approved", False))
                            if self.approval_manager and req_id:
                                self.approval_manager.resolve(req_id, approved, resolver="hud")
                    except Exception as e:
                        logger.error(f"Error procesando mensaje WebSocket entrante: {e}")
        finally:
            self.sockets.discard(ws)
            logger.info(f"Cliente HUD desconectado. Restantes: {len(self.sockets)}")
        return ws

    def _on_domain_event(self, event: Event) -> None:
        """Callback del EventBus; difunde eventos a todos los WebSockets conectados."""
        if not self.sockets:
            return

        event_name = event.__class__.__name__
        event_dict = {
            "type": event_name,
            "timestamp": getattr(event, "timestamp", "").isoformat() if hasattr(event, "timestamp") else "",
            "data": {}
        }

        for k, v in getattr(event, "__dict__", {}).items():
            if k not in ("context", "timestamp"):
                event_dict["data"][k] = v

        payload = json.dumps(event_dict)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(payload))
        except RuntimeError:
            pass

    async def _broadcast(self, payload: str) -> None:
        """Difunde a los clientes WebSocket conectados."""
        for ws in list(self.sockets):
            if not ws.closed:
                try:
                    await ws.send_str(payload)
                except Exception:
                    self.sockets.discard(ws)

    async def start(self, auto_open: bool = False) -> None:
        """Inicia el servidor HTTP y WebSocket."""
        logger.info(f"Iniciando Dashboard HUD en http://localhost:{self.port}")
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, '0.0.0.0', self.port)
        await self._site.start()
        logger.info(f"⚡ Dashboard HUD activo en http://localhost:{self.port}")

        if auto_open:
            try:
                webbrowser.open(f"http://localhost:{self.port}")
            except Exception:
                pass

    async def stop(self) -> None:
        """Detiene el servidor y cierra WebSockets."""
        for ws in list(self.sockets):
            await ws.close()
        self.sockets.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
