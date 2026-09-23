import httpx
from typing import List, Dict, Any, Optional
from src.utils.logging import get_logger

logger = get_logger("agents.client")


class CLIProxyClient:
    """
    Cliente asíncrono OpenAI-compatible para comunicarse con CLIProxyAPI
    corriendo en el servidor Oracle a través del túnel local (127.0.0.1:8317).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0
    ):
        import os
        self.base_url = (base_url or os.environ.get("CLIPROXY_BASE_URL", "http://127.0.0.1:8317/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("CLIPROXY_API_KEY", "")
        if not self.api_key:
            logger.warning(
                "CLIPROXY_API_KEY no está configurada. El subagente desarrollador "
                "fallará al autenticar. Exporta: export CLIPROXY_API_KEY=tu_clave"
            )
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Devuelve un cliente HTTP persistente (keep-alive), creándolo si es necesario."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        """Cierra el cliente HTTP persistente y libera las conexiones."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gemini-3.8-flash-high",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2
    ) -> Dict[str, Any]:
        """
        Envía una petición de chat completion con soporte para function calling y reasoning.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.base_url}/chat/completions"
        logger.debug(f"Petición a CLIProxy [{model}] ({len(messages)} mensajes, {len(tools or [])} tools)")

        client = await self._get_client()
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = e.response.text[:1000]
            except Exception:
                pass
            logger.error(
                f"CLIProxy HTTP {e.response.status_code}: {body or '(sin body)'}"
            )
            raise RuntimeError(
                f"CLIProxy respondió HTTP {e.response.status_code}: {body or '(sin detalle)'}"
            ) from e
        except httpx.TimeoutException as e:
            logger.error(f"Timeout en petición a CLIProxy ({self.timeout}s)")
            raise RuntimeError(
                f"Timeout ({self.timeout}s) esperando respuesta de CLIProxy. "
                "Considera simplificar la instrucción o aumentar el timeout."
            ) from e
