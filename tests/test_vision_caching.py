import io
import pytest
from PIL import Image
from src.vision.service import OptimizedScreenCaptureService, FrameMetadata


def test_frame_metadata_dataclass():
    meta = FrameMetadata(
        frame_id="frame_123",
        timestamp=100.0,
        original_size=(1920, 1080),
        scaled_size=(1280, 720),
        scale_factor_x=1.5,
        scale_factor_y=1.5,
        unchanged=False,
        change_ratio=0.85
    )
    assert meta.frame_id == "frame_123"
    assert meta.scale_factor_x == 1.5
    assert not meta.unchanged


def test_screen_caching_and_idempotence():
    service = OptimizedScreenCaptureService(default_max_dim=1280, default_quality=70)
    
    # Simular una imagen fija (ej. fondo rojo)
    img1 = Image.new("RGB", (1920, 1080), color=(255, 0, 0))
    buf1 = io.BytesIO()
    img1.save(buf1, format="JPEG")
    bytes1 = buf1.getvalue()
    
    # Mockear captura interna
    service._optimize_pil_image(img1, max_dim=1280, quality=70)
    service.capture_screen = lambda max_dim=None, quality=None: bytes1
    
    # 1. Primera captura -> Debe reportar unchanged=False ya que no había frame previo
    data1, meta1 = service.capture_screen_with_metadata()
    assert meta1["frame_id"].startswith("frame_")
    assert meta1["unchanged"] is False
    assert meta1["change_ratio"] == 1.0
    assert meta1["original_size"] == (1920, 1080)
    assert meta1["scaled_size"] == (1280, 720)
    assert round(meta1["scale_factor_x"], 2) == 1.50
    assert round(meta1["scale_factor_y"], 2) == 1.50

    # 2. Segunda captura idéntica -> Debe reportar unchanged=True (idempotente)
    data2, meta2 = service.capture_screen_with_metadata()
    assert meta2["unchanged"] is True
    assert meta2["change_ratio"] <= 0.01
    assert meta2["frame_id"] == meta1["frame_id"]

    # 3. Tercera captura con cambio sustancial (fondo blanco)
    img3 = Image.new("RGB", (1920, 1080), color=(255, 255, 255))
    buf3 = io.BytesIO()
    img3.save(buf3, format="JPEG")
    bytes3 = buf3.getvalue()
    service.capture_screen = lambda max_dim=None, quality=None: bytes3

    data3, meta3 = service.capture_screen_with_metadata()
    assert meta3["unchanged"] is False
    assert meta3["change_ratio"] > 0.05
    assert meta3["frame_id"] != meta1["frame_id"]


def test_coordinate_normalization():
    service = OptimizedScreenCaptureService()
    # Simular pantalla 1920x1080 escalada a 1280x720 (factor 1.5)
    img = Image.new("RGB", (1920, 1080))
    service._optimize_pil_image(img, max_dim=1280, quality=70)

    # Coordenada en centro de la imagen escalada (640, 360) -> debe ser (960, 540) en el monitor
    x_real, y_real = service.normalize_coordinates(640, 360)
    assert x_real == 960
    assert y_real == 540

    # Desnormalización inversa: (960, 540) -> (640, 360)
    x_scaled, y_scaled = service.denormalize_coordinates(960, 540)
    assert x_scaled == 640
    assert y_scaled == 360
