import base64
import time
from typing import Dict, Any, Optional

from src.tools.base import BaseTool, ToolContext, ToolResult
from src.vision.service import ScreenCapture, OptimizedScreenCaptureService
from src.utils.logging import get_logger

logger = get_logger("plugins.vision")


class AnalizarPantallaTool(BaseTool):
    """
    Toma una captura de pantalla ultrarrápida y optimizada del monitor actual.
    Inyecta la imagen comprimida en Base64 en el flujo multimodal para que Atlas
    o el modelo puedan analizar visualmente errores, confirmar si una tarea GUI se
    completó exitosamente o entender qué está viendo el usuario.
    """

    def __init__(self, screen_service: Optional[ScreenCapture] = None):
        self.screen_service = screen_service or OptimizedScreenCaptureService()
        self.last_capture_time = 0.0

    @property
    def name(self) -> str:
        return "analizar_pantalla"

    @property
    def description(self) -> str:
        return (
            "Captura la pantalla actual del usuario. Úsalo ÚNICAMENTE cuando el usuario te pida "
            "explícitamente mirar, revisar o analizar su pantalla (ej: 'mira mi pantalla', 'qué error ves'), "
            "o para verificar si una automatización GUI finalizó con éxito. NUNCA lo uses por iniciativa "
            "propia en conversaciones ordinarias ni para adivinar dudas generales."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "motivo": {
                    "type": "STRING",
                    "description": "Breve explicación de por qué tomas la captura (ej: 'verificar envío de mensaje', 'leer error en terminal')."
                }
            }
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if hasattr(context.config, "vision") and not context.config.vision.enabled:
            return ToolResult(success=False, content="La herramienta de visión está deshabilitada en la configuración.")

        now = time.time()
        if now - self.last_capture_time < 1.0:  # 1 segundo de cooldown
            return ToolResult(success=False, content="Espera un momento antes de tomar otra captura de pantalla.")

        try:
            self.last_capture_time = now
            t0 = time.time()
            frame_meta = {"frame_id": "frame_legacy", "unchanged": False, "change_ratio": 1.0}
            image_bytes = None
            if hasattr(self.screen_service, "capture_screen_with_metadata"):
                try:
                    ret = self.screen_service.capture_screen_with_metadata(max_dim=1280, quality=70)
                    if isinstance(ret, tuple) and len(ret) == 2 and isinstance(ret[1], dict):
                        image_bytes, frame_meta = ret
                except Exception:
                    pass

            if image_bytes is None:
                image_bytes = self.screen_service.capture_screen(max_dim=1280, quality=70)

            elapsed = time.time() - t0
            b64_img = base64.b64encode(image_bytes).decode("utf-8")

            unchanged_tag = " (Sin cambios perceptibles respecto al fotograma previo)" if frame_meta.get("unchanged") else ""
            logger.info(f"📸 Captura optimizada tomada en {elapsed:.2f}s ({len(image_bytes)/1024:.1f} KB){unchanged_tag}.")

            metadata = {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": b64_img
                },
                "frame_id": frame_meta.get("frame_id"),
                "unchanged": frame_meta.get("unchanged", False),
                "change_ratio": frame_meta.get("change_ratio", 1.0),
                "scale_factor_x": frame_meta.get("scale_factor_x", 1.0),
                "scale_factor_y": frame_meta.get("scale_factor_y", 1.0)
            }
            motivo = kwargs.get("motivo", "").strip()
            detalle = f" Motivo: '{motivo}'." if motivo else ""
            return ToolResult(
                success=True,
                content=f"Captura de pantalla realizada exitosamente ({len(image_bytes)/1024:.1f} KB){unchanged_tag}.{detalle} Analiza la imagen para responder.",
                metadata=metadata
            )
        except Exception as e:
            logger.error(f"Error en capturar pantalla: {e}")
            return ToolResult(success=False, content=f"No se pudo capturar la pantalla: {str(e)}")
