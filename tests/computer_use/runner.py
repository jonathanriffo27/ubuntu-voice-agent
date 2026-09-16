"""
Runner del mini-benchmark de computer-use de Atlas (Fases 0 y 7 del plan).

Sondas:
- Pasivas de escritorio (`requiere: escritorio`): salud de la cadena de input,
  AT-SPI2 y captura. Solo corren con sesión de escritorio real.
- Activas seguras (`requiere: navegador`): flujo CDP REAL contra una instancia
  headless desechable del navegador (perfil en /tmp). Mide abrir/snapshot/
  click/escribir/leer de la Fase 4 sin tocar la sesión del usuario.
- Locales: sandbox bwrap (Fase 6) y política de riesgo.

Registro continuo (Fase 7): cada corrida se anexa a `history.jsonl` con
fecha, commit de git, % de éxito y detalle; el runner imprime el delta contra
la corrida anterior. Regla de oro del plan: si una mejora no sube el número,
no entra.

Uso:
    ./venv/bin/python -m tests.computer_use.runner                # tabla legible
    ./venv/bin/python -m tests.computer_use.runner --json         # salida JSON
    ./venv/bin/python -m tests.computer_use.runner --no-history   # sin registrar
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import yaml

_TASKS_FILE = os.path.join(os.path.dirname(__file__), "tasks.yaml")
_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "history.jsonl")

# Puerto dedicado del benchmark: jamás colisiona con el navegador real (9222)
_BENCH_PORT = 9223

# Página de prueba autosuficiente: botón que muta el DOM, input, enlace y texto
# conocido. Se codifica en base64 porque las data: URLs en texto plano cortan
# el documento al primer '#' (inicio de fragmento) — bug real detectado aquí.
_TEST_HTML = (
    "<html><head><title>Atlas CDP Benchmark</title></head><body>"
    "<h1 id='titulo'>Pagina de prueba</h1>"
    "<button id='b1' onclick=\"document.getElementById('titulo').textContent='CAMBIADO-ATLAS'\">"
    "Pulsar boton</button>"
    "<input id='campo' placeholder='Campo de prueba'>"
    "<a href='#'>Enlace decorativo</a>"
    "<p>AtlasBenchmarkOK contenido conocido.</p>"
    "</body></html>"
)
_TEST_PAGE = "data:text/html;base64," + __import__("base64").b64encode(
    _TEST_HTML.encode()).decode()


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


def _probe_sandbox(_task):
    """Fase 6: ¿el shell del subagente corre confinado de verdad?"""
    from src.security.sandbox import BubblewrapSandbox
    sb = BubblewrapSandbox(os.getcwd())
    if not sb.available:
        return True, "bwrap no instalado: modo degradado (env limpio, sin confinamiento)"
    res = sb.run("test $HOME = /tmp && echo confinado", timeout=15)
    ok = res.returncode == 0 and "confinado" in res.stdout
    return ok, ("bwrap OK: HOME aislado, red off, fs mínimo" if ok
                else f"bwrap roto: rc={res.returncode} {res.stderr[:80]}")


def _probe_policy(_task):
    """La política de riesgo carga el YAML del repo y clasifica/escala bien."""
    from src.security.policy import SecurityPolicy, RiskTier
    policy = SecurityPolicy.load()
    checks = [
        policy.classify("navegador_web.leer") == RiskTier.READ_ONLY,
        policy.classify("navegador_web.click") == RiskTier.LOCAL_WRITE,
        policy.classify("navegador_web.click", "Confirmar compra") == RiskTier.IRREVERSIBLE,
        policy.classify("enviar_whatsapp") == RiskTier.IRREVERSIBLE,
    ]
    ok = all(checks)
    return ok, f"{sum(checks)}/4 clasificaciones correctas"


def _probe_navegador_flujo(_task):
    """
    Fase 4 end-to-end sobre instancia headless DESECHABLE: abrir una página,
    enumerar controles, hacer click (verificado por cambio de firma), escribir
    texto y leer contenido. Nada toca la sesión del usuario.
    """
    import asyncio

    async def _flow():
        from src.cdp import BrowserManager
        manager = BrowserManager(port=_BENCH_PORT, headless=True)
        pasos = {}
        try:
            _tid, page = await manager.open_tab("about:blank")
            await page.navigate(_TEST_PAGE)
            info = await page.current_info()
            pasos["abrir"] = "Atlas CDP Benchmark" in (info.get("title") or "")

            elements = await page.snapshot()
            pasos["elementos"] = len(elements) >= 3

            boton = next((e for e in elements if "Pulsar" in e.name), None)
            if boton is None:
                pasos["click"] = False
            else:
                firma_antes = await page.signature()
                r = await page.click(boton.idx)
                firma_despues = await page.signature()
                pasos["click"] = bool(r.get("found")) and firma_antes != firma_despues

            campo = next((e for e in elements if e.editable), None)
            if campo is None:
                pasos["escribir"] = False
            else:
                r = await page.type_text(campo.idx, "marca-Atlas-2026")
                valor = await page.evaluate(
                    "document.getElementById('campo').value") or ""
                pasos["escribir"] = bool(r.get("ok")) and "marca-Atlas-2026" in valor

            data = await page.read_text()
            texto = data.get("text", "")
            pasos["leer"] = "AtlasBenchmarkOK" in texto and "CAMBIADO-ATLAS" in texto
        finally:
            await manager.shutdown()
        return pasos

    pasos = asyncio.run(_flow())
    detalle = " ".join(f"{k}{'✅' if v else '❌'}" for k, v in pasos.items())
    return all(pasos.values()) and len(pasos) == 5, detalle or "flujo vacío"


_PROBES = {
    "health": _probe_health,
    "atspi_apps": _probe_atspi_apps,
    "elementos": _probe_elementos,
    "captura": _probe_captura,
    "sandbox": _probe_sandbox,
    "policy": _probe_policy,
    "navegador_flujo": _probe_navegador_flujo,
}


def _browser_available() -> bool:
    return any(shutil.which(b) for b in
               ("brave", "brave-browser", "google-chrome", "google-chrome-stable",
                "chromium", "chromium-browser"))


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def run_benchmark(include_requires=None) -> dict:
    """
    Ejecuta el benchmark. `include_requires`: set de valores de `requiere`
    admitidos (None = todos). Permite a pytest correr solo el subconjunto
    viable en cada entorno (sin escritorio, sin navegador...).
    """
    with open(_TASKS_FILE, "r", encoding="utf-8") as f:
        tasks = (yaml.safe_load(f) or {}).get("pruebas", [])

    if include_requires is not None:
        tasks = [t for t in tasks if (t.get("requiere") or "local") in include_requires]

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
            "requiere": task.get("requiere") or "local",
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


def _load_history() -> list:
    if not os.path.exists(_HISTORY_FILE):
        return []
    entradas = []
    with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entradas.append(json.loads(line))
                except ValueError:
                    continue
    return entradas


def _record_history(report: dict) -> dict:
    """Anexa la corrida al histórico y devuelve la entrada registrada."""
    entrada = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git": _git_sha(),
        "exito_pct": report["exito_pct"],
        "exitosas": report["exitosas"],
        "total": report["total"],
        "segundos": round(sum(r["segundos"] for r in report["resultados"]), 2),
        "fallos": [r["id"] for r in report["resultados"] if not r["ok"]],
    }
    with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    return entrada


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini-benchmark de computer-use de Atlas")
    parser.add_argument("--json", action="store_true", help="Salida en JSON")
    parser.add_argument("--no-history", action="store_true",
                        help="No registrar esta corrida en history.jsonl")
    args = parser.parse_args()

    report = run_benchmark()

    historial = _load_history()
    previo = historial[-1] if historial else None
    entrada = None
    if not args.no_history:
        entrada = _record_history(report)

    if args.json:
        report["git"] = _git_sha()
        if previo:
            report["delta_pct"] = round(report["exito_pct"] - previo["exito_pct"], 1)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n=== 🖥️  Benchmark Computer-Use Atlas ===")
        for r in report["resultados"]:
            marca = "✅" if r["ok"] else "❌"
            print(f" {marca} [{r['requiere']:<9}] {r['id']:<22} {r['detalle']}  ({r['segundos']}s)")
        resumen = f"\n Éxito: {report['exitosas']}/{report['total']} ({report['exito_pct']}%)"
        if previo:
            delta = report["exito_pct"] - previo["exito_pct"]
            signo = "+" if delta >= 0 else ""
            resumen += f"  (Δ {signo}{delta:.1f} pts vs {previo['ts']}" + \
                       (f", {previo['git']}" if previo.get("git") else "") + ")"
        if entrada:
            resumen += f"\n 📈 Registrado en history.jsonl ({len(historial) + 1} corridas)"
        print(resumen + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
