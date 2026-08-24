import asyncio
import os
import socket
import subprocess
from src.utils.logging import get_logger

logger = get_logger("agents.tunnel")


def is_port_open(host: str = "127.0.0.1", port: int = 8317, timeout: float = 0.1) -> bool:
    """Verifica si el puerto local está abierto de forma ultrarrápida (100ms)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def ensure_cliproxy_tunnel(port: int = 8317) -> bool:
    """
    Verifica e inicia el túnel SSH a CLIProxy en segundo plano sin ralentizar el arranque de Atlas.
    Se ejecuta de forma asíncrona concurrente con el inicio del asistente de voz.
    """
    try:
        # 1. Comprobación rápida en hilo worker (<5ms)
        port_open = await asyncio.to_thread(is_port_open, "127.0.0.1", port, 0.1)
        if port_open:
            logger.debug(f"Túnel CLIProxy en :{port} ya está activo.")
            return True

        logger.info(f"Iniciando túnel CLIProxy (puerto {port}) en segundo plano...")

        # 2. Intentar iniciar vía systemctl --user
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "--user", "start", "cliproxy-tunnel",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await proc.wait()
        except Exception as e:
            logger.debug(f"systemctl para cliproxy-tunnel no disponible: {e}")

        # 3. Esperar hasta que el puerto responda (máximo 5s en segundo plano)
        for _ in range(10):
            await asyncio.sleep(0.5)
            if await asyncio.to_thread(is_port_open, "127.0.0.1", port, 0.1):
                logger.info(f"⚡ Túnel CLIProxy conectado exitosamente en :{port}.")
                return True

        # 4. Fallback directo en caso de que el servicio systemd no esté configurado
        try:
            ssh_config = os.path.expanduser("~/.ssh/config")
            if os.path.exists(ssh_config):
                subprocess.Popen(
                    ["/usr/bin/ssh", "-N", "-F", ssh_config, "cliproxy-tunnel"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                await asyncio.sleep(1.0)
                if await asyncio.to_thread(is_port_open, "127.0.0.1", port, 0.1):
                    logger.info(f"⚡ Túnel CLIProxy conectado vía SSH fallback en :{port}.")
                    return True
        except Exception as e:
            logger.debug(f"SSH fallback error: {e}")

        logger.warning(f"No se pudo verificar el túnel CLIProxy en :{port}.")
        return False
    except Exception as e:
        logger.debug(f"Error asegurando túnel CLIProxy: {e}")
        return False
