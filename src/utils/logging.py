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
    level: int = logging.INFO,
    log_file: Optional[str] = "latest_session.log"
) -> logging.Logger:
    """
    Configura el sistema de logging estructurado centralizado para Atlas.
    """
    root_logger = logging.getLogger("atlas")
    root_logger.setLevel(level)

    # Evitar duplicar handlers si ya se configuró
    if root_logger.handlers:
        return root_logger

    # Handler de Consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_fmt = ColoredFormatter(
        fmt="%(asctime)s [%(levelname_colored)s] [%(name)s]: %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # Handler de Archivo (sin colores ANSI)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
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
