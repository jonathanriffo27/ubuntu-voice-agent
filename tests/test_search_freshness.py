"""Tests de frescura de respuestas de búsqueda (anti-respuestas obsoletas)."""
import time

import pytest
from unittest.mock import AsyncMock

from src.plugins.browser.engines.base import SearchResponse
from src.plugins.browser.tools import MultiEngineSearchManager


CURRENT_YEAR = int(time.strftime("%Y"))


def make_manager(google=None, tavily=None, ddg=None, exa=None):
    m = MultiEngineSearchManager()
    m.google_engine.search = AsyncMock(return_value=google or SearchResponse(
        query="q", success=False, engine_used="google_grounding"))
    m.tavily_engine.search = AsyncMock(return_value=tavily or SearchResponse(
        query="q", success=False, engine_used="tavily"))
    m.exa_engine.search = AsyncMock(return_value=exa or SearchResponse(
        query="q", success=False, engine_used="exa"))
    m.ddg_engine.search = AsyncMock(return_value=ddg or SearchResponse(
        query="q", success=False, engine_used="duckduckgo"))
    m.ddg_engine.weather_fast_path = AsyncMock(return_value=None)
    return m


class TestMarcadoresTemporales:
    @pytest.mark.parametrize("q", [
        "quién ganó el último mundial",
        "quien gano el ultimo mundial",      # sin tildes (entrada por teclado)
        "QUIEN GANO EL ULTIMO MUNDIAL",      # mayúsculas gritadas
        "noticias de hoy",
        "clima mañana",
        "clima manana",                      # 'mañana' sin eñe
        "resultado del partido reciente",
        "precio actual del cobre",
        "estrenos de este año",
    ])
    def test_detecta_temporal(self, q):
        assert MultiEngineSearchManager._es_temporal(q) is True

    @pytest.mark.parametrize("q", [
        "capital de Francia",
        "cómo funciona HTTP",
        "historia de Roma",
        "quien escribió el Quijote",
    ])
    def test_no_temporal(self, q):
        assert MultiEngineSearchManager._es_temporal(q) is False


class TestAnclajeDeQuery:
    def test_anade_fecha_a_query_temporal(self):
        q = MultiEngineSearchManager._anclar_query("quién ganó el último mundial")
        assert str(CURRENT_YEAR) in q
        assert "hoy es" in q

    def test_no_duplica_si_ya_hay_anio(self):
        q = MultiEngineSearchManager._anclar_query(f"último mundial {CURRENT_YEAR}")
        assert q.count(str(CURRENT_YEAR)) == 1
        assert "hoy es" not in q

    def test_no_toca_query_atemporal(self):
        q = "capital de Francia"
        assert MultiEngineSearchManager._anclar_query(q) == q


class TestDeteccionObsoleta:
    def test_obsoleta_si_solo_anios_pasados(self):
        assert MultiEngineSearchManager._respuesta_obsoleta(
            f"Argentina ganó en {CURRENT_YEAR - 4}", CURRENT_YEAR) is True

    def test_fresca_si_menciona_anio_actual(self):
        assert MultiEngineSearchManager._respuesta_obsoleta(
            f"la final fue en {CURRENT_YEAR} y ganó X", CURRENT_YEAR) is False

    def test_sin_anios_no_se_marca(self):
        assert MultiEngineSearchManager._respuesta_obsoleta(
            "respuesta sin fechas", CURRENT_YEAR) is False

    def test_vacia_no_es_obsoleta(self):
        assert MultiEngineSearchManager._respuesta_obsoleta(None, CURRENT_YEAR) is False
        assert MultiEngineSearchManager._respuesta_obsoleta("", CURRENT_YEAR) is False


