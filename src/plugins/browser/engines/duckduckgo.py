import re
import urllib.parse
from html import unescape
from typing import Optional, List
import httpx
from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.duckduckgo")


class DuckDuckGoSearchEngine(BaseSearchEngine):
    """
    Motor de búsqueda libre y sin API keys con soporte para Respuestas Instantáneas,
    Clima en tiempo real (wttr.in) y DuckDuckGo API (Fallback Universal).
    """

    def __init__(self, timeout: float = 6.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def _try_weather(self, query: str) -> Optional[SearchResponse]:
        """Consulta directa y rápida para consultas de clima/tiempo meteorológico."""
        lower = query.lower()
        if any(w in lower for w in ("tiempo", "clima", "temperatura", "pronostico", "pronóstico")):
            clean_loc = query
            for prefix in ("tiempo en", "tiempo actual en", "tiempo actual", "clima en", "clima de", "temperatura en", "temperatura de", "pronostico de", "pronóstico de", "el tiempo en"):
                if prefix in lower:
                    clean_loc = lower.split(prefix)[-1].strip()
                    break

            loc_slug = urllib.parse.quote(clean_loc.replace(" ", "_"))
            if not loc_slug:
                return None

            try:
                # Usar User-Agent tipo curl para recibir texto plano limpio de wttr.in
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

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        # 1. Intentar consulta especializada de clima si aplica
        weather_res = await self._try_weather(query)
        if weather_res:
            return weather_res

        # 2. Intentar DuckDuckGo Instant API
        instant_res = await self._try_instant_api(query, max_results=max_results)
        if instant_res and (instant_res.answer or instant_res.results):
            return instant_res

        # 3. Intentar HTML Scraper
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=self.headers) as client:
                resp = await client.post(url, data=data)
                if resp.status_code == 200:
                    html = resp.text
                    items = []
                    matches = re.findall(
                        r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>',
                        html,
                        re.DOTALL
                    )

                    for m in matches[:max_results]:
                        raw_url = m[0].strip()
                        if "uddg=" in raw_url:
                            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                            clean_url = parsed.get("uddg", [raw_url])[0]
                        else:
                            clean_url = raw_url

                        raw_title = re.sub(r'<[^>]+>', '', m[1]).strip()
                        raw_snippet = re.sub(r'<[^>]+>', '', m[2]).strip()

                        items.append(
                            SearchResultItem(
                                title=unescape(raw_title) or "Sin título",
                                url=clean_url,
                                content=unescape(raw_snippet),
                                source_engine=self.name
                            )
                        )

                    if items:
                        return SearchResponse(
                            query=query,
                            results=items,
                            engine_used=self.name,
                            success=True
                        )

            return SearchResponse(query=query, success=False, error="Sin resultados en DuckDuckGo.", engine_used=self.name)
        except Exception as e:
            logger.warning(f"Error en DuckDuckGo Search: {e}")
            return SearchResponse(query=query, success=False, error=str(e), engine_used=self.name)
