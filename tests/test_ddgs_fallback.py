"""
Tests del fallback generalista vía librería `ddgs`.

Regresión del incidente: el scraper HTML de html.duckduckgo.com quedó bloqueado
por anti-bot (HTTP 202 anomaly challenge) y la Instant API devuelve vacío para
consultas generales, así que el tercer fallback moría siempre en silencio.
Ahora la capa generalista usa ddgs (meta-buscador sin API keys).
"""
import sys
import types

import pytest

from src.plugins.browser.engines.duckduckgo import DuckDuckGoSearchEngine


def _install_fake_ddgs(monkeypatch, text_impl):
    """Registra un módulo 'ddgs' falso con la clase DDGS esperada."""
    mod = types.ModuleType("ddgs")

    class FakeDDGS:
        def __init__(self, timeout=5):
            self.timeout = timeout

        def text(self, query, region="us-en", safesearch="moderate",
                 max_results=10, **kwargs):
            return text_impl(query, region=region, max_results=max_results)

    mod.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", mod)
    return mod


@pytest.fixture
def engine(monkeypatch):
    """Motor con la Instant API anulada (tests sin red)."""
    eng = DuckDuckGoSearchEngine()

    async def _no_instant(query, max_results=4):
        return None

    monkeypatch.setattr(eng, "_try_instant_api", _no_instant)
    return eng


class TestDdgsSearch:
    async def test_resultados_convertidos_a_items(self, engine, monkeypatch):
        _install_fake_ddgs(monkeypatch, lambda q, **kw: [
            {"title": "Meganoticias", "href": "https://meganoticias.cl", "body": "Noticias de Chile"},
            {"title": "Sin href", "body": "se descarta"},
            {"title": "La Tercera", "href": "https://www.latercera.com", "body": "Chile y el mundo"},
        ])
        res = await engine.search("noticias recientes Chile")
        assert res.success
        assert len(res.results) == 2  # el item sin href se descarta
        assert res.results[0].url == "https://meganoticias.cl"
        assert res.results[0].source_engine == "duckduckgo"

    async def test_respeta_region_y_max_results(self, engine, monkeypatch):
        capturado = {}

        def impl(query, region=None, max_results=None):
            capturado.update(query=query, region=region, max_results=max_results)
            return [{"title": "T", "href": "https://x.cl", "body": "b"}]

        _install_fake_ddgs(monkeypatch, impl)
        engine.region = "cl-es"
        await engine.search("dólar hoy", max_results=2)
        assert capturado["region"] == "cl-es"
        assert capturado["max_results"] == 2
        assert capturado["query"] == "dólar hoy"

    async def test_sin_resultados_es_failure_controlada(self, engine, monkeypatch):
        _install_fake_ddgs(monkeypatch, lambda q, **kw: [])
        res = await engine.search("consulta oscura")
        assert not res.success
        assert "Sin resultados" in res.error

    async def test_excepcion_de_red_es_failure_controlada(self, engine, monkeypatch):
        def impl(query, **kw):
            raise RuntimeError("boom de red")

        _install_fake_ddgs(monkeypatch, impl)
        res = await engine.search("noticias")
        assert not res.success
        assert "boom de red" in res.error

    async def test_paquete_no_instalado_es_failure_controlada(self, engine, monkeypatch):
        monkeypatch.setitem(sys.modules, "ddgs", None)  # import -> ImportError
        res = await engine.search("noticias")
        assert not res.success
        assert "ddgs" in res.error

    async def test_clima_tiene_prioridad_sobre_ddgs(self, monkeypatch):
        """Las consultas de clima nunca deben llegar a ddgs (fast-path wttr.in)."""
        llamadas = {"n": 0}

        def impl(query, **kw):
            llamadas["n"] += 1
            return [{"title": "T", "href": "https://x.cl", "body": "b"}]

        _install_fake_ddgs(monkeypatch, impl)
        eng = DuckDuckGoSearchEngine()

        async def _clima(query):
            from src.plugins.browser.engines.base import SearchResponse
            return SearchResponse(query=query, success=True, answer="☁️ +5°C",
                                  engine_used=eng.name)

        monkeypatch.setattr(eng, "_try_weather", _clima)
        res = await eng.search("qué tiempo hace en Puerto Natales")
        assert res.success and res.answer
        assert llamadas["n"] == 0
