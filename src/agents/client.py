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
        self.api_key = api_key or os.environ.get("CLIPROXY_API_KEY", "YOUR_CLIPROXY_API_KEY")
        self.timeout = timeout

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gemini-3.7-flash-high",
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
        logger.debug(f"Petición a CLIProxy [{model}] ({len(messages)} mensajes)")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data
