import math
import struct
import pytest
from src.voice.vad import VoiceActivityDetector


def test_vad_silence_detection():
    vad = VoiceActivityDetector(sample_rate=16000)
    # Audio silencioso (todos ceros)
    silent_chunk = struct.pack('h' * 512, *([0] * 512))
    assert vad.is_speech(silent_chunk) is False


def test_vad_voice_detection():
    vad = VoiceActivityDetector(sample_rate=16000)
    # Generar una onda senoidal pura a 300Hz (frecuencia típica de voz humana con amplitud media)
    samples = [int(4000 * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(512)]
    voice_chunk = struct.pack('h' * 512, *samples)

    assert vad.is_speech(voice_chunk, current_threshold=1000.0) is True


def test_vad_impulsive_click_rejection():
    vad = VoiceActivityDetector(sample_rate=16000)
    # Clic impulsivo: un solo pico de muy alta amplitud seguido de ceros (clic mecánico/tecla)
    samples = [0] * 512
    samples[10] = 30000
    click_chunk = struct.pack('h' * 512, *samples)

    # El VAD debe rechazar el clic por crest factor anómalo (> 9.5)
    assert vad.is_speech(click_chunk, current_threshold=500.0) is False


@pytest.mark.asyncio
async def test_audio_recorder_calibration():
    import pytest
    from unittest.mock import MagicMock
    from src.voice.recorder import AudioRecorder
    from src.events.bus import EventBus
    from src.events.base import ConversationContext

    mock_stream = MagicMock()
    # Generar muestras de audio de ruido ambiente bajo (~800 RMS)
    samples = [int(800 * math.sin(2 * math.pi * 100 * i / 16000)) for i in range(512)]
    mock_data = struct.pack('h' * 512, *samples)
    mock_stream.read.return_value = mock_data

    recorder = AudioRecorder(mock_stream, EventBus(), ConversationContext())
    threshold = await recorder.calibrate(max_drain_frames=4, sample_frames=6)

    assert threshold >= 1200
    assert threshold <= 3500
    assert recorder.silence_threshold == threshold

