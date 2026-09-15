"""Tests del diagnóstico y auto-recuperación de la cadena de input (Fase 0).

Nota de diseño: el socket de ydotoold es DGRAM (no se puede sondear conectando),
así que el check es archivo-existe + proceso `ydotoold` vivo en /proc. Un socket
obsoleto con daemon muerto era un falso positivo detectado el 2026-09-14.
"""
import os
from unittest.mock import patch

from src.input.health import InputHealth, log_startup_report


class _Probe:
    """Estado mutable compartido entre la sonda de proceso y el subprocess falso."""

    def __init__(self, daemon_alive: bool):
        self.state = {"ydotoold": daemon_alive}

    def proc_alive(self, name: str) -> bool:
        return self.state["ydotoold"]

    def subprocess_run(self, cmd, **kwargs):
        class R:
            returncode = 0
            stdout = "(true,)"
            stderr = ""
        if cmd[0] == "systemctl":
            self.state["ydotoold"] = True  # el servicio levanta el daemon
        return R()


def _patched(probe: _Probe):
    atspi_bus = f"/run/user/{os.getuid()}/at-spi/bus"
    socket_ydotool = InputHealth.YDOTOOL_SOCKET
    return (
        patch("src.input.health.process_alive", side_effect=probe.proc_alive),
        patch("src.input.health.shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
        patch("src.input.health.os.path.exists",
              side_effect=lambda p: p in {atspi_bus, socket_ydotool}),
        patch("src.input.health.subprocess.run", side_effect=probe.subprocess_run),
    )


class TestHealthCheck:
    def test_todo_sano(self):
        probe = _Probe(daemon_alive=True)
        p = _patched(probe)
        with p[0], p[1], p[2], p[3]:
            report = InputHealth().check()
        assert report.ydotoold_running and report.portal_desktop and report.atspi_bus
        assert report.ok
        assert report.backend_order == ["ydotool", "portal"]
        assert not report.ydotoold_autofixed

    def test_autofix_levanta_daemon_muerto(self):
        probe = _Probe(daemon_alive=False)  # socket obsoleto, daemon caído
        p = _patched(probe)
        with p[0], p[1], p[2], p[3]:
            report = InputHealth().check(autofix=True)
        assert report.ydotoold_autofixed
        assert report.ydotoold_running
        assert "ydotool" in report.backend_order

    def test_sin_autofix_reporta_issue(self):
        probe = _Probe(daemon_alive=False)
        p = _patched(probe)
        with p[0], p[1], p[2], p[3]:
            report = InputHealth().check(autofix=False)
        assert not report.ydotoold_running and not report.ydotoold_autofixed
        assert any("ydotoold" in i for i in report.issues)
        # Con portal disponible la cadena sigue siendo operativa (degradada)
        assert report.backend_order == ["portal"]

    def test_sin_wl_copy_no_ok(self):
        probe = _Probe(daemon_alive=True)
        paths = {f"/run/user/{os.getuid()}/at-spi/bus", InputHealth.YDOTOOL_SOCKET}
        with patch("src.input.health.process_alive", side_effect=probe.proc_alive), \
             patch("src.input.health.shutil.which",
                   side_effect=lambda b: None if b == "wl-copy" else f"/usr/bin/{b}"), \
             patch("src.input.health.os.path.exists", side_effect=lambda p: p in paths), \
             patch("src.input.health.subprocess.run", side_effect=probe.subprocess_run):
            report = InputHealth().check()
        assert not report.ok
        assert any("wl-copy" in i for i in report.issues)

    def test_log_startup_report_no_lanza(self):
        probe = _Probe(daemon_alive=True)
        p = _patched(probe)
        with p[0], p[1], p[2], p[3]:
            report = log_startup_report()
        assert report.ok
