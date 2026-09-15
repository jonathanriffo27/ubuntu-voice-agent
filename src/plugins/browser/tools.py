import asyncio
from typing import Dict, Any, Optional, Tuple
from src.tools.base import BaseTool, ToolContext, ToolResult
from .engines.google_grounding import GoogleGroundingSearchEngine
from .engines.tavily import TavilySearchEngine, _redact_secrets
from .engines.duckduckgo import DuckDuckGoSearchEngine
from .engines.reader import WebPageReader
from .research import DeepResearchEngine
from src.utils.logging import get_logger

logger = get_logger("plugins.browser")


class MultiEngineSearchManager:
    """
    Gestor de búsqueda multi-motor con jerarquía estricta de fallback y trazabilidad:
    1. Google Search Grounding (Motor Primario)
    2. Tavily Search (Segundo Fallback)
    3. DuckDuckGo Search (Tercer Fallback Gratuito y Libre)
    """

    def __init__(self):
        self.google_engine = GoogleGroundingSearchEngine()
        self.tavily_engine = TavilySearchEngine()
        self.ddg_engine = DuckDuckGoSearchEngine()

    @staticmethod
    def _motivo_corto(error: Optional[str]) -> str:
        """Resume la causa del fallo de un motor en una etiqueta legible para el trail."""
        if not error:
            return ""
        low = error.lower()
        if "429" in low or "cuota" in low or "exhausted" in low:
            return "cuota"
        if "timeout" in low:
            return "timeout"
        if "no configurada" in low:
            return "sin key"
        return "fallo"

    async def search(self, query: str, max_results: int = 4) -> Tuple[Any, str]:
        trail = []

        # 0. Fast-path de clima: wttr.in responde en ~300ms sin consumir cuota de nadie
        weather_res = await self.ddg_engine.weather_fast_path(query)
        if weather_res and weather_res.success and weather_res.answer:
            return weather_res, "CLIMA ✅"

        # 1. Intentar Google Grounding
        res = await self.google_engine.search(query, max_results=max_results)
        if res.success and (res.answer or res.results):
            trail.append("GOOGLE ✅")
            return res, " → ".join(trail)
        else:
            motivo = self._motivo_corto(getattr(res, "error", None))
            trail.append(f"GOOGLE ❌({motivo})" if motivo else "GOOGLE ❌")

        # 2. Fallbacks EN PARALELO: Tavily y DuckDuckGo compiten; gana el más rápido
        #    (antes eran secuenciales y sumaban sus timeouts: hasta ~14s extra).
        res_tavily, res_ddg = await asyncio.gather(
            self.tavily_engine.search(query, max_results=max_results),
            self.ddg_engine.search(query, max_results=max_results)
        )

        # Prioridad: Tavily (respuesta sintetizada) si logró algo útil
        if res_tavily.success and (res_tavily.answer or res_tavily.results):
            trail.append("TAVILY ✅")
            return res_tavily, " → ".join(trail)
        else:
            trail.append("TAVILY ❌")

        if res_ddg.success and res_ddg.results:
            trail.append("DUCKDUCKGO ✅")
            return res_ddg, " → ".join(trail)
        else:
            trail.append("DUCKDUCKGO ❌")

        return res_ddg, " → ".join(trail)


class BuscarEnInternetTool(BaseTool):
    """Herramienta de búsqueda rápida en internet con fallback automático visible."""

    def __init__(self, search_manager: Optional[MultiEngineSearchManager] = None):
        self.search_manager = search_manager or MultiEngineSearchManager()

    @property
    def name(self) -> str:
        return "buscar_en_internet"

    @property
    def description(self) -> str:
        return (
            "Busca información en internet en tiempo real sobre noticias, clima, cotizaciones, "
            "deportes, eventos o dudas generales. Utiliza Google Search con respaldo de Tavily y DuckDuckGo."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "La consulta o términos de búsqueda."
                }
            },
            "required": ["query"]
        }

    async def execute(self, context: ToolContext, query: str = None, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(success=False, content="Falta la consulta (query).")

        res, trail = await self.search_manager.search(query)
        if not res.success:
            return ToolResult(success=False, content=f"[{trail}] Falló la búsqueda: {res.error}")

        # Construir resumen inmediato para la visualización en consola
        short_summary = ""
        if res.answer:
            short_summary = res.answer.replace('\n', ' ').strip()
        elif res.results:
            short_summary = f"{res.results[0].title}: {res.results[0].content}".replace('\n', ' ').strip()

        header_badge = f"[{trail}] {short_summary}" if short_summary else f"[{trail}] Resultados obtenidos."

        output = f"{header_badge}\n\n"
        if res.answer:
            output += f"Respuesta Directa:\n{res.answer}\n\n"

        if res.results:
            output += "Fuentes y Enlaces:\n"
            for i, r in enumerate(res.results):
                desc = f": {r.content}" if r.content else ""
                output += f"{i+1}. [{r.title}]({r.url}){desc}\n"

        if len(output) > 2500:
            output = output[:2500] + "\n...[información truncada]"

        return ToolResult(success=True, content=output.strip())


class LeerPaginaWebTool(BaseTool):
    """Permite leer el contenido textual completo de una URL específica."""

    def __init__(self, reader: Optional[WebPageReader] = None):
        self.reader = reader or WebPageReader()

    @property
    def name(self) -> str:
        return "leer_pagina_web"

    @property
    def description(self) -> str:
        return (
            "Descarga y extrae el texto limpio de un enlace web o artículo. "
            "Úsalo cuando necesites consultar una URL que encontraste en una búsqueda o que el usuario te facilitó."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "url": {
                    "type": "STRING",
                    "description": "La dirección URL de la página web a leer (ej. 'https://ejemplo.com/noticia')."
                }
            },
            "required": ["url"]
        }

    async def execute(self, context: ToolContext, url: str = None, **kwargs) -> ToolResult:
        if not url:
            return ToolResult(success=False, content="Falta la URL de la página a leer.")

        content = await self.reader.read_url(url)
        return ToolResult(success=True, content=content)


class InvestigarEnProfundidadTool(BaseTool):
    """Ejecuta una investigación profunda multi-fuente con síntesis de Gemini 3.7 Flash."""

    def __init__(self, research_engine: DeepResearchEngine):
        self.research_engine = research_engine

    @property
    def name(self) -> str:
        return "investigar_en_profundidad"

    @property
    def description(self) -> str:
        return (
            "Realiza una investigación profunda y exhaustiva sobre un tema complejo. "
            "Descompone el tema en múltiples búsquedas web en paralelo, analiza artículos completos "
            "y redacta un reporte estructurado con razonamiento avanzado (Gemini 3.7 Flash). "
            "Úsalo para comparativas, investigaciones técnicas, análisis de mercado o revisiones bibliográficas."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "tema": {
                    "type": "STRING",
                    "description": "El tema o pregunta compleja a investigar en profundidad."
                }
            },
            "required": ["tema"]
        }

    async def execute(self, context: ToolContext, tema: str = None, **kwargs) -> ToolResult:
        if not tema:
            return ToolResult(success=False, content="Falta el tema de investigación.")

        res = await self.research_engine.conduct_research(tema)
        if not res["success"]:
            return ToolResult(success=False, content=res["summary_voice"])

        formatted = (
            f"=== 🔬 INFORME DE INVESTIGACIÓN: {tema} ===\n\n"
            f"{res['full_report']}\n\n"
            f"=== 🎙️ Resumen para Voz ===\n{res['summary_voice']}"
        )
        return ToolResult(success=True, content=formatted, metadata=res)
