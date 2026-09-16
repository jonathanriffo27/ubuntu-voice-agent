import os
import re
import httpx
from typing import Optional
from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.tavily")


def _redact_secrets(text: str) -> str:
    return re.sub(
        r"(?i)(api_key|password|secret|token|auth|cred)([\s=:\"]+)[A-Za-z0-9\-_]{16,}", 
        r"\1\2[REDACTADO]", 
        text
    )


class TavilySearchEngine(BaseSearchEngine):
    """Motor de búsqueda Tavily API (Fallback 1).

    MODO SNIPPETS CRUDOS: `include_answer=False` a propósito. El campo `answer`
    de Tavily es síntesis de un LLM interno y ALUCINA (incidente 2026-09-16:
    afirmó "Argentina ganó el Mundial 2026"; la verdad: España 1-0). La
    síntesis para voz la hace el LLM de Atlas a partir de los snippets reales.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")

    @property
    def name(self) -> str:
        return "tavily"

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        if not self.api_key:
            return SearchResponse(query=query, success=False, error="TAVILY_API_KEY no configurada.", engine_used=self.name)

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": False,  # respuesta sintetizada por LLM de Tavily: riesgo de alucinación
            "include_raw_content": False,
            "max_results": max_results
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post("https://api.tavily.com/search", json=payload)
                resp.raise_for_status()
                data = resp.json()

            items = []
            for r in data.get("results", []):
                items.append(
                    SearchResultItem(
                        title=r.get("title", "Sin título"),
                        url=r.get("url", ""),
                        content=_redact_secrets(r.get("content", "")),
                        source_engine=self.name
                    )
                )

            return SearchResponse(
                query=query,
                answer=data.get("answer"),
                results=items,
                engine_used=self.name,
                success=True
            )
        except Exception as e:
            logger.warning(f"Fallo en Tavily Search: {e}")
            return SearchResponse(query=query, success=False, error=str(e), engine_used=self.name)
