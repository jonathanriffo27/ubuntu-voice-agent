import io
import os
import glob
import time
import shutil
import hashlib
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from PIL import Image

from src.utils.logging import get_logger

logger = get_logger("vision.service")


@dataclass
class FrameMetadata:
    """Metadatos de fotograma capturado (inspirado en OpenClaw/CUA)."""
    frame_id: str
    timestamp: float
    original_size: Tuple[int, int]
    scaled_size: Tuple[int, int]
    scale_factor_x: float
    scale_factor_y: float
    unchanged: bool
    change_ratio: float


class ScreenCapture(ABC):
    @abstractmethod
    def capture_screen(self, max_dim: int = 1280, quality: int = 70) -> bytes:
        """Captura la pantalla completa y la devuelve optimizada en bytes JPEG."""
        pass

    @abstractmethod
    def capture_region(self, region: Tuple[int, int, int, int], quality: int = 70) -> bytes:
        """Captura una región específica: (left, top, width, height)."""
        pass

    def capture_screen_with_metadata(
        self, max_dim: int = 1280, quality: int = 70, diff_threshold: float = 0.015
    ) -> Tuple[bytes, Dict[str, Any]]:
        """Captura la pantalla retornando los bytes y metadatos del fotograma (idempotencia, factores de escala)."""
        data = self.capture_screen(max_dim, quality)
        return data, {
            "frame_id": "frame_generic",
            "timestamp": time.time(),
            "original_size": (1280, 720),
            "scaled_size": (1280, 720),
            "scale_factor_x": 1.0,
            "scale_factor_y": 1.0,
            "unchanged": False,
            "change_ratio": 1.0
        }

    def normalize_coordinates(self, x_scaled: int, y_scaled: int) -> Tuple[int, int]:
        """Convierte coordenadas de la imagen escalada a coordenadas físicas del monitor."""
        return x_scaled, y_scaled

    def denormalize_coordinates(self, x_real: int, y_real: int) -> Tuple[int, int]:
        """Convierte coordenadas físicas del monitor a coordenadas de la imagen escalada."""
        return x_real, y_real


