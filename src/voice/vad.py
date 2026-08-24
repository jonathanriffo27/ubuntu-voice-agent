import struct
import math
import numpy as np
from typing import Optional
from src.utils.logging import get_logger

logger = get_logger("voice.vad")


class VoiceActivityDetector:
    """
    Detector de actividad de voz (VAD) inteligente.
    Combina análisis de energía adaptativa, tasa de cruces por cero (ZCR)
    y distribución espectral para distinguir voz humana real de ruidos mecánicos
    (teclado, respiración, ruidos estáticos de fondo).
    """

    def __init__(self, sample_rate: int = 16000, sensitivity: float = 0.5):
        self.sample_rate = sample_rate
        self.sensitivity = sensitivity  # 0.0 (menos sensible) a 1.0 (muy sensible)
        self._noise_energy = 500.0
        self._speech_energy = 3000.0
        self._alpha = 0.95  # Factor de suavizado para estimación de ruido

    def calculate_features(self, pcm_bytes: bytes) -> tuple[float, float, float]:
        """Calcula energía RMS, tasa de cruce por cero (ZCR) y rango dinámico."""
        if not pcm_bytes:
            return 0.0, 0.0, 0.0

        shorts = struct.unpack('h' * (len(pcm_bytes) // 2), pcm_bytes)
        if not shorts:
            return 0.0, 0.0, 0.0

        n = len(shorts)
        energy = math.sqrt(sum(s * s for s in shorts) / n)

        # Zero Crossing Rate (ZCR)
        zero_crossings = sum(1 for i in range(1, n) if (shorts[i] >= 0 and shorts[i - 1] < 0) or (shorts[i] < 0 and shorts[i - 1] >= 0))
        zcr = zero_crossings / float(n)

        # Rango pico a promedio (crest factor)
        peak = max(abs(s) for s in shorts)
        crest = peak / (energy + 1e-5)

        return energy, zcr, crest

    def is_speech(self, pcm_bytes: bytes, current_threshold: Optional[float] = None) -> bool:
        """
        Determina si un fragmento PCM contiene habla humana activa.
        Rechaza clics bruscos (crest factor excesivo) y ruidos estáticos/soplidos (ZCR anómalo).
        """
        energy, zcr, crest = self.calculate_features(pcm_bytes)

        threshold = current_threshold if current_threshold is not None else self._noise_energy * 2.5
        threshold = max(600.0, threshold)

        # La voz humana típica a 16kHz tiene ZCR entre 0.02 y 0.35
        # Teclado / clics suelen tener crest factor muy alto (> 8.0) y duración ultracorta
        is_energy_above = energy > threshold
        is_voice_zcr = 0.015 <= zcr <= 0.40
        is_not_impulsive_click = crest < 9.5

        if is_energy_above and is_voice_zcr and is_not_impulsive_click:
            # Adaptar energía de habla
            self._speech_energy = self._alpha * self._speech_energy + (1 - self._alpha) * energy
            return True
        else:
            # Adaptar piso de ruido si el ambiente está en silencio
            if energy < threshold:
                self._noise_energy = self._alpha * self._noise_energy + (1 - self._alpha) * energy
            return False
