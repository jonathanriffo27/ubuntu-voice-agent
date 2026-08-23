import logging
import tempfile
import os
from src.utils.logging import setup_logging, get_logger, ColoredFormatter


def test_colored_formatter():
    formatter = ColoredFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None
    )
    formatted = formatter.format(record)
    assert "INFO" in formatted
    assert "Test message" in formatted


def test_setup_logging_and_get_logger():
    fd, log_path = tempfile.mkstemp(suffix=".log")
    os.close(fd)

    try:
        logger = setup_logging(level=logging.DEBUG, log_file=log_path)
        child_logger = get_logger("brain.test")

        child_logger.info("Mensaje de prueba en logger estructurado")

        # Forzar flush
        for handler in logger.handlers:
            handler.flush()

        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "Mensaje de prueba en logger estructurado" in content
        assert "atlas.brain.test" in content
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)
