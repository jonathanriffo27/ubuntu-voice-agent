from abc import ABC, abstractmethod
from typing import Optional, Tuple
import io

class ScreenCapture(ABC):
    @abstractmethod
    def capture_screen(self, monitor_index: int = 1) -> bytes:
        pass
        
    @abstractmethod
    def capture_region(self, region: Tuple[int, int, int, int]) -> bytes:
        pass

class MssScreenService(ScreenCapture):
    def __init__(self):
        try:
            import mss
            from PIL import Image
        except ImportError:
            raise ImportError("mss y Pillow son requeridos para MssScreenService. Instálalos con 'pip install mss Pillow'.")
            
        self.mss = mss

    def _process_image(self, sct_img) -> bytes:
        from PIL import Image
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        
        # Opcional: redimensionar si es muy grande para ahorrar tokens
        max_size = (1920, 1080)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=80)
        return buffer.getvalue()

    def capture_screen(self, monitor_index: int = 1) -> bytes:
        """Captura un monitor específico (1-indexado)."""
        with self.mss.mss() as sct:
            if monitor_index > len(sct.monitors) - 1:
                monitor_index = 1
            sct_img = sct.grab(sct.monitors[monitor_index])
            return self._process_image(sct_img)

    def capture_region(self, region: Tuple[int, int, int, int]) -> bytes:
        """Captura una región específica: (left, top, width, height)."""
        with self.mss.mss() as sct:
            sct_img = sct.grab(region)
            return self._process_image(sct_img)