class TestFlujoConFreshness:
    async def test_query_temporal_se_ancla_antes_de_google(self):
        m = make_manager(google=SearchResponse(
            query="q", answer=f"nitidez en {CURRENT_YEAR}", success=True,
            engine_used="google_grounding"))
        await m.search("quién ganó el último mundial")
        q_enviada = m.google_engine.search.await_args.kwargs.get("query") or \
            m.google_engine.search.await_args.args[0]
        assert "hoy es" in q_enviada
        assert str(CURRENT_YEAR) in q_enviada

    async def test_obsoleta_dispara_reintento_con_anio(self):
        stale = SearchResponse(
            query="q", answer=f"ganó Argentina en {CURRENT_YEAR - 4}",
            success=True, engine_used="tavily")
        fresh = SearchResponse(
            query="q", answer=f"ganó España en {CURRENT_YEAR}",
            success=True, engine_used="tavily")
        tavily = AsyncMock(side_effect=[stale, fresh])
        m = MultiEngineSearchManager()
        m.google_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="google_grounding"))
        m.tavily_engine.search = tavily
        m.exa_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="exa"))
        m.ddg_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="duckduckgo"))
        m.ddg_engine.weather_fast_path = AsyncMock(return_value=None)

        res, trail = await m.search("quién ganó el último mundial")
        assert res.answer == fresh.answer
        assert tavily.await_count == 2
        # La 2ª llamada lleva el año explícito
        segunda_query = tavily.await_args.args[0]
        assert str(CURRENT_YEAR) in segunda_query
        assert "frescura" in trail

    async def test_si_sigue_obsoleta_se_advierte_al_llm(self):
        stale = SearchResponse(
            query="q", answer=f"ganó Argentina en {CURRENT_YEAR - 4}",
            success=True, engine_used="tavily")
        m = MultiEngineSearchManager()
        m.google_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="google_grounding"))
        m.tavily_engine.search = AsyncMock(side_effect=[stale, stale])
        m.exa_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="exa"))
        m.ddg_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="duckduckgo"))
        m.ddg_engine.weather_fast_path = AsyncMock(return_value=None)

        res, trail = await m.search("quién ganó el último mundial")
        assert "AVISO DE ACTUALIZACIÓN" in res.answer
        assert str(CURRENT_YEAR - 4) in res.answer  # conserva el dato original
        assert "stale" in trail

    async def test_staleness_sobre_snippets_sin_answer(self):
        """Modo raw (include_answer=False): la frescura se evalúa sobre snippets."""
        from src.plugins.browser.engines.base import SearchResultItem
        stale = SearchResponse(query="q", answer=None, success=True, engine_used="tavily",
                               results=[SearchResultItem(
                                   title=f"Resumen del mundial {CURRENT_YEAR - 4}",
                                   url="https://x", content=f"ganó Argentina en {CURRENT_YEAR - 4}",
                                   source_engine="tavily")])
        fresh = SearchResponse(query="q", answer=None, success=True, engine_used="tavily",
                               results=[SearchResultItem(
                                   title=f"España campeón {CURRENT_YEAR}", url="https://x",
                                   content=f"final de {CURRENT_YEAR}", source_engine="tavily")])
        m = MultiEngineSearchManager()
        m.google_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="google_grounding"))
        m.tavily_engine.search = AsyncMock(side_effect=[stale, fresh])
        m.exa_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="exa"))
        m.ddg_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="duckduckgo"))
        m.ddg_engine.weather_fast_path = AsyncMock(return_value=None)

        res, trail = await m.search("quién ganó el último mundial")
        assert res.results[0].title == f"España campeón {CURRENT_YEAR}"
        assert "frescura" in trail

    async def test_query_atemporal_no_reintenta(self):
        good = SearchResponse(query="q", answer="París", success=True, engine_used="tavily")
        tavily = AsyncMock(return_value=good)
        m = MultiEngineSearchManager()
        m.google_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="google_grounding"))
        m.tavily_engine.search = tavily
        m.exa_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="exa"))
        m.ddg_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="duckduckgo"))
        m.ddg_engine.weather_fast_path = AsyncMock(return_value=None)

        res, _ = await m.search("capital de Francia")
        assert res.answer == "París"
        assert tavily.await_count == 1


class TestNotaTemporalEnOutput:
    """Defensa anti-memoria: el resultado de una consulta temporal lleva una
    nota pegada a los datos ordenando al modelo responder con ellos (y con el
    año más reciente), en vez de desde su entrenamiento."""

    def _tool(self):
        from src.plugins.browser.engines.base import SearchResultItem
        from src.plugins.browser.tools import BuscarEnInternetTool
        res = SearchResponse(
            query="q", answer=f"España ganó en {CURRENT_YEAR}.",
            success=True, engine_used="tavily",
            results=[SearchResultItem(title="Palmarés", url="https://x",
                                      content=f"{CURRENT_YEAR} España", source_engine="tavily")])

        class StubManager:
            async def search(self, query, max_results=4):
                return res, "TAVILY ✅"

        return BuscarEnInternetTool(search_manager=StubManager())

    async def test_consulta_temporal_incluye_nota_de_contexto(self):
        from src.tools.base import ToolContext
        out = await self._tool().execute(ToolContext(config=None),
                                         query="quién ganó el último mundial")
        assert "CONTEXTO TEMPORAL" in out.content
        assert str(CURRENT_YEAR) in out.content
        assert "AÑO MÁS RECIENTE" in out.content

    async def test_consulta_atemporal_no_lleva_nota(self):
        from src.tools.base import ToolContext
        out = await self._tool().execute(ToolContext(config=None),
                                         query="capital de Francia")
        assert "CONTEXTO TEMPORAL" not in out.content


class TestCadenaDeFallback:
    async def test_prioridad_tavily_luego_exa_luego_ddg(self):
        """Si Tavily falla pero Exa tiene contenido, gana Exa."""
        from src.plugins.browser.engines.base import SearchResultItem
        exa_ok = SearchResponse(query="q", answer=None, success=True, engine_used="exa",
                                results=[SearchResultItem(title="t", url="u",
                                                          content="c", source_engine="exa")])
        m = MultiEngineSearchManager()
        m.google_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="google_grounding"))
        m.tavily_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, error="down", engine_used="tavily"))
        m.exa_engine.search = AsyncMock(return_value=exa_ok)
        m.ddg_engine.search = AsyncMock(return_value=SearchResponse(
            query="q", success=False, engine_used="duckduckgo"))
        m.ddg_engine.weather_fast_path = AsyncMock(return_value=None)

        res, trail = await m.search("algo no temporal")
        assert res.engine_used == "exa"
        assert "TAVILY ❌" in trail and "EXA ✅" in trail
