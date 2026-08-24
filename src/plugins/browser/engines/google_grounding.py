import asyncio
import os
import re
from typing import Optional, List
from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.google")


class GoogleGroundingSearchEngine(BaseSearchEngine):
    """
    Motor primario oficial de Google Search Grounding usando el SDK de Gemini.
    Optimizado con timeout estricto de 2.5s para no demorar la respuesta de voz.
    """

    def __init__(self, model: str = "gemini-2.5-flash", timeout: float = 2.5):
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "google_grounding"

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return SearchResponse(query=query, success=False, error="GEMINI_API_KEY no configurada.", engine_used=self.name)

        async def _call_google():
            from google import genai
            from google.genai import types

            client = genai.Client()
            prompt = (
                f"Busca en Google información actualizada sobre: '{query}'. "
                "Devuelve un resumen claro, exacto y directo en español con los datos más recientes."
            )

            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2
            )

            response = await client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config
            )

            answer_text = response.text or ""
            items = []

            if response.candidates and response.candidates[0].grounding_metadata:
                gm = response.candidates[0].grounding_metadata
                chunks = getattr(gm, "grounding_chunks", []) or []
                for chunk in chunks[:max_results]:
                    web = getattr(chunk, "web", None)
                    if web and getattr(web, "uri", None):
                        items.append(
                            SearchResultItem(
                                title=getattr(web, "title", "Fuente de Google"),
                                url=web.uri,
                                content="",
                                source_engine=self.name
                            )
                        )

            return SearchResponse(
                query=query,
                answer=answer_text,
                results=items,
                engine_used=self.name,
                success=True
            )

        try:
            return await asyncio.wait_for(_call_google(), timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.debug(f"Google Search Grounding timeout ({self.timeout}s).")
            return SearchResponse(query=query, success=False, error=f"Timeout ({self.timeout}s)", engine_used=self.name)
        except Exception as e:
            logger.debug(f"Google Search Grounding fallo: {e}")
            return SearchResponse(query=query, success=False, error=str(e), engine_used=self.name)
