import logging
import os
import sys
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """
    Formateador de consola compacto: símbolo + nivel coloreado + módulo corto + mensaje.
    El modo 'verbose' (ATLAS_LOG_VERBOSE=1) restaura timestamp y nombre completo del módulo.
    """

    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Verde
        logging.WARNING: "\033[33m",   # Amarillo
        logging.ERROR: "\033[31m",     # Rojo
        logging.CRITICAL: "\033[1;31m" # Rojo negrita
    }
    SYMBOLS = {
        logging.DEBUG: "·",
        logging.INFO: "ℹ",
        logging.WARNING: "⚠",
        logging.ERROR: "✖",
        logging.CRITICAL: "✖✖"
    }
    RESET = "\033[0m"
    DIM = "\033[2m"

    def __init__(self, fmt=None, datefmt=None, verbose: Optional[bool] = None):
        super().__init__(fmt=fmt or "%(message)s", datefmt=datefmt)
        if verbose is None:
            verbose = os.environ.get("ATLAS_LOG_VERBOSE", "").strip() in ("1", "true", "yes")
        self.verbose = verbose
        self.datefmt = datefmt or "%H:%M:%S"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        symbol = self.SYMBOLS.get(record.levelno, " ")
        timestamp = self.formatTime(record, self.datefmt)
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        if self.verbose:
            return f"{timestamp} {color}[{record.levelname}]{self.RESET} [{record.name}]: {message}"

        # Compacto: '14:58:31 ⚠ google: mensaje' (módulo = último componente)
        short_name = record.name.rsplit(".", 1)[-1] if "." in record.name else record.name
        return f"{self.DIM}{timestamp}{self.RESET} {color}{symbol} {short_name}{self.RESET}: {message}"


def setup_logging(
    console_level: int = logging.WARNING,
    file_level: int = logging.DEBUG,
    log_file: Optional[str] = "latest_session.log",
    level: Optional[int] = None
) -> logging.Logger:
    """
    Configura el sistema de logging estructurado centralizado para Atlas.
    Consola limpia y compacta (advertencias+), archivo de log completo con detalles.
    Usa ATLAS_LOG_VERBOSE=1 para ver timestamps y módulos completos en consola.
    """
    if level is not None:
        console_level = level
        file_level = level

    root_logger = logging.getLogger("atlas")
    root_logger.setLevel(min(console_level, file_level))

    # Limpiar handlers previos si existían
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Handler de Consola (compacto por defecto)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ColoredFormatter(datefmt="%H:%M:%S"))
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
