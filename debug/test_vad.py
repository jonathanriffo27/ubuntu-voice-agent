import pyaudio
import struct

# Configuración PyAudio
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_IN_RATE = 16000
CHUNK_SIZE = 512

p = pyaudio.PyAudio()
input_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_IN_RATE, input=True, frames_per_buffer=CHUNK_SIZE)

print("Grabando ambiente por 5 segundos para medir ruido de fondo...")
max_rms = 0
min_rms = 999999
avg_rms = 0
samples = 0

try:
    for _ in range(0, int(AUDIO_IN_RATE / CHUNK_SIZE * 5)):
        data = input_stream.read(CHUNK_SIZE, exception_on_overflow=False)
        shorts = struct.unpack('h' * (len(data) // 2), data)
        rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
        
        if rms > max_rms: max_rms = rms
        if rms < min_rms: min_rms = rms
        avg_rms += rms
        samples += 1

    avg_rms = avg_rms / samples
    print(f"\n--- RESULTADOS DEL VAD ---")
    print(f"Ruido Mínimo (Absoluto silencio): {min_rms:.2f}")
    print(f"Ruido Máximo (Picos): {max_rms:.2f}")
    print(f"Ruido Promedio: {avg_rms:.2f}")
    print(f"Umbral recomendado para silence_threshold: {avg_rms * 1.5:.0f}")

except Exception as e:
    print(f"Error: {e}")
finally:
    input_stream.stop_stream()
    input_stream.close()
    p.terminate()
