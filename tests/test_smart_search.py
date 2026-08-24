import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.plugins.browser.engines.base import SearchResponse, SearchResultItem
from src.plugins.browser.engines.google_grounding import GoogleGroundingSearchEngine
from src.plugins.browser.engines.tavily import TavilySearchEngine
from src.plugins.browser.engines.duckduckgo import DuckDuckGoSearchEngine
from src.plugins.browser.engines.reader import WebPageReader
from src.plugins.browser.tools import (
    MultiEngineSearchManager,
    BuscarEnInternetTool,
    LeerPaginaWebTool,
    InvestigarEnProfundidadTool
)
from src.plugins.browser.research import DeepResearchEngine
from src.tools.base import ToolContext
from src.config.models import AtlasConfig


@pytest.mark.asyncio
async def test_duckduckgo_search_parsing():
    engine = DuckDuckGoSearchEngine()
    fake_html = """
    <html>
        <body>
            <a class="result__url" href="https://example.com/test">Ejemplo</a>
            <a class="result__snippet" href="#">Este es un snippet de prueba sobre Python.</a>
        </body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html
    mock_resp.raise_for_status = MagicMock()

    with patch.object(engine, "_try_instant_api", AsyncMock(return_value=None)), \
         patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        res = await engine.search("python")
        assert res.success is True
        assert len(res.results) == 1
        assert "example.com" in res.results[0].url
        assert "snippet" in res.results[0].content


@pytest.mark.asyncio
async def test_tavily_search_engine():
    engine = TavilySearchEngine(api_key="mock_key")
    mock_json = {
        "answer": "Respuesta directa",
        "results": [{"title": "Test", "url": "https://test.com", "content": "Contenido test"}]
    }
    mock_resp = MagicMock()
    mock_resp.json = MagicMock(return_value=mock_json)
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        res = await engine.search("consulta")
        assert res.success is True
        assert res.answer == "Respuesta directa"
        assert len(res.results) == 1


@pytest.mark.asyncio
async def test_multi_engine_search_fallback_hierarchy():
    manager = MultiEngineSearchManager()

    # Case 1: Google Grounding succeeds
    manager.google_engine.search = AsyncMock(return_value=SearchResponse(query="q", answer="Google OK", success=True, engine_used="google_grounding"))
    res, trail = await manager.search("q")
    assert res.engine_used == "google_grounding"
    assert "GOOGLE ✅" in trail

    # Case 2: Google fails -> Tavily succeeds
    manager.google_engine.search = AsyncMock(return_value=SearchResponse(query="q", success=False, engine_used="google_grounding"))
    manager.tavily_engine.search = AsyncMock(return_value=SearchResponse(query="q", answer="Tavily OK", success=True, engine_used="tavily"))
    res, trail = await manager.search("q")
    assert res.engine_used == "tavily"
    assert "GOOGLE ❌ → TAVILY ✅" in trail

    # Case 3: Google and Tavily fail -> DuckDuckGo succeeds
    manager.tavily_engine.search = AsyncMock(return_value=SearchResponse(query="q", success=False, engine_used="tavily"))
    manager.ddg_engine.search = AsyncMock(return_value=SearchResponse(query="q", results=[SearchResultItem("DDG", "url", "content", "duckduckgo")], success=True, engine_used="duckduckgo"))
    res, trail = await manager.search("q")
    assert res.engine_used == "duckduckgo"
    assert "GOOGLE ❌ → TAVILY ❌ → DUCKDUCKGO ✅" in trail


@pytest.mark.asyncio
async def test_web_page_reader():
    reader = WebPageReader()
    html_doc = """
    <html>
        <head><style>.ad { color: red; }</style></head>
        <body>
            <nav>Menu link 1</nav>
            <article>
                <h1>Noticia Importante</h1>
                <p>Este es el p&aacute;rrafo principal de la noticia.</p>
            </article>
            <script>console.log("analytics");</script>
        </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.text = html_doc
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        text = await reader.read_url("https://noticias.com/1")
        assert "Noticia Importante" in text
        assert "párrafo principal" in text
        assert "console.log" not in text
        assert "Menu link" not in text


@pytest.mark.asyncio
async def test_deep_research_engine():
    mock_search = MagicMock()
    mock_search.search = AsyncMock(return_value=SearchResponse(
        query="q",
        results=[SearchResultItem("Fuente 1", "https://ejemplo.com/1", "Detalles", "mock")],
        success=True
    ))

    mock_client = MagicMock()
    # Mock planning response & synthesis response
    mock_client.chat_completion = AsyncMock(side_effect=[
        {"choices": [{"message": {"content": '{"queries": ["sub1", "sub2"]}', "tool_calls": None}}]},
        {"choices": [{"message": {"content": "RESUMEN EJECUTIVO: Resumen clave.\n2. ANÁLISIS: Datos.", "reasoning_content": "Thinking...", "tool_calls": None}}]}
    ])

    engine = DeepResearchEngine(search_engine=mock_search, cli_client=mock_client)
    res = await engine.conduct_research("Comparativa de GPUs")

    assert res["success"] is True
    assert "Resumen clave" in res["summary_voice"]
    assert "https://ejemplo.com/1" in res["sources"]


@pytest.mark.asyncio
async def test_browser_tools_execution():
    ctx = ToolContext(config=AtlasConfig())
    manager = MultiEngineSearchManager()
    manager.search = AsyncMock(return_value=(SearchResponse(
        query="dolar",
        answer="950 CLP",
        results=[SearchResultItem("Banco Central", "https://bcentral.cl", "Dolar 950", "google")],
        success=True,
        engine_used="google"
    ), "GOOGLE ✅"))

    tool_buscar = BuscarEnInternetTool(manager)
    res_b = await tool_buscar.execute(ctx, query="dolar")
    assert res_b.success is True
    assert "950 CLP" in res_b.content

    reader = WebPageReader()
    reader.read_url = AsyncMock(return_value="Texto de la web")
    tool_leer = LeerPaginaWebTool(reader)
    res_l = await tool_leer.execute(ctx, url="https://test.com")
    assert res_l.success is True
    assert "Texto de la web" in res_l.content
