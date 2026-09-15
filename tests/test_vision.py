import pytest
from unittest.mock import MagicMock, patch
from PIL import Image
import io

from src.tools.base import ToolContext
from src.vision.service import OptimizedScreenCaptureService
from src.plugins.vision.tools import AnalizarPantallaTool


def test_optimized_screen_capture_service_process_image():
    service = OptimizedScreenCaptureService(default_max_dim=1280, default_quality=70)
    
    # Crear una imagen simulada grande (1920x1080)
    img = Image.new("RGBA", (1920, 1080), color=(255, 0, 0, 255))
    data = service._optimize_pil_image(img, max_dim=1280, quality=70)
    
    assert isinstance(data, bytes)
    assert len(data) > 0
    
    # Comprobar que la imagen resultante fue redimensionada a max 1280
    res_img = Image.open(io.BytesIO(data))
    assert res_img.width <= 1280
    assert res_img.height <= 1280
    assert res_img.format == "JPEG"


@pytest.mark.asyncio
async def test_analizar_pantalla_tool_metadata():
    tool = AnalizarPantallaTool()
    assert tool.name == "analizar_pantalla"
    assert "pantalla" in tool.description.lower()
    assert "motivo" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_analizar_pantalla_tool_execute():
    mock_service = MagicMock()
    # Devolver bytes JPEG simulados
    img = Image.new("RGB", (640, 480), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    mock_service.capture_screen.return_value = buf.getvalue()

    tool = AnalizarPantallaTool(screen_service=mock_service)
    ctx = ToolContext(config=None)

    result = await tool.execute(ctx, motivo="verificar estado")
    assert result.success
    assert "Captura de pantalla realizada" in result.content
    assert "inline_data" in result.metadata
    assert result.metadata["inline_data"]["mime_type"] == "image/jpeg"
    assert len(result.metadata["inline_data"]["data"]) > 0
