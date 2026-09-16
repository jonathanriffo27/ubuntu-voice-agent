"""
Exa.ai — Fallback semántico opcional (activo solo si existe EXA_API_KEY).

Tier gratuito 2026: ~$10/mes en créditos (~1400 búsquedas), sin tarjeta.
Ventaja frente a Tavily: devuelve CONTENIDO REAL de las páginas (texto
parseado), no síntesis de LLM → sin riesgo de alucinación en la respuesta.
Si no hay key configurada, el motor se reporta no disponible y la cadena
de fallback lo ignora sin coste alguno.
"""
import os
from typing import Optional

import httpx

from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from .tavily import _redact_secrets
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.exa")


class ExaSearchEngine(BaseSearchEngine):
    """Búsqueda neural/keyword de Exa con contenido crudo (fallback opcional)."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")

    @property
    def name(self) -> str:
        return "exa"

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        if not self.api_key:
            return SearchResponse(query=query, success=False,
                                  error="EXA_API_KEY no configurada.", engine_used=self.name)
        payload = {
            "query": query,
            "numResults": max_results,
            "contents": {"text": {"maxCharacters": 600}},  # texto crudo de la página
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api.exa.ai/search",
                    json=payload,
                    headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()

            items = []
            for r in data.get("results", []):
                items.append(SearchResultItem(
                    title=r.get("title", "Sin título"),
                    url=r.get("url", ""),
                    content=_redact_secrets(r.get("text", "")),
                    source_engine=self.name,
                ))
            if not items:
                return SearchResponse(query=query, success=False,
                                      error="Exa devolvió 0 resultados.", engine_used=self.name)
            return SearchResponse(
                query=query, answer=None, results=items,
                engine_used=self.name, success=True,
            )
        except Exception as e:
            logger.warning(f"Fallo en Exa Search: {e}")
            return SearchResponse(query=query, success=False, error=str(e), engine_used=self.name)
