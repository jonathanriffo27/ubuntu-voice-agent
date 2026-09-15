"""Detector de Wake Word local basado en openWakeWord con buffer acumulador exacto."""
import os
import warnings
from typing import Optional, Tuple
from src.utils.logging import get_logger

logger = get_logger("voice.wake_word")

# Mapeo de nombres cortos a identificadores del modelo pre-entrenado
_BUILTIN_MODELS = {
    "hey_jarvis": "hey_jarvis",
    "jarvis": "hey_jarvis",
    "alexa": "alexa",
    "hey_mycroft": "hey_mycroft",
    "mycroft": "hey_mycroft",
}


class WakeWordDetector:
    """
    Detector de Wake Word local usando openWakeWord (ONNX, CPU).
    
    Incorpora un buffer acumulador exacto para asegurar que la red neuronal
    reciba siempre múltiplos exactos de 1280 muestras (80ms), evitando la
    pérdida de muestras (descarte de 256 samples) que ocurre con chunks de 512.
    """

    def __init__(self, chunk_samples: int = 1280):
        self._model = None
        self._np = None
        self._model_name: str = ""
        self._target_samples = chunk_samples
        self._target_bytes = chunk_samples * 2  # 16-bit PCM (2 bytes por muestra)
        self._buffer = bytearray()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load_sync(self, wake_word: str = "hey_jarvis") -> None:
        """Carga el modelo de forma síncrona (ejecutar en thread worker)."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                import openwakeword
                from openwakeword.model import Model
                import numpy as np
                self._np = np

                # Resolver ruta del modelo
                if os.path.isfile(wake_word) and wake_word.endswith(".onnx"):
                    model_paths = [wake_word]
                    self._model_name = os.path.basename(wake_word).replace(".onnx", "")
                else:
                    key = wake_word.lower().replace("-", "_")
                    suffix = _BUILTIN_MODELS.get(key, key)
                    all_paths = openwakeword.get_pretrained_model_paths()
                    model_paths = [p for p in all_paths if suffix in p]
                    self._model_name = key

                if not model_paths:
                    logger.error(f"No se encontró modelo para wake word '{wake_word}'. Modelos disponibles: {list(_BUILTIN_MODELS.keys())}")
                    return

                self._model = Model(wakeword_model_paths=model_paths)
                logger.info(f"Modelo wake word cargado: {self._model_name} ({os.path.basename(model_paths[0])})")
        except ImportError:
            logger.error("openwakeword o numpy no están instalados.")
        except Exception as e:
            logger.error(f"Error cargando modelo wake word: {e}")

    def predict(self, pcm_bytes: bytes, threshold: float = 0.35) -> Tuple[bool, str, float]:
        """
        Acumula audio y evalúa en ventanas exactas de 1280 muestras (80ms).

        Returns:
            Tuple (detected: bool, model_name: str, confidence: float)
        """
        if not self._model or self._np is None:
            return False, "", 0.0

        self._buffer.extend(pcm_bytes)
        if len(self._buffer) < self._target_bytes:
            return False, self._model_name, 0.0

        detected = False
        best_name = self._model_name
        best_score = 0.0

        # Procesar todos los bloques completos de 1280 muestras disponibles
        while len(self._buffer) >= self._target_bytes:
            chunk_bytes = bytes(self._buffer[:self._target_bytes])
            self._buffer = self._buffer[self._target_bytes:]

            audio_np = self._np.frombuffer(chunk_bytes, dtype=self._np.int16)
            prediction = self._model.predict(audio_np)

            if prediction:
                name = max(prediction, key=prediction.get)
                score = float(prediction[name])
                if score > best_score:
                    best_score = score
                    best_name = self._model_name or name
                if score > threshold:
                    detected = True

        if best_score >= 0.15:
            logger.debug(f"[WakeWord] Probabilidad: {best_score:.3f} (umbral: {threshold:.2f})")

        return detected, best_name, best_score

    def reset(self) -> None:
        """Vacía el buffer interno de audio y el buffer de predicción tras una activación."""
        self._buffer.clear()
        if self._model:
            try:
                self._model.reset()
            except Exception as e:
                logger.debug(f"Error reseteando modelo wake word: {e}")
