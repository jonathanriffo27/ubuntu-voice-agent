import re
import urllib.parse
from html import unescape
from typing import Optional
import httpx
from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.duckduckgo")


class DuckDuckGoSearchEngine(BaseSearchEngine):
    """
    Motor de búsqueda DuckDuckGo libre, gratuito y sin necesidad de API keys (Fallback 2).
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=self.headers) as client:
                resp = await client.post(url, data=data)
                resp.raise_for_status()
                html = resp.text

            items = []
            # Extraer bloques de resultados (url, título y snippet)
            matches = re.findall(
                r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>',
                html,
                re.DOTALL
            )

            for m in matches[:max_results]:
                raw_url = m[0].strip()
                # Limpiar redirecciones de DDG si existen
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

            if not items:
                logger.warning(f"DuckDuckGo no devolvió resultados para: {query}")
                return SearchResponse(query=query, success=False, error="Sin resultados en DuckDuckGo.", engine_used=self.name)

            return SearchResponse(
                query=query,
                results=items,
                engine_used=self.name,
                success=True
            )
        except Exception as e:
            logger.warning(f"Error en DuckDuckGo Search: {e}")
            return SearchResponse(query=query, success=False, error=str(e), engine_used=self.name)
