import asyncio
import re
import time
from typing import Dict, Any, Optional, Tuple
from src.tools.base import BaseTool, ToolContext, ToolResult
from .engines.google_grounding import GoogleGroundingSearchEngine
from .engines.tavily import TavilySearchEngine, _redact_secrets
from .engines.duckduckgo import DuckDuckGoSearchEngine
from .engines.exa import ExaSearchEngine
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

    FreSCo (freshness): las consultas con marcadores temporales relativos
    ("último", "hoy", "reciente"...) se anclan a la fecha actual antes de buscar;
    si la respuesta ganadora solo menciona años pasados, se reintenta una vez
    con el año explícito y, si aun así no mejora, se marca como posiblemente
    desactualizada en vez de presentarla con confianza.
    """

    # Marcadores de "depende de la fecha de hoy"
    _TEMPORAL_RE = re.compile(
        r"\b(últim[oa]s?|hoy|ayer|mañana|reciente[sm]?|actuale?s?|actualmente|este\s+(año|mes|semana)|"
        r"próxim[oa]s?|noticias?|ahora|esta\s+semana|quién\s+ganó|resultado\s+de)\b",
        re.IGNORECASE,
    )
    _YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

    def __init__(self):
        self.google_engine = GoogleGroundingSearchEngine()
        self.tavily_engine = TavilySearchEngine()
        self.exa_engine = ExaSearchEngine()
        self.ddg_engine = DuckDuckGoSearchEngine()

    # ------------------------------------------------------------------
    # Freshness / anclaje temporal
    # ------------------------------------------------------------------
    @classmethod
    def _es_temporal(cls, query: str) -> bool:
        return bool(cls._TEMPORAL_RE.search(query or ""))

    @classmethod
    def _anclar_query(cls, query: str) -> str:
        """Añade la fecha actual si la query es temporal y no trae ya un año."""
        if not cls._es_temporal(query):
            return query
        if cls._YEAR_RE.search(query):
            return query  # ya viene anclada por el LLM
        now = time.localtime()
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        return f"{query} (hoy es {now.tm_mday} de {meses[now.tm_mon - 1]} de {now.tm_year})"

    @classmethod
    def _respuesta_obsoleta(cls, text: str, current_year: int) -> bool:
        """La respuesta menciona años pero NINGUNO es el actual → sospecha de stale."""
        if not text:
            return False
        years = [int(y) for y in cls._YEAR_RE.findall(text)]
        return bool(years) and max(years) < current_year

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
        temporal = self._es_temporal(query)
        q = self._anclar_query(query) if temporal else query
        trail = []

        # 0. Fast-path de clima: wttr.in responde en ~300ms sin consumir cuota de nadie
        weather_res = await self.ddg_engine.weather_fast_path(query)
        if weather_res and weather_res.success and weather_res.answer:
            return weather_res, "CLIMA ✅"

        # 1. Intentar Google Grounding
        res = await self.google_engine.search(q, max_results=max_results)
        if res.success and (res.answer or res.results):
            trail.append("GOOGLE ✅")
            return await self._verificar_frescura(res, query, temporal, trail)
        else:
            motivo = self._motivo_corto(getattr(res, "error", None))
            trail.append(f"GOOGLE ❌({motivo})" if motivo else "GOOGLE ❌")

        # 2. Fallbacks EN PARALELO: Tavily (raw), Exa (si hay key) y DuckDuckGo
        #    compiten; se elige el primero con contenido según prioridad.
        res_tavily, res_exa, res_ddg = await asyncio.gather(
            self.tavily_engine.search(q, max_results=max_results),
            self.exa_engine.search(q, max_results=max_results),
            self.ddg_engine.search(q, max_results=max_results),
        )

        def _util(r):
            return r.success and (r.answer or r.results)

        candidatos = [("TAVILY", res_tavily), ("EXA", res_exa), ("DUCKDUCKGO", res_ddg)]
        elegido = None
        for nombre, r in candidatos:
            if _util(r) and elegido is None:
                elegido = (nombre, r)
                trail.append(f"{nombre} ✅")
            else:
                trail.append(f"{nombre} ❌" if not _util(r) else f"{nombre} ✅")

        if elegido:
            return await self._verificar_frescura(elegido[1], query, temporal, trail)

        return res_ddg, " → ".join(trail)

    async def _verificar_frescura(self, res, query_original: str,
                                  temporal: bool, trail: list) -> Tuple[Any, str]:
        """
        Post-proceso anti-obsolescencia para consultas temporales:
        1. Se evalúa texto = answer o, si no hay (modo snippets crudos), los 3
           primeros snippets. Si solo menciona años pasados → 1 reintento con
           año explícito vía Tavily (barato, raw).
        2. Si aun así no se actualiza → se antepone un aviso para que el LLM de
           voz no lo presente como hecho cierto.
        """
        if not temporal:
            return res, " → ".join(trail)

        year = int(time.strftime("%Y"))
        blob = res.answer or " ".join(
            f"{s.title} {s.content}" for s in (res.results[:3] if res.results else []))
        if not self._respuesta_obsoleta(blob, year):
            return res, " → ".join(trail)

        # Reintento con ancla de año explícita (Tavily en modo raw: rápido).
        logger.info(f"🕐 Respuesta sospechosa de obsoleta para '{query_original[:60]}'; reintentando con ancla {year}.")
        res_retry = await self.tavily_engine.search(
            f"{query_original} {year} últimas noticias", max_results=3)
        blob_retry = res_retry.answer or " ".join(
            f"{s.title} {s.content}" for s in (res_retry.results[:3] if res_retry.results else []))
        if res_retry.success and blob_retry and not self._respuesta_obsoleta(blob_retry, year):
            trail.append("TAVILY🔁✅ (frescura)")
            return res_retry, " → ".join(trail)

        # Sin mejora: devolver la original, pero con aviso HONESTO al principio
        years = self._YEAR_RE.findall(blob)
        oldest = max(int(y) for y in years) if years else "?"
        aviso = (
            f"⚠️ AVISO DE ACTUALIZACIÓN: la fuente más fiable disponible menciona como "
            f"último dato el año {oldest}, pero hoy estamos en {year}. La respuesta puede estar "
            f"desactualizada: dila con cautela o admite la duda; no la afirmes como hecho actual."
        )
        res.answer = f"{aviso}\n\n{res.answer}" if res.answer else aviso
        trail.append("⚠️stale")
        logger.warning(f"🕐 Sin fuente fresca para '{query_original[:60]}' (máximo año citado: {oldest}).")
        return res, " → ".join(trail)


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
            "deportes, eventos o dudas generales. Utiliza Google Search con respaldo de Tavily y DuckDuckGo. "
            "IMPORTANTE: si la pregunta depende del presente ('último', 'hoy', 'actual', 'quién ganó'), "
            "incluye en la query el año/fecha actual que conoces por el contexto (ej. '2026'), "
            "para evitar respuestas obsoletas de años anteriores."
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
