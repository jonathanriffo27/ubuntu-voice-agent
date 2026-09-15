"""
Runner del mini-benchmark de computer-use de Atlas (Fase 0 del plan).

Diseño deliberadamente PASIVO: mide la salud de la cadena de automatización
(sensores, backends, captura) sin inyectar input real en el escritorio.
Es la vara de medir falsificable del claim "Atlas puede operar la GUI".

Uso:
    ./venv/bin/python -m tests.computer_use.runner          # tabla legible
    ./venv/bin/python -m tests.computer_use.runner --json   # salida JSON
"""
import argparse
import json
import os
import sys
import time

import yaml

_TASKS_FILE = os.path.join(os.path.dirname(__file__), "tasks.yaml")


def _probe_health(_task):
    from src.input.health import InputHealth
    report = InputHealth().check(autofix=True)
    ok = report.ok
    detalle = f"backends={report.backend_order}, issues={len(report.issues)}"
    return ok, detalle


def _probe_atspi_apps(task):
    from src.utils.a11y import AccessibilitySensor
    sensor = AccessibilitySensor()
    apps = sensor.list_accessible_apps()
    min_apps = int(task.get("min_apps", 1))
    ok = len(apps) >= min_apps
    return ok, f"{len(apps)} apps (mínimo {min_apps})"


def _probe_elementos(task):
    from src.input.resolver import ElementResolver
    resolver = ElementResolver()
    elements = resolver.list_elements()
    min_elements = int(task.get("min_elementos", 1))
    ok = len(elements) >= min_elements
    return ok, f"{len(elements)} elementos (mínimo {min_elements})"


def _probe_captura(task):
    from src.vision.service import OptimizedScreenCaptureService
    service = OptimizedScreenCaptureService()
    t0 = time.time()
    data = service.capture_screen(max_dim=1280, quality=70)
    elapsed_ms = (time.time() - t0) * 1000
    max_ms = float(task.get("max_ms", 2000))
    ok = elapsed_ms <= max_ms and len(data) > 10_000
    return ok, f"{elapsed_ms:.0f}ms / {len(data)/1024:.0f}KB (máximo {max_ms:.0f}ms)"


_PROBES = {
    "health": _probe_health,
    "atspi_apps": _probe_atspi_apps,
    "elementos": _probe_elementos,
    "captura": _probe_captura,
}


def run_benchmark() -> dict:
    with open(_TASKS_FILE, "r", encoding="utf-8") as f:
        tasks = (yaml.safe_load(f) or {}).get("pruebas", [])

    resultados = []
    for task in tasks:
        probe = _PROBES.get(task.get("sonda"))
        t0 = time.time()
        if not probe:
            ok, detalle = False, f"sonda desconocida: {task.get('sonda')}"
        else:
            try:
                ok, detalle = probe(task)
            except Exception as e:
                ok, detalle = False, f"excepción: {e}"
        resultados.append({
            "id": task.get("id"),
            "nombre": task.get("nombre"),
            "ok": bool(ok),
            "detalle": detalle,
            "segundos": round(time.time() - t0, 2),
        })

    total = len(resultados)
    exitosas = sum(1 for r in resultados if r["ok"])
    return {
        "ok": total > 0 and exitosas == total,
        "exito_pct": round(100.0 * exitosas / total, 1) if total else 0.0,
        "total": total,
        "exitosas": exitosas,
        "resultados": resultados,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini-benchmark de computer-use de Atlas")
    parser.add_argument("--json", action="store_true", help="Salida en JSON")
    args = parser.parse_args()

    report = run_benchmark()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n=== 🖥️  Benchmark Computer-Use Atlas (pasivo) ===")
        for r in report["resultados"]:
            marca = "✅" if r["ok"] else "❌"
            print(f" {marca} {r['id']:<22} {r['detalle']}")
        print(f"\n Éxito: {report['exitosas']}/{report['total']} ({report['exito_pct']}%)\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
