import sys
import pyaudio
import numpy as np
import openwakeword
from openwakeword.model import Model

print("=" * 68)
print("  🎙️  CALIBRADOR EN VIVO: WAKE WORD (Hey Jarvis & Alexa)")
print("=" * 68)

model_paths = [p for p in openwakeword.get_pretrained_model_paths() if 'hey_jarvis' in p or 'alexa' in p]
m = Model(wakeword_model_paths=model_paths)

p = pyaudio.PyAudio()
stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=512)

buffer = bytearray()
target_bytes = 1280 * 2

print("\n[LISTO] Habla frente al micrófono diciendo:")
print("  1. 'Hey Jarvis'")
print("  2. 'Alexa'")
print("Umbral de activación: 0.30 | Presiona Ctrl+C para salir.\n")

peak_jarvis = 0.0
peak_alexa = 0.0
last_activation = 0

try:
    while True:
        data = stream.read(512, exception_on_overflow=False)
        buffer.extend(data)

        while len(buffer) >= target_bytes:
            chunk = np.frombuffer(buffer[:target_bytes], dtype=np.int16)
            buffer = buffer[target_bytes:]

            pred = m.predict(chunk)
            score_j = float(pred.get('hey_jarvis_v0.1', 0.0))
            score_a = float(pred.get('alexa_v0.1', 0.0))

            peak_jarvis = max(peak_jarvis, score_j)
            peak_alexa = max(peak_alexa, score_a)

            # Si detecta activación con >= 0.30
            if score_j >= 0.30:
                print(f"\r🔔 ¡ACTIVADO POR 'HEY JARVIS'! (Puntaje: {score_j:.3f} / Pico: {peak_jarvis:.3f})\033[K")
                m.reset()
                peak_jarvis = 0.0
            elif score_a >= 0.30:
                print(f"\r🔔 ¡ACTIVADO POR 'ALEXA'! (Puntaje: {score_a:.3f} / Pico: {peak_alexa:.3f})\033[K")
                m.reset()
                peak_alexa = 0.0
            else:
                # Mostrar barra en tiempo real del modelo con mayor score actual
                lead_name = "Jarvis" if score_j >= score_a else "Alexa"
                lead_score = max(score_j, score_a)
                bar_len = min(25, int(lead_score * 40))
                bar = '█' * bar_len + '░' * (25 - bar_len)
                sys.stdout.write(f"\r[{lead_name:6}] Score: {lead_score:.3f} |{bar}| (Pico J:{peak_jarvis:.2f} A:{peak_alexa:.2f})\033[K")
                sys.stdout.flush()

except KeyboardInterrupt:
    print("\n\nCalibración finalizada.")
finally:
    stream.stop_stream()
    stream.close()
    p.terminate()
