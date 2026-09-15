"""
Puerta de entrada pytest al mini-benchmark de computer-use.

Solo se ejecuta con ATLAS_DESKTOP_TESTS=1 (requiere sesión de escritorio real
con GNOME/Wayland, bus AT-SPI2 y cadena de input). En CI/headless se omite.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_DESKTOP_TESTS") != "1",
    reason="Requiere sesión de escritorio real (ATLAS_DESKTOP_TESTS=1 para activar).",
)


def test_benchmark_computer_use_pasa():
    from tests.computer_use.runner import run_benchmark
    report = run_benchmark()
    for r in report["resultados"]:
        assert r["ok"], f"Sonda '{r['id']}' falló: {r['detalle']}"
    assert report["ok"], f"Benchmark al {report['exito_pct']}%"
