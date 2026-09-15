import asyncio
import json
import os
from typing import Dict, Any, List, Optional
from src.utils.logging import get_logger

logger = get_logger("mcp.client")


class MCPClient:
    """
    Cliente asíncrono de Model Context Protocol (MCP) vía JSON-RPC 2.0 sobre stdio.
    Permite interactuar con cualquier servidor MCP (Node.js, Python, binarios nativos).
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None
    ):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd

        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def start(self) -> None:
        """Inicia el proceso del servidor MCP y la tarea de lectura continua."""
        if self._is_running:
            return

        logger.info(f"Iniciando servidor MCP [{self.name}]: {self.command} {' '.join(self.args)}")
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
                cwd=self.cwd
            )
            self._is_running = True
            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())

            # Realizar Handshake de inicialización MCP
            await self._initialize()
            logger.info(f"Servidor MCP [{self.name}] inicializado correctamente.")
        except Exception as e:
            logger.error(f"Fallo al iniciar servidor MCP [{self.name}]: {e}")
            await self.close()
            raise

    async def _read_stdout(self):
        """Lee respuestas JSON-RPC línea por línea desde stdout."""
        while self._is_running and self._process and self._process.stdout:
            line = await self._process.stdout.readline()
            if not line:
                break

            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    future = self._pending_requests.pop(req_id)
                    if not future.done():
                        if "error" in msg:
                            future.set_exception(RuntimeError(msg["error"].get("message", str(msg["error"]))))
                        else:
                            future.set_result(msg.get("result", {}))
            except json.JSONDecodeError:
                logger.debug(f"[{self.name} stdout no-json]: {line_str}")

    async def _read_stderr(self):
        """Lee logs de depuración desde stderr del servidor."""
        while self._is_running and self._process and self._process.stderr:
            line = await self._process.stderr.readline()
            if not line:
                break
            line_str = line.decode("utf-8").strip()
            if line_str:
                logger.debug(f"[{self.name} stderr]: {line_str}")

    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Envía una petición JSON-RPC 2.0 y espera su respuesta asíncrona."""
        if not self._is_running or not self._process or not self._process.stdin:
            raise RuntimeError(f"Servidor MCP [{self.name}] no está en ejecución.")

        self._request_id += 1
        req_id = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params if params is not None else {}
        }

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[req_id] = future

        msg_bytes = (json.dumps(payload) + "\n").encode("utf-8")
        self._process.stdin.write(msg_bytes)
        await self._process.stdin.drain()

        try:
            return await asyncio.wait_for(future, timeout=30.0)
        finally:
            # Evitar fuga de memoria: si hubo timeout, la respuesta tardía del
            # servidor se ignora limpiamente en lugar de quedar en el dict.
            self._pending_requests.pop(req_id, None)

    async def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Envía una notificación JSON-RPC (sin esperar respuesta)."""
        if not self._is_running or not self._process or not self._process.stdin:
            return

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {}
        }
        msg_bytes = (json.dumps(payload) + "\n").encode("utf-8")
        self._process.stdin.write(msg_bytes)
        await self._process.stdin.drain()

    async def _initialize(self) -> None:
        """Handshake según especificación MCP."""
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "Atlas-Voice-Agent",
                "version": "1.0.0"
            }
        }
        await self.send_request("initialize", init_params)
        await self.send_notification("notifications/initialized")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Solicita la lista de herramientas disponibles en el servidor MCP."""
        res = await self.send_request("tools/list", {})
        return res.get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Ejecuta una herramienta en el servidor MCP."""
        return await self.send_request("tools/call", {
            "name": name,
            "arguments": arguments
        })

    async def close(self) -> None:
        """Cierra la conexión y termina el subproceso."""
        self._is_running = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()

        for req_id, future in list(self._pending_requests.items()):
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
