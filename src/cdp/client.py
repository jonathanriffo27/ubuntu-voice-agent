"""
Cliente CDP minimalista sobre un único websocket (asyncio + websockets).

Diseño:
- Un solo socket al endpoint del navegador (`webSocketDebuggerUrl` de
  `/json/version`). Las pestañas se multiplexan adjuntando `sessionId` a cada
  comando (Target.attachToTarget con flatten=True), evitando abrir un socket
  por pestaña.
- Tarea de recepción dedicada que resuelve Futures pendientes por `id` y
  reparte eventos a suscriptores.
- Timeouts por comando con limpieza de futures huérfanos.
"""
import asyncio
import itertools
import json
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger("cdp.client")


class CDPError(Exception):
    """Error devuelto por el propio navegador (payload `error` del protocolo)."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class CDPTimeoutError(CDPError):
    """El comando no obtuvo respuesta dentro del timeout."""


EventHandler = Callable[[Dict[str, Any]], None]


class CDPConnection:
    """Conexión al endpoint CDP del navegador con soporte de flatten sessions."""

    def __init__(self, ws_url: str, recv_max_size: int = 64 * 1024 * 1024):
        self._url = ws_url
        self._max_size = recv_max_size
        self._ws = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ids = itertools.count(1)
        self._pending: Dict[int, asyncio.Future] = {}
        self._handlers: Dict[Tuple[str, Optional[str]], list] = {}
        self._closed = asyncio.Event()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    async def connect(self, timeout: float = 10.0) -> None:
        import websockets

        self._ws = await asyncio.wait_for(
            websockets.connect(self._url, max_size=self._max_size),
            timeout=timeout,
        )
        self._closed.clear()
        self._recv_task = asyncio.create_task(self._recv_loop(), name="cdp-recv")

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._fail_pending(CDPError("conexión cerrada"))
        self._closed.set()

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    async def send(self, method: str, params: Optional[Dict[str, Any]] = None,
                   session_id: Optional[str] = None, timeout: float = 20.0) -> Dict[str, Any]:
        """Envía un comando CDP y espera su respuesta. Lanza CDPError/CDPTimeoutError."""
        if not self.is_open:
            raise CDPError("CDPConnection no está conectada.")
        msg_id = next(self._ids)
        payload: Dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send(json.dumps(payload))
            response = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise CDPTimeoutError(f"Timeout ({timeout}s) esperando respuesta a '{method}'")
        except Exception:
            self._pending.pop(msg_id, None)
            raise

        if "error" in response:
            err = response["error"]
            raise CDPError(err.get("message", "error CDP desconocido"), code=err.get("code"))
        return response.get("result", {})

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------
    def on_event(self, method: str, handler: EventHandler,
                 session_id: Optional[str] = None) -> None:
        """Suscribe `handler` a un evento CDP (opcionalmente filtrado por sesión)."""
        self._handlers.setdefault((method, session_id), []).append(handler)

    def off_event(self, method: str, handler: EventHandler,
                  session_id: Optional[str] = None) -> None:
        handlers = self._handlers.get((method, session_id))
        if handlers and handler in handlers:
            handlers.remove(handler)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    self._dispatch_event(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"CDP recv loop terminado: {e}")
        finally:
            self._fail_pending(CDPError("conexión CDP perdida"))
            self._closed.set()

    def _dispatch_event(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method", "")
        session = msg.get("sessionId")
        params = msg.get("params", {})
        for key in ((method, session), (method, None)):
            for handler in list(self._handlers.get(key, [])):
                try:
                    handler(params)
                except Exception as e:
                    logger.error(f"Error en handler de evento {method}: {e}")

    def _fail_pending(self, exc: Exception) -> None:
        for _, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
