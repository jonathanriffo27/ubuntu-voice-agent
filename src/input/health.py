"""
Diagnóstico y auto-recuperación de la cadena de input de Atlas.

Por qué existe: el 2026-09-14 se detectó ydotoold.service inactivo/disabled en la
máquina de desarrollo, lo que rompía silenciosamente toda la automatización GUI.
Este módulo verifica la cadena completa al arranque y repara lo reparable.
"""
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from src.input.backends import process_alive
from src.utils.logging import get_logger

logger = get_logger("input.health")


@dataclass
class InputHealthReport:
    """Resultado del diagnóstico de la cadena de input GUI."""
    ydotool_bin: bool = False
    ydotoold_running: bool = False
    ydotoold_autofixed: bool = False
    wl_copy: bool = False
    grim: bool = False
    atspi_bus: bool = False
    portal_desktop: bool = False
    backend_order: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True si hay al menos un backend de input funcional y clipboard."""
        return self.atspi_bus and self.wl_copy and (self.ydotoold_running or self.portal_desktop)


class InputHealth:
    """Verifica (y repara lo reparable) la cadena de automatización GUI."""

    YDOTOOL_SOCKET = f"/run/user/{os.getuid()}/.ydotool_socket"

    def check(self, autofix: bool = True) -> InputHealthReport:
        report = InputHealthReport()
        report.ydotool_bin = shutil.which("ydotool") is not None
        report.wl_copy = shutil.which("wl-copy") is not None
        report.grim = shutil.which("grim") is not None
        report.atspi_bus = os.path.exists(f"/run/user/{os.getuid()}/at-spi/bus")
        report.portal_desktop = self._check_portal()

        # ydotoold: socket presente Y daemon vivo. (El socket es DGRAM y no se
        # puede sondear conectando; un socket obsoleto con daemon muerto era un
        # falso positivo clásico — detectado en producción el 2026-09-14.)
        report.ydotoold_running = (
            os.path.exists(self.YDOTOOL_SOCKET) and process_alive("ydotoold")
        )
        if report.ydotool_bin and not report.ydotoold_running and autofix:
            report.ydotoold_autofixed = self._autofix_ydotoold()
            report.ydotoold_running = (
                os.path.exists(self.YDOTOOL_SOCKET) and process_alive("ydotoold")
            )

        # Orden recomendado de backends crudos (el AT-SPI2 semántico siempre va antes,
        # pero eso lo decide el ElementResolver, no este reporte).
        if report.ydotoold_running:
            report.backend_order.append("ydotool")
        if report.portal_desktop:
            report.backend_order.append("portal")

        # Acumular incidencias accionables
        if not report.ydotool_bin:
            report.issues.append("Binario 'ydotool' no instalado (sudo apt install ydotool).")
        elif not report.ydotoold_running:
            report.issues.append(
                "ydotoold no está corriendo y no se pudo auto-reparar. "
                "Ejecuta: systemctl --user enable --now ydotoold.service"
            )
        if not report.wl_copy:
            report.issues.append("'wl-copy' no instalado (paquete wl-clipboard): el pegado de texto fallará.")
        if not report.atspi_bus:
            report.issues.append("Bus AT-SPI2 no encontrado: la automatización semántica no funcionará.")
        if not report.grim:
            report.issues.append("'grim' no instalado (opcional): la captura de pantalla usará el portal (más lento).")
        if report.ydotoold_autofixed:
            report.issues.append("INFO: ydotoold.service fue levantado automáticamente.")

        return report

    def _autofix_ydotoold(self) -> bool:
        """Intenta levantar ydotoold.service vía systemd --user. Nunca lanza excepción."""
        import time
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "start", "ydotoold.service"],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode != 0:
                logger.debug(f"Autofix ydotoold falló: {proc.stderr.strip()}")
                return False
            # El daemon tarda unos ms en crear el socket tras el start;
            # sondear con reintentos hasta 1.5s en total.
            for _ in range(6):
                if os.path.exists(self.YDOTOOL_SOCKET) and process_alive("ydotoold"):
                    logger.info("🔧 [InputHealth] ydotoold.service levantado automáticamente.")
                    return True
                time.sleep(0.25)
            logger.debug("ydotoold arrancó pero el socket no respondió a tiempo.")
        except Exception as e:
            logger.debug(f"Autofix ydotoold no disponible: {e}")
        return False

    def _check_portal(self) -> bool:
        """Verifica que el portal XDG Desktop responde en el bus de sesión (vía gdbus, ligero)."""
        try:
            proc = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.freedesktop.DBus",
                 "--object-path", "/org/freedesktop/DBus",
                 "--method", "org.freedesktop.DBus.NameHasOwner",
                 "org.freedesktop.portal.Desktop"],
                capture_output=True, text=True, timeout=3
            )
            return proc.returncode == 0 and "true" in proc.stdout
        except Exception:
            return False


def log_startup_report(report: Optional[InputHealthReport] = None) -> InputHealthReport:
    """Helper: ejecuta el chequeo y lo registra en los logs de arranque de Atlas."""
    report = report or InputHealth().check()
    if report.ok:
        logger.info(
            f"🖱️ [InputHealth] OK. Backends: {' → '.join(report.backend_order) or 'ninguno'} | "
            f"AT-SPI2: ✅ | wl-copy: {'✅' if report.wl_copy else '❌'}"
        )
    else:
        logger.warning("🖱️ [InputHealth] Cadena de input degradada.")
    for issue in report.issues:
        logger.warning(f"🖱️ [InputHealth] {issue}")
    return report
