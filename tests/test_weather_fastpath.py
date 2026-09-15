"""Tests del parser de locación y fast-path de clima (wttr.in)."""
import pytest
from src.plugins.browser.engines.duckduckgo import DuckDuckGoSearchEngine


@pytest.fixture
def ddg():
    return DuckDuckGoSearchEngine()


@pytest.mark.parametrize("query,esperado", [
    ("tiempo Puerto Natales mañana 14 septiembre 2026", "puerto natales"),
    ("clima en Viña del Mar", "viña del mar"),          # 'mar' no debe tragarse como mes
    ("que tiempo hara mañana en Santiago", "santiago"),
    ("qué tiempo hará pasado mañana en Puerto Natales", "puerto natales"),
    ("cómo está el tiempo en Punta Arenas hoy", "punta arenas"),
    ("temperatura en Valparaíso", "valparaíso"),
    ("temperatura actual", ""),                          # sin ciudad -> fallback a otros motores
    ("pronóstico del tiempo en La Serena esta semana", "la serena"),
])
def test_clean_weather_location(ddg, query, esperado):
    assert ddg._clean_weather_location(query) == esperado


@pytest.mark.parametrize("query,es_forecast", [
    ("qué tiempo hace en Santiago", False),
    ("temperatura en Valparaíso", False),
    ("qué tiempo hará mañana en Santiago", True),
    ("tiempo pasado mañana en Arica", True),
    ("pronóstico fin de semana en Concepción", True),
])
def test_deteccion_forecast(ddg, query, es_forecast):
    lower = query.lower()
    es = any(w in lower for w in ddg._FUTURE_WORDS)
    assert es == es_forecast


@pytest.mark.asyncio
async def test_weather_fast_path_no_clima_retorna_none(ddg):
    assert await ddg.weather_fast_path("cotización dólar hoy") is None
    assert await ddg.weather_fast_path("hora actual en Puerto Rico") is None
