import asyncio
import re
import urllib.parse
from typing import Optional, List
import httpx
from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.duckduckgo")


class DuckDuckGoSearchEngine(BaseSearchEngine):
    """
    Motor de búsqueda libre y sin API keys con soporte para Respuestas Instantáneas,
    Clima en tiempo real (wttr.in) y búsqueda generalista vía librería `ddgs`
    (meta-buscador: duckduckgo, bing, brave, mojeek...). Reemplaza al scraper HTML
    de html.duckduckgo.com, bloqueado por anti-bot (HTTP 202 anomaly challenge).
    """

    def __init__(self, timeout: float = 6.0, region: str = "cl-es"):
        self.timeout = timeout
        self.region = region
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def weather_fast_path(self, query: str) -> Optional[SearchResponse]:
        """
        Atajo público para consultas de clima: wttr.in responde en ~300ms gratis.
        Usado por el orquestador ANTES de gastar cuota de Google/Tavily.
        """
        return await self._try_weather(query)

    # Palabras que NO forman parte del nombre de la ciudad (ruido temporal/común)
    _WEATHER_PREFIXES = (
        "el tiempo en", "tiempo actual en", "tiempo actual", "tiempo en", "tiempo de",
        "clima actual en", "clima en", "clima de", "temperatura en", "temperatura de",
        "pronóstico del tiempo en", "pronostico del tiempo en", "pronostico de", "pronóstico de",
        "pronostico para", "pronóstico para", "el clima en", "el tiempo de", "tiempo",
        "clima", "temperatura", "pronóstico", "pronostico",
    )
    _FUTURE_WORDS = ("mañana", "manana", "pasado mañana", "próximos días", "proximos dias",
                     "fin de semana", "esta noche", "esta tarde", "esta semana")
    # Verbres/partículas interrogativas que el LLM añade al reformular ("que tiempo hará...")
    _VERB_NOISE_RE = re.compile(
        r"\b(que|qué|cual|cuál|como|cómo|cuanto|cuánto|hara|hará|hace|hacia|hacía|"
        r"esta|está|estara|estará|estuvo|hay|dime|sabes|por favor)\b"
    )
    _MONTHS_RE = re.compile(
        r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre|ene|feb|abr|sep|sept|oct|nov|dic|"
        r"(19|20)\d{2})\b"
    )  # Nota: 'mar' NO se abrevia aquí: rompería topónimos como 'Viña del Mar'
    _NOISE_RE = re.compile(
        r"\b(mañana|manana|pasado\s+mañana|hoy|ahora|actual(mente)?|(esta\s+)?(noche|tarde|semana)|"
        r"(el\s+)?fin\s+de\s+semana|los\s+pr(ó|o)ximos\s+d(í|i)as|"
        r"\d{1,2}\s+(de\s+)?\w+(\s+de\s+\d{4})?)\b"
    )

    def _clean_weather_location(self, query: str) -> str:
        """Extrae la ubicación real removiendo prefijos, verbos de consulta y fechas."""
        lower = query.lower().strip("¿?.,!")

        # 1. Quitar palabras interrogativas/verbales que el LLM agrega al reformular
        lower = self._VERB_NOISE_RE.sub(" ", lower)

        # 2. Quitar el prefijo de clima (el más específico primero)
        loc = lower
        for prefix in self._WEATHER_PREFIXES:
            if prefix in lower:
                loc = lower.split(prefix, 1)[-1]
                break

        # 3. Quitar referencias temporales y fechas
        loc = self._NOISE_RE.sub(" ", loc)
        loc = self._MONTHS_RE.sub(" ", loc)

        # 4. Rescate: si queda un " ... en X", la ciudad suele ser lo último
        loc = " ".join(loc.split())  # colapsar espacios sobrantes
        if " en " in f" {loc} ":
            tail = loc.rsplit(" en ", 1)[-1].strip()
            if tail:
                loc = tail

        # 5. Preposiciones residuales al inicio (NO artículos: 'La Serena', 'El Quisco',
        #    'Viña del Mar' los llevan como parte del nombre oficial)
        loc = re.sub(r"^(en|para)\s+", "", loc).strip(" ,.!?")
        return loc

    async def _try_weather(self, query: str) -> Optional[SearchResponse]:
        """Consulta directa y rápida de clima: actual o pronóstico de mañana."""
        lower = query.lower()
        if not any(w in lower for w in ("tiempo", "clima", "temperatura", "pronostico", "pronóstico")):
            return None

        wants_forecast = any(w in lower for w in self._FUTURE_WORDS)
        day_index = 2 if ("pasado mañana" in lower or "pasado manana" in lower) else 1
        clean_loc = self._clean_weather_location(query)
        if not clean_loc:
            return None
        loc_slug = urllib.parse.quote(clean_loc.replace(" ", "_"))
        if not loc_slug:
            return None

        try:
            if wants_forecast:
                return await self._fetch_tomorrow_forecast(query, loc_slug, clean_loc, day_index)

            # Clima actual: texto plano ultrarrápido
            async with httpx.AsyncClient(timeout=3.5, headers={"User-Agent": "curl/8.5.0"}) as client:
                r = await client.get(f"https://wttr.in/{loc_slug}?format=3")
                if r.status_code == 200 and r.text.strip() and not r.text.startswith("<"):
                    weather_info = r.text.strip()
                    return SearchResponse(
                        query=query,
                        answer=f"Condición meteorológica actual: {weather_info}",
                        results=[
                            SearchResultItem(
                                title=f"Reporte de Clima para {clean_loc.title()}",
                                url=f"https://wttr.in/{loc_slug}",
                                content=weather_info,
                                source_engine=self.name
                            )
                        ],
                        engine_used=self.name,
                        success=True
                    )
        except Exception as e:
            logger.debug(f"wttr.in no disponible: {e}")
        return None

    async def _fetch_tomorrow_forecast(self, query: str, loc_slug: str, clean_loc: str, day_index: int = 1) -> Optional[SearchResponse]:
        """Pronóstico estructurado vía wttr.in JSON (format=j1, gratis). day_index: 1=mañana, 2=pasado mañana."""
        async with httpx.AsyncClient(timeout=4.0, headers={"User-Agent": "curl/8.5.0"}) as client:
            r = await client.get(f"https://wttr.in/{loc_slug}?format=j1&lang=es")
            if r.status_code != 200:
                return None
            data = r.json()

        days = data.get("weather", [])
        if len(days) <= day_index:
            return None
        tomorrow = days[day_index]
        day_label = "mañana" if day_index == 1 else "pasado mañana"

        max_c = tomorrow.get("maxtempC", "?")
        min_c = tomorrow.get("mintempC", "?")

        # Descripción en español (~mediodía) y máxima probabilidad de lluvia del día
        desc = ""
        max_rain = 0
        hourly = tomorrow.get("hourly", [])
        for h in hourly:
            try:
                max_rain = max(max_rain, int(h.get("chanceofrain", 0) or 0))
            except (TypeError, ValueError):
                pass
        noon = hourly[4] if len(hourly) > 4 else (hourly[0] if hourly else {})
        lang_es = noon.get("lang_es") or []
        if lang_es and lang_es[0].get("value"):
            desc = lang_es[0]["value"]
        else:
            wdesc = noon.get("weatherDesc") or []
            if wdesc:
                desc = wdesc[0].get("value", "")

        summary = f"Pronóstico para {day_label} en {clean_loc.title()}: {desc}. Mínima {min_c}°C, máxima {max_c}°C."
        if max_rain > 20:
            summary += f" Probabilidad de lluvia hasta {max_rain}%."
        summary += " (fuente: wttr.in)"

        return SearchResponse(
            query=query,
            answer=summary,
            results=[
                SearchResultItem(
                    title=f"Pronóstico de {day_label} para {clean_loc.title()}",
                    url=f"https://wttr.in/{loc_slug}",
                    content=summary,
                    source_engine=self.name
                )
            ],
            engine_used=self.name,
            success=True
        )

    async def _try_instant_api(self, query: str, max_results: int = 4) -> Optional[SearchResponse]:
        """Consulta la API de respuestas instantáneas y tópicos relacionados de DuckDuckGo."""
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=0"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    abstract = data.get("AbstractText", "").strip()
                    heading = data.get("Heading", "")
                    abstract_url = data.get("AbstractURL", "")

                    items = []
                    if abstract and abstract_url:
                        items.append(
                            SearchResultItem(
                                title=heading or query.title(),
                                url=abstract_url,
                                content=abstract,
                                source_engine=self.name
                            )
                        )

                    # Tópicos relacionados
                    for topic in data.get("RelatedTopics", [])[:max_results]:
                        if isinstance(topic, dict) and topic.get("FirstURL") and topic.get("Text"):
                            items.append(
                                SearchResultItem(
                                    title=topic.get("Text", "")[:60],
                                    url=topic.get("FirstURL"),
                                    content=topic.get("Text", ""),
                                    source_engine=self.name
                                )
                            )

                    if abstract or items:
                        return SearchResponse(
                            query=query,
                            answer=abstract if abstract else None,
                            results=items[:max_results],
                            engine_used=self.name,
                            success=True
                        )
        except Exception as e:
            logger.debug(f"DDG Instant API error: {e}")
        return None

    async def _ddgs_search(self, query: str, max_results: int) -> SearchResponse:
        """
        Búsqueda generalista vía librería `ddgs` (meta-buscador sin API keys:
        duckduckgo, bing, brave, mojeek, startpage...). Es síncrona, así que se
        ejecuta en un hilo para no bloquear el loop de voz.
        """
        try:
            from ddgs import DDGS
        except ImportError:
            return SearchResponse(
                query=query, success=False,
                error="Paquete 'ddgs' no instalado (pip install ddgs).",
                engine_used=self.name
            )

        try:
            timeout, region = self.timeout, self.region

            def _run():
                return DDGS(timeout=timeout).text(
                    query, region=region, safesearch="moderate", max_results=max_results
                )

            raw = await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning(f"Error en búsqueda ddgs: {e}")
            return SearchResponse(query=query, success=False, error=str(e), engine_used=self.name)

        items = [
            SearchResultItem(
                title=(r.get("title") or "Sin título").strip(),
                url=(r.get("href") or "").strip(),
                content=(r.get("body") or "").strip(),
                source_engine=self.name
            )
            for r in (raw or [])[:max_results]
            if r.get("href")
        ]
        if not items:
            return SearchResponse(
                query=query, success=False,
                error="Sin resultados en DuckDuckGo/ddgs.", engine_used=self.name
            )
        return SearchResponse(query=query, results=items, engine_used=self.name, success=True)

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        # 1. Intentar consulta especializada de clima si aplica
        weather_res = await self._try_weather(query)
        if weather_res:
            return weather_res

        # 2. Intentar DuckDuckGo Instant API (respuestas enciclopédicas)
        instant_res = await self._try_instant_api(query, max_results=max_results)
        if instant_res and (instant_res.answer or instant_res.results):
            return instant_res

        # 3. Búsqueda generalista vía ddgs (sustituto del scraper HTML bloqueado)
        return await self._ddgs_search(query, max_results)
