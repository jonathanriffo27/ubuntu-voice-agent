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
    2. Fallbacks EN PARALELO por prioridad: Tavily → Exa → DuckDuckGo
       (este último vía librería `ddgs`, meta-buscador gratuito sin API keys)

    FreSCo (freshness): las consultas con marcadores temporales relativos
    ("último", "hoy", "reciente"...) se anclan a la fecha actual antes de buscar;
    si la respuesta ganadora solo menciona años pasados, se reintenta una vez
    con el año explícito y, si aun así no mejora, se marca como posiblemente
    desactualizada en vez de presentarla con confianza.
    """

    # Marcadores de "depende de la fecha de hoy". Escritos SIN tildes a
    # propósito: la query se normaliza (NFD sin diacríticos) antes de comparar,
    # porque la voz suele transcribir "último" pero un usuario tecleando escribe
    # "ultimo" — y sin normalizar la detección temporal no se activaba nunca.
    _TEMPORAL_RE = re.compile(
        r"\b(ultim[oa]s?|hoy|ayer|manana|reciente[sm]?|actuale?s?|actualmente|este\s+(ano|mes|semana)|"
        r"proxim[oa]s?|noticias?|ahora|esta\s+semana|quien\s+gano|resultado\s+de)\b",
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
    @staticmethod
    def _normalizar(texto: str) -> str:
        """Minúsculas y sin diacríticos (NFD): 'último' ≡ 'ultimo', 'mañana' ≡ 'manana'."""
        import unicodedata
        return "".join(
            c for c in unicodedata.normalize("NFD", texto or "")
            if unicodedata.category(c) != "Mn"
        ).lower()

    @classmethod
    def _es_temporal(cls, query: str) -> bool:
        return bool(cls._TEMPORAL_RE.search(cls._normalizar(query)))

    @staticmethod
    def _fecha_hoy() -> str:
        now = time.localtime()
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        return f"{now.tm_mday} de {meses[now.tm_mon - 1]} de {now.tm_year}"

    @classmethod
    def _anclar_query(cls, query: str) -> str:
        """Añade la fecha actual si la query es temporal y no trae ya un año."""
        if not cls._es_temporal(query):
            return query
        if cls._YEAR_RE.search(query):
            return query  # ya viene anclada por el LLM
        return f"{query} (hoy es {cls._fecha_hoy()})"

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

    # Prioridad de desempate cuando varios motores responden a la vez.
    _PRIORIDAD = {"GOOGLE": 0, "TAVILY": 1, "EXA": 2, "DUCKDUCKGO": 3}
    # Presupuesto total de la carrera; si nadie responde útil antes, se reporta.
    _RACE_TIMEOUT_S = 15.0
    # Ventana extra tras un ganador para recolectar snippets de otros motores
    # (alimenta la fusión multi-motor sin pagar su latencia completa).
    _GRACE_S = 0.6

    async def _engine_race(self, q: str, max_results: int):
        """Todos los motores a la vez; gana el PRIMERO con resultado útil.

        Justificación medida (sep-2026, esta red): Exa responde en ~1.3s y
        Tavily ~1.9s con buena calidad, mientras Google Grounding se muere en
        timeout (~5-10s) en gran parte de las sesiones. Con el esquema
        secuencial 'Google → fallbacks' cada consulta simple pagaba 10-13s;
        correrlos en paralelo baja el tiempo percibido a ~1.5-2s.

        Tras un ganador hay una ventana de gracia de _GRACE_S para que el resto
        termine: así la fusión multi-motor conserva sus snippets sin coste real.

        Devuelve (ganador_o_None, ultimo_res, resultados_por_motor, trail).
        """
        import time as _tm
        motores = [
            ("GOOGLE", self.google_engine.search(q, max_results=max_results)),
            ("TAVILY", self.tavily_engine.search(q, max_results=max_results)),
            ("EXA", self.exa_engine.search(q, max_results=max_results)),
            ("DUCKDUCKGO", self.ddg_engine.search(q, max_results=max_results)),
        ]
        restantes = {asyncio.create_task(coro): nombre for nombre, coro in motores}
        trail: list = []
        ganador = None
        ultimo_res = None
        resultados: dict = {}
        limite = _tm.monotonic() + self._RACE_TIMEOUT_S

        def _util(r):
            return r is not None and r.success and (r.answer or r.results)

        def _procesar(done_set):
            nonlocal ganador, ultimo_res
            for t in sorted(done_set, key=lambda t: self._PRIORIDAD[restantes[t]]):
                nombre = restantes.pop(t)
                try:
                    r = t.result()
                except Exception:
                    trail.append(f"{nombre} ❌")
                    continue
                ultimo_res = r
                resultados[nombre] = r
                if _util(r):
                    if ganador is None:
                        ganador = (nombre, r)
                        trail.append(f"{nombre} ✅")
                    else:
                        trail.append(f"{nombre} ✓")
                else:
                    trail.append(f"{nombre} ❌")

        while restantes:
            espera = max(0.05, limite - _tm.monotonic())
            done, _ = await asyncio.wait(set(restantes), timeout=espera, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            _procesar(done)
            if ganador is not None and restantes:
                # Ventana de gracia: dejar que el resto complete (típicamente ya
                # casi terminan) para alimentar la fusión multi-motor sin pagar
                # su latencia completa.
                fin_gracia = _tm.monotonic() + self._GRACE_S
                espera_gracia = max(0.05, min(fin_gracia - _tm.monotonic(), limite - _tm.monotonic()))
                done2, _ = await asyncio.wait(set(restantes), timeout=espera_gracia,
                                              return_when=asyncio.ALL_COMPLETED)
                if done2:
                    _procesar(done2)
                break
            if ganador is not None:
                break
        for t, nombre in restantes.items():
            t.cancel()
        return ganador, ultimo_res, resultados, trail

    async def search(self, query: str, max_results: int = 4) -> Tuple[Any, str]:
        temporal = self._es_temporal(query)
        q = self._anclar_query(query) if temporal else query

        # 0. Fast-path de clima: wttr.in responde en ~300ms sin consumir cuota de nadie
        weather_res = await self.ddg_engine.weather_fast_path(query)
        if weather_res and weather_res.success and weather_res.answer:
            return weather_res, "CLIMA ✅"

        ganador, ultimo_res, resultados, trail = await self._engine_race(q, max_results)

        if ganador:
            nombre, res_ganador = ganador
            # Fusión multi-motor con lo QUE YA tenemos de la carrera (sin
            # re-lanzar queries): si la fuente ganadora no contesta bien, el
            # modelo rellena con memoria; adjuntar los top-1 de los demás sube
            # mucho la probabilidad de que la respuesta real esté a la vista.
            ganador_results = list(res_ganador.results or [])
            urls = {r.url for r in ganador_results}
            extra = []
            for otro_nombre in ("GOOGLE", "TAVILY", "EXA", "DUCKDUCKGO"):
                if otro_nombre == nombre:
                    continue
                r = resultados.get(otro_nombre)
                if r is None or not (r.success and (r.answer or r.results)):
                    continue
                for item in (r.results or [])[:1]:
                    if item.url not in urls:
                        urls.add(item.url)
                        extra.append(item)
            if extra:
                res_ganador.results = ganador_results[:2] + extra + ganador_results[2:]
            return await self._verificar_frescura(res_ganador, query, temporal, trail)

        return ultimo_res, " → ".join(trail)

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
            "deportes, eventos o dudas generales. Utiliza Google Search con respaldo de Tavily, Exa y DuckDuckGo. "
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

        # Defensa anti-memoria: los modelos Live con function calling asíncrono
        # pueden seguir respondiendo "de memoria" pese a tener el resultado a la
        # vista. La nota va pegada a los datos para anclar la respuesta a ellos.
        if MultiEngineSearchManager._es_temporal(query):
            output += (
                f"📅 CONTEXTO TEMPORAL: hoy es {MultiEngineSearchManager._fecha_hoy()}. "
                "Estas fuentes recogen lo publicado MÁS RECIENTEMENTE: si contradicen tu "
                "memoria de entrenamiento, tu memoria está desactualizada y prevalecen las "
                "fuentes. Si traen una lista por años (palmarés, rankings), el dato correcto "
                "es el del AÑO MÁS RECIENTE.\n\n"
            )
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
