import logging
import sys
import os
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Formateador de consola con colores ANSI para niveles de log."""

    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Verde
        logging.WARNING: "\033[33m",   # Amarillo
        logging.ERROR: "\033[31m",     # Rojo
        logging.CRITICAL: "\033[1;31m" # Rojo negrita
    }
    RESET = "\033[0m"

    def __init__(self, fmt="[%(levelname_colored)s] %(message)s", datefmt=None):
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname_colored = f"{color}{record.levelname:<7}{self.RESET}"
        return super().format(record)


def setup_logging(
    console_level: int = logging.WARNING,
    file_level: int = logging.DEBUG,
    log_file: Optional[str] = "latest_session.log",
    level: Optional[int] = None
) -> logging.Logger:
    """
    Configura el sistema de logging estructurado centralizado para Atlas.
    Por defecto, mantiene la consola limpia (solo advertencias y errores) y guarda
    todos los detalles de depuración en el archivo de log.
    """
    if level is not None:
        console_level = level
        file_level = level

    root_logger = logging.getLogger("atlas")
    root_logger.setLevel(min(console_level, file_level))

    # Limpiar handlers previos si existían
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Handler de Consola (discreto y limpio)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_fmt = ColoredFormatter(
        fmt="%(asctime)s [%(levelname_colored)s] [%(name)s]: %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # Handler de Archivo (completo con timestamps y números de línea)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
            file_handler.setLevel(file_level)
            file_fmt = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] [%(name)s] (%(filename)s:%(lineno)d): %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_fmt)
            root_logger.addHandler(file_handler)
        except Exception as e:
            print(f"No se pudo inicializar log file {log_file}: {e}", file=sys.stderr)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Obtiene un logger hijo en el espacio de nombres 'atlas.*'."""
    if name.startswith("atlas."):
        return logging.getLogger(name)
    return logging.getLogger(f"atlas.{name}")
