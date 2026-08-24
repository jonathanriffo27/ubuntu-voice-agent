import asyncio
from typing import Dict, Any, List
from src.mcp.client import MCPClient
from src.mcp.adapter import MCPToolAdapter
from src.tools.registry import ToolRegistry
from src.utils.logging import get_logger

logger = get_logger("mcp.manager")


class MCPServerManager:
    """
    Gestiona el ciclo de vida de los servidores MCP configurados en Atlas,
    descubre sus herramientas y las registra automáticamente en el ToolRegistry.
    """

    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}

    async def load_servers(
        self,
        servers_config: Dict[str, Any],
        registry: ToolRegistry
    ) -> List[MCPToolAdapter]:
        """
        Inicia los servidores definidos en la configuración y registra sus herramientas.
        """
        registered_tools: List[MCPToolAdapter] = []

        for name, srv_def in servers_config.items():
            if not isinstance(srv_def, dict):
                continue

            command = srv_def.get("command")
            if not command:
                logger.warning(f"Servidor MCP [{name}] omitido: no especifica 'command'.")
                continue

            args = srv_def.get("args", [])
            env = srv_def.get("env", {})
            cwd = srv_def.get("cwd")

            client = MCPClient(name=name, command=command, args=args, env=env, cwd=cwd)
            try:
                await client.start()
                self.clients[name] = client

                tools = await client.list_tools()
                logger.info(f"Servidor MCP [{name}] expone {len(tools)} herramienta(s).")

                for t_info in tools:
                    adapter = MCPToolAdapter(client, t_info, prefix=name)
                    try:
                        registry.register(adapter)
                        registered_tools.append(adapter)
                        logger.info(f"  🔧 Registrada herramienta MCP: {adapter.name}")
                    except ValueError as e:
                        logger.warning(f"No se pudo registrar {adapter.name}: {e}")

            except Exception as e:
                logger.error(f"No se pudo inicializar el servidor MCP [{name}]: {e}")

        return registered_tools

    async def shutdown_all(self) -> None:
        """Detiene todos los servidores MCP en ejecución de forma segura."""
        if not self.clients:
            return

        logger.info(f"Cerrando {len(self.clients)} servidor(es) MCP...")
        tasks = [client.close() for client in self.clients.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self.clients.clear()
