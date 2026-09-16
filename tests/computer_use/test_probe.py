"""
Puerta de entrada pytest al mini-benchmark de computer-use (Fase 7).

Cada grupo corre solo donde es viable:
- escritorio: requiere sesión real (ATLAS_DESKTOP_TESTS=1).
- navegador: requiere un binario Chromium/Brave/Chrome (instancia headless
  desechable; jamás toca la sesión del usuario).
- local: siempre (sandbox bwrap, política de riesgo).
"""
import os

import pytest


def _hay_navegador() -> bool:
    import shutil
    return any(shutil.which(b) for b in (
        "brave", "brave-browser", "google-chrome", "google-chrome-stable",
        "chromium", "chromium-browser"))


def _correr(requires):
    from tests.computer_use.runner import run_benchmark
    report = run_benchmark(include_requires=requires)
    for r in report["resultados"]:
        assert r["ok"], f"Sonda '{r['id']}' falló: {r['detalle']}"


def test_benchmark_local():
    """Sandbox endurecido + política de tiers: siempre deben pasar."""
    _correr({"local"})


@pytest.mark.skipif(
    not _hay_navegador(),
    reason="No hay navegador Chromium/Brave/Chrome en el sistema.",
)
def test_benchmark_navegador():
    """Flujo CDP real en navegador headless desechable (Fase 4)."""
    _correr({"navegador"})


@pytest.mark.skipif(
    os.environ.get("ATLAS_DESKTOP_TESTS") != "1",
    reason="Requiere sesión de escritorio real (ATLAS_DESKTOP_TESTS=1 para activar).",
)
def test_benchmark_escritorio():
    """Sondas pasivas de la sesión GNOME/Wayland (cadena input, AT-SPI2, captura)."""
    _correr({"escritorio"})
