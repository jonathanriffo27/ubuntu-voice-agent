import base64
from typing import Dict, Any, Optional
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.vision.service import ScreenCapture

class AnalizarPantallaTool(BaseTool):
    """
    Toma una captura de pantalla del monitor principal y devuelve
    la imagen en Base64. (Gemini Live soporta enviar el b64 como
    parte de los resultados de la función o puedes inyectarlo al contexto).
    """
    def __init__(self, screen_service: ScreenCapture):
        self.screen_service = screen_service

    @property
    def name(self) -> str:
        return "analizar_pantalla"

    @property
    def description(self) -> str:
        return "Captura lo que hay en la pantalla actual del usuario para que puedas analizar errores, código o cualquier contexto visual."

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        try:
            image_bytes = self.screen_service.capture_screen()
            b64_img = base64.b64encode(image_bytes).decode('utf-8')
            
            # Devolvemos el mimetype y los datos. El Assistant o Gemini
            # sabrán interpretarlo si lo pasamos en el metadata.
            metadata = {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": b64_img
                }
            }
            return ToolResult(
                success=True, 
                content="Captura de pantalla realizada. Se incluye el inline_data en la respuesta para tu análisis.",
                metadata=metadata
            )
        except Exception as e:
            return ToolResult(success=False, content=f"No se pudo capturar la pantalla: {e}")