class OptimizedScreenCaptureService(ScreenCapture):
    """
    Servicio de captura de pantalla híbrido de alto rendimiento.
    - En Wayland (GNOME / Ubuntu): Utiliza XDG Desktop Portal via D-Bus o grim.
    - En X11: Utiliza mss para captura directa en memoria.
    - Optimización automática: Redimensionado bilineal a max 1280px y compresión
      JPEG a calidad 70, reduciendo el peso de ~2MB a ~50-80KB (>95% ahorro)
      manteniendo total nitidez para textos, botones e interfaces.
    - Idempotencia de fotogramas (OpenClaw CUA pattern): Detección de "screen unchanged"
      y mapeo de coordenadas bidireccional entre viewport escalado y escritorio real.
    """

    def __init__(self, default_max_dim: int = 1280, default_quality: int = 70):
        self.default_max_dim = default_max_dim
        self.default_quality = default_quality
        self.is_wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        self._screenshot_dirs = [
            os.path.expanduser("~/Imágenes"),
            os.path.expanduser("~/Pictures"),
            "/tmp",
        ]
        # Estado de fotogramas para idempotencia y escala
        self._prev_frame_bytes: Optional[bytes] = None
        self._last_frame_bytes: Optional[bytes] = None
        self._last_frame_hash: Optional[str] = None
        self._last_raw_size: Tuple[int, int] = (1920, 1080)
        self._last_scaled_size: Tuple[int, int] = (1280, 720)
        self._last_scale_factors: Tuple[float, float] = (1.0, 1.0)
        self._last_metadata: Optional[Dict[str, Any]] = None

    def _calculate_frame_diff(self, prev_bytes: bytes, curr_bytes: bytes) -> float:
        """Calcula una diferencia perceptual ultrarrápida (0.0 a 1.0) entre dos fotogramas."""
        if prev_bytes == curr_bytes:
            return 0.0
        try:
            img1 = Image.open(io.BytesIO(prev_bytes)).convert("L").resize((32, 32))
            img2 = Image.open(io.BytesIO(curr_bytes)).convert("L").resize((32, 32))
            diffs = sum(abs(p1 - p2) for p1, p2 in zip(img1.tobytes(), img2.tobytes()))
            return diffs / (32 * 32 * 255)
        except Exception:
            return 1.0

    def _optimize_pil_image(self, img: Image.Image, max_dim: int, quality: int) -> bytes:
        """Redimensiona y comprime una imagen PIL para máxima velocidad de inferencia."""
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        orig_w, orig_h = img.size
        self._last_raw_size = (orig_w, orig_h)

        # Redimensionar manteniendo relación de aspecto
        w, h = img.size
        if max(w, h) > max_dim:
            ratio = min(max_dim / w, max_dim / h)
            new_size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
            img = img.resize(new_size, Image.Resampling.BILINEAR)
            self._last_scaled_size = new_size
        else:
            self._last_scaled_size = (w, h)

        self._last_scale_factors = (
            orig_w / float(self._last_scaled_size[0]),
            orig_h / float(self._last_scaled_size[1])
        )

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()

    def _capture_wayland_portal(self) -> Optional[Image.Image]:
        """Captura la pantalla en Wayland usando XDG Desktop Portal."""
        # Registrar tiempo antes de la llamada
        t_before = time.time() - 0.5

        proc = subprocess.run([
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.portal.Desktop",
            "--object-path", "/org/freedesktop/portal/desktop",
            "--method", "org.freedesktop.portal.Screenshot.Screenshot",
            "", "{'interactive': <false>}"
        ], capture_output=True, text=True, timeout=5)

        if proc.returncode != 0:
            logger.warning(f"gdbus portal screenshot falló: {proc.stderr}")
            return None

        # Esperar a que GNOME escriba el archivo (primera sonda temprana)
        time.sleep(0.15)

        # Buscar el screenshot más reciente generado después de t_before
        candidates = []
        for sdir in self._screenshot_dirs:
            if not os.path.isdir(sdir):
                continue
            for pattern in ["Screenshot*.png", "Captura*.png", "*.png"]:
                for fpath in glob.glob(os.path.join(sdir, pattern)):
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime >= t_before:
                            candidates.append((mtime, fpath))
                    except OSError:
                        pass

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            latest_path = candidates[0][1]
            
            # GNOME puede tardar >1s en materializar el PNG; ventana total ~3s
            # (12 reintentos x 0.25s). Verificado en vivo 2026-09-14: con solo
            # 4x0.15s la lectura fallaba y se caía a una captura negra de mss.
            for _ in range(12):
                try:
                    if os.path.exists(latest_path) and os.path.getsize(latest_path) > 1000:
                        img = Image.open(latest_path)
                        img.load()  # Cargar en memoria antes de cerrar
                        return img
                except Exception:
                    pass
                time.sleep(0.25)

            logger.error(f"No se pudo leer la captura de portal en {latest_path} tras reintentos.")

        return None

    def _capture_grim(self) -> Optional[Image.Image]:
        """Captura en Wayland usando la utilidad grim si está instalada."""
        if not shutil.which("grim"):
            return None
        try:
            proc = subprocess.run(["grim", "-"], capture_output=True, timeout=3)
            if proc.returncode == 0 and proc.stdout:
                img = Image.open(io.BytesIO(proc.stdout))
                img.load()
                return img
        except Exception:
            pass
        return None

    def _capture_mss(self, monitor_index: int = 1) -> Optional[Image.Image]:
        """Captura en X11 usando mss."""
        try:
            import mss
            with mss.mss() as sct:
                if monitor_index > len(sct.monitors) - 1:
                    monitor_index = 1
                sct_img = sct.grab(sct.monitors[monitor_index])
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                return img
        except Exception as e:
            logger.debug(f"mss capture falló: {e}")
            return None

    def capture_screen(self, max_dim: Optional[int] = None, quality: Optional[int] = None) -> bytes:
        """Captura la pantalla actual con optimización de latencia y tamaño."""
        max_dim = max_dim or self.default_max_dim
        quality = quality or self.default_quality

        img = None

        # Si estamos en Wayland, priorizar portal o grim
        if self.is_wayland:
            img = self._capture_grim() or self._capture_wayland_portal()

        # Si falló o estamos en X11, usar mss
        if img is None:
            img = self._capture_mss()

        # Fallback de último recurso: portal gdbus
        if img is None and not self.is_wayland:
            img = self._capture_wayland_portal()

        if img is None:
            raise RuntimeError("No se pudo capturar la pantalla con ninguno de los métodos disponibles (Portal, Grim, MSS).")

        data = self._optimize_pil_image(img, max_dim, quality)
        self._last_frame_bytes = data
        return data

    def capture_screen_with_metadata(
        self,
        max_dim: Optional[int] = None,
        quality: Optional[int] = None,
        diff_threshold: float = 0.015
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Captura la pantalla y calcula metadatos de fotograma (OpenClaw CUA pattern):
        - frame_id: Identificador SHA-256 único del fotograma.
        - unchanged: Booleano True si la pantalla no cambió significativamente respecto al anterior.
        - change_ratio: Proporción de píxeles cambiados (0.0 a 1.0).
        - scale_factor_x / scale_factor_y: Factores para normalizar coordenadas hacia el monitor real.
        """
        prev_bytes = self._last_frame_bytes
        curr_bytes = self.capture_screen(max_dim=max_dim, quality=quality)

        h = hashlib.sha256(curr_bytes).hexdigest()[:16]
        frame_id = f"frame_{h}"

        if prev_bytes is not None:
            diff = self._calculate_frame_diff(prev_bytes, curr_bytes)
        else:
            diff = 1.0

        unchanged = (diff <= diff_threshold)
        
        meta = {
            "frame_id": frame_id,
            "timestamp": time.time(),
            "original_size": self._last_raw_size,
            "scaled_size": self._last_scaled_size,
            "scale_factor_x": self._last_scale_factors[0],
            "scale_factor_y": self._last_scale_factors[1],
            "unchanged": unchanged,
            "change_ratio": round(diff, 4),
        }
        self._last_frame_hash = frame_id
        self._last_metadata = meta
        self._last_frame_bytes = curr_bytes
        return curr_bytes, meta

    def normalize_coordinates(self, x_scaled: int, y_scaled: int) -> Tuple[int, int]:
        """
        Mapea coordenadas de la imagen comprimida/escalada (ej. 1280px)
        a coordenadas físicas absolutas del monitor (ej. 1920x1080).
        """
        fx, fy = self._last_scale_factors
        return int(round(x_scaled * fx)), int(round(y_scaled * fy))

    def denormalize_coordinates(self, x_real: int, y_real: int) -> Tuple[int, int]:
        """
        Mapea coordenadas físicas del monitor a coordenadas de la imagen escalada.
        """
        fx, fy = self._last_scale_factors
        fx = fx if fx != 0 else 1.0
        fy = fy if fy != 0 else 1.0
        return int(round(x_real / fx)), int(round(y_real / fy))

    def capture_region(self, region: Tuple[int, int, int, int], quality: Optional[int] = None) -> bytes:
        """Captura una región específica: (left, top, width, height)."""
        quality = quality or self.default_quality
        # Para recorte, capturar la pantalla completa y recortar en memoria con PIL
        full_img = None
        if self.is_wayland:
            full_img = self._capture_grim() or self._capture_wayland_portal()
        if full_img is None:
            full_img = self._capture_mss()

        if full_img is None:
            raise RuntimeError("No se pudo capturar la pantalla para recortar región.")

        left, top, width, height = region
        cropped = full_img.crop((left, top, left + width, top + height))
        return self._optimize_pil_image(cropped, max_dim=max(width, height), quality=quality)


# Alias de compatibilidad
MssScreenService = OptimizedScreenCaptureService
