import asyncio
import json
import os
import time
from typing import Optional, List
from .base import BaseSearchEngine, SearchResponse, SearchResultItem
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.google")

_STATE_PATH = os.path.expanduser("~/.cache/atlas/google_grounding_state.json")


class GoogleGroundingSearchEngine(BaseSearchEngine):
    """
    Motor primario oficial de Google Search Grounding usando el SDK oficial de Gemini (google-genai).
    Rota entre modelos compatibles con grounding y cortocircuita automáticamente
    si se agota la cuota gratuita diaria (HTTP 429), delegando a Tavily/DuckDuckGo
    sin penalizar la latencia de las búsquedas siguientes.
    """

    # Orden de preferencia: más reciente primero, clásico estable como último recurso
    MODEL_CANDIDATES = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
    ]

    # Si todos los modelos agotan cuota (429), no reintentar hasta pasado este lapso
    QUOTA_COOLDOWN_SECONDS = 1800  # 30 min

    def __init__(self, model: Optional[str] = None, timeout: float = 5.0):
        self.timeout = timeout
        self._client = None
        # Penalización individual por modelo: timestamp hasta el cual no reintentarlo (429)
        self._model_blocked_until: dict = {}
        self._load_state()
        # El modelo preferido se pisa solo si se pasó explícitamente
        if model:
            self.model = model

    # ------------------------------------------------------------------
    # Persistencia ligera del estado del motor (sobrevive reinicios y hot-reload)
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        """Restaura el último modelo exitoso y los bloqueos de cuota vigentes."""
        now = time.time()
        self.model = self.MODEL_CANDIDATES[0]
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved_model = data.get("model")
                if saved_model in self.MODEL_CANDIDATES:
                    self.model = saved_model
                for m, until in (data.get("blocked_until") or {}).items():
                    if until > now:
                        self._model_blocked_until[m] = until
        except Exception as e:
            logger.debug(f"Estado previo de grounding no cargado: {e}")

    def _save_state(self) -> None:
        """Persiste el modelo preferido actual y los bloqueos de cuota vigentes."""
        try:
            os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
            now = time.time()
            payload = {
                "model": self.model,
                "blocked_until": {
                    m: until for m, until in self._model_blocked_until.items() if until > now
                },
            }
            tmp = f"{_STATE_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, _STATE_PATH)
        except Exception as e:
            logger.debug(f"No se pudo persistir estado de grounding: {e}")

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        return self._client

    @property
    def name(self) -> str:
        return "google_grounding"

    async def _try_model(self, query: str, model: str, max_results: int) -> Optional[SearchResponse]:
        """Intento individual de grounding con un modelo concreto. None => fallo."""
        from google.genai import types

        client = self._get_client()
        prompt = (
            f"Consulta de búsqueda en tiempo real: '{query}'. "
            "Responde de forma muy concisa, directa y precisa en español (máximo 2 a 3 frases) con los datos más recientes."
        )
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1
        )

        response = await asyncio.wait_for(
            client.aio.models.generate_content(model=model, contents=prompt, config=config),
            timeout=self.timeout
        )

        answer_text = response.text or ""
        if not answer_text.strip():
            return None

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

    async def search(self, query: str, max_results: int = 4) -> SearchResponse:
        if not os.environ.get("GEMINI_API_KEY"):
            return SearchResponse(query=query, success=False, error="GEMINI_API_KEY no configurada.", engine_used=self.name)

        now = time.time()

        # Rotación de modelos: el preferido primero, saltando los que agotaron
        # su cuota recientemente (cada 429 bloquea ese modelo por 30 min).
        ordered = [self.model] + [m for m in self.MODEL_CANDIDATES if m != self.model]
        models_to_try = [
            m for m in ordered if now >= self._model_blocked_until.get(m, 0.0)
        ]

        if not models_to_try:
            remaining = int(min(self._model_blocked_until.values()) - now)
            return SearchResponse(
                query=query,
                success=False,
                error=f"Cuota de Google Grounding agotada (reintento en ~{max(remaining, 60) // 60} min).",
                engine_used=self.name
            )

        errors = []
        for model in models_to_try:
            t0 = time.time()
            try:
                result = await self._try_model(query, model, max_results)
                if result:
                    if model != self.model:
                        self.model = model  # promover el modelo que funcionó
                        logger.info(f"Google Grounding ahora usa '{model}' (rotado por cuota/fallo).")
                        self._save_state()
                    return result
                errors.append(f"{model}: respuesta vacía")
            except asyncio.TimeoutError:
                errors.append(f"{model}: timeout ({self.timeout}s)")
                logger.debug(f"Google Grounding timeout con {model} tras {time.time()-t0:.1f}s")
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    # Bloquear SOLO este modelo: los demás pueden tener cuota propia
                    self._model_blocked_until[model] = now + self.QUOTA_COOLDOWN_SECONDS
                    self._save_state()
                    errors.append(f"{model}: cuota agotada (429)")
                    # El trail de búsqueda ('GOOGLE ❌(cuota)') ya informa al usuario;
                    # el detalle técnico queda en el log de archivo.
                    logger.debug(f"Google Grounding: cuota agotada en {model}, bloqueado {self.QUOTA_COOLDOWN_SECONDS // 60} min.")
                    # Avisar en consola SOLO cuando ya no queda ningún modelo utilizable
                    available = [m for m in self.MODEL_CANDIDATES if self._model_blocked_until.get(m, 0.0) <= now]
                    if not available:
                        logger.warning("Google Grounding sin cuota disponible en ningún modelo; usando fallbacks.")
                else:
                    errors.append(f"{model}: {type(e).__name__} {err[:120]}")
                    logger.debug(f"Google Grounding fallo con {model}: {e}")

        return SearchResponse(
            query=query,
            success=False,
            error="; ".join(errors) if errors else "Sin resultados de Google Grounding.",
            engine_used=self.name
        )
