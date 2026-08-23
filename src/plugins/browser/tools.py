import os
import re
from typing import Dict, Any

import httpx

from src.tools.base import BaseTool, ToolContext, ToolResult


def _redact_secrets(text: str) -> str:
    redacted = re.sub(
        r"(?i)(api_key|password|secret|token|auth|cred)([\s=:\"]+)[A-Za-z0-9\-_]{16,}", 
        r"\1\2[REDACTADO]", 
        text
    )
    return redacted


class BuscarEnInternetTool(BaseTool):
    @property
    def name(self) -> str:
        return "buscar_en_internet"

    @property
    def description(self) -> str:
        return "Realiza una búsqueda en internet y devuelve un resumen de los resultados."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "query": {"type": "STRING", "description": "La consulta a buscar en internet"}
            }, 
            "required": ["query"]
        }

    async def execute(self, context: ToolContext, query: str = None) -> ToolResult:
        if not query:
            return ToolResult(success=False, content="Falta la consulta (query).")
            
        print(f"\n🔍 [ATLAS BUSCANDO EN INTERNET (TAVILY)]: {query}")
        try:
            payload = {
                'api_key': os.environ.get('TAVILY_API_KEY', ''),
                'query': query,
                'search_depth': 'basic',
                'include_answer': True,
                'include_raw_content': False,
                'max_results': 3
            }
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    'https://api.tavily.com/search',
                    json=payload,
                    headers={'Content-Type': 'application/json'}
                )
                response.raise_for_status()
                result = response.json()
            
            output = f"Resultados de búsqueda para: {query}\n\n"
            answer = result.get('answer')
            if answer:
                output += f"Respuesta Directa:\n{answer}\n\n"
                
            output += "Fuentes y Detalles Adicionales:\n"
            for i, r in enumerate(result.get('results', [])):
                title = r.get('title', 'Sin título')
                content = r.get('content', '')
                output += f"{i+1}. {title}\n   {content}\n"
            
            print(f"  ✅ [TAVILY] Búsqueda exitosa ({len(output)} chars)")
            
            safe_output = _redact_secrets(output)
            if len(safe_output) > 2000:
                safe_output = safe_output[:2000] + "\n...[información truncada]"
                
            return ToolResult(success=True, content=safe_output)
            
        except httpx.HTTPStatusError as e:
            print(f"  ❌ [TAVILY] Error HTTP {e.response.status_code}")
            return ToolResult(success=False, content=f"Fallo en la búsqueda de Tavily: HTTP {e.response.status_code}")
        except Exception as e:
            print(f"  ❌ [TAVILY] Error: {e}")
            return ToolResult(success=False, content=f"Fallo en la búsqueda: {str(e)}")
