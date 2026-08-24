from typing import Dict, Any
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.mcp.client import MCPClient
from src.utils.logging import get_logger

logger = get_logger("mcp.adapter")


class MCPToolAdapter(BaseTool):
    """
    Adapta dinámicamente cualquier herramienta de un servidor MCP (Model Context Protocol)
    al contrato BaseTool de Atlas para que esté disponible para el LLM.
    """

    def __init__(self, mcp_client: MCPClient, tool_info: Dict[str, Any], prefix: str = ""):
        self.client = mcp_client
        self.raw_info = tool_info
        self._original_name = tool_info.get("name", "unnamed_tool")

        # Prefijo para evitar colisiones si hay múltiples servidores MCP
        self._tool_name = f"mcp_{prefix}_{self._original_name}" if prefix else f"mcp_{self._original_name}"
        self._description = tool_info.get("description", f"Herramienta MCP {self._original_name}")

        # Adaptación del JSON Schema de entrada
        input_schema = tool_info.get("inputSchema", {})
        self._parameters = self._normalize_schema(input_schema)

    def _normalize_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Normaliza el inputSchema al formato esperado por el registry de Atlas."""
        if not schema or not isinstance(schema, dict):
            return {"type": "OBJECT", "properties": {}}

        schema_type = str(schema.get("type", "OBJECT")).upper()
        return {
            "type": schema_type if schema_type == "OBJECT" else "OBJECT",
            "properties": schema.get("properties", {}),
            "required": schema.get("required", [])
        }

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> Dict[str, Any]:
        return self._parameters

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        logger.info(f"⚡ [MCP Ejecutando {self._original_name} en {self.client.name}] con args: {kwargs}")
        try:
            res = await self.client.call_tool(self._original_name, kwargs)
            is_error = res.get("isError", False)
            content_items = res.get("content", [])

            text_outputs = []
            metadata = {}

            for item in content_items:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "text":
                        text_outputs.append(item.get("text", ""))
                    elif item_type == "image":
                        # Imagen retornada por MCP
                        metadata["inline_data"] = {
                            "mime_type": item.get("mimeType", "image/png"),
                            "data": item.get("data", "")
                        }
                    elif item_type == "resource":
                        resource = item.get("resource", {})
                        text_outputs.append(f"[Recurso: {resource.get('uri')}]\n{resource.get('text', '')}")
                else:
                    text_outputs.append(str(item))

            final_text = "\n".join(text_outputs) if text_outputs else "Operación completada exitosamente."
            return ToolResult(
                success=not is_error,
                content=final_text,
                metadata=metadata if metadata else None
            )
        except Exception as e:
            logger.error(f"Error ejecutando herramienta MCP {self.name}: {e}")
            return ToolResult(success=False, content=f"Error en servidor MCP ({self.client.name}): {e}")
