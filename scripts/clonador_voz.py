#!/usr/bin/env python3
"""
🎙️ CLONADOR DE VOZ RÁPIDO (Atlas Voice Lab)
===========================================
Permite a tu amigo clonar su voz en segundos y escucharla hablar en español.

Modos de uso:
  1. Modo Asistente Terminal:  ./venv/bin/python scripts/clonador_voz.py --cli
  2. Modo Web Interactivo:     ./venv/bin/python scripts/clonador_voz.py --web
"""

import os
import sys
import time
import wave
import struct
import asyncio
import subprocess
import argparse
from pathlib import Path
import httpx
import pyaudio

AUDIO_DIR = Path(__file__).parent / "audio_samples"
AUDIO_DIR.mkdir(exist_ok=True)


def grabar_microfono(duracion_segundos: int = 10, archivo_salida: Path = AUDIO_DIR / "mi_voz.wav"):
    """Graba directamente del micrófono local usando PyAudio."""
    print(f"\n🎙️  Preparando grabación ({duracion_segundos} segundos)...")
    print("👉 Pídele a tu amigo que lea un texto natural en voz alta (ej: una anécdota o una noticia).")
    for i in range(3, 0, -1):
        print(f"   Iniciando en {i}...", end="\r", flush=True)
        time.sleep(1)
    print("\n🔴 ¡GRABANDO AHORA! (Habla con voz clara y natural)...")

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
    frames = []

    total_chunks = int(16000 / 1024 * duracion_segundos)
    for i in range(total_chunks):
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)
        # Barra de progreso visual
        pct = int((i + 1) / total_chunks * 30)
        bar = "█" * pct + "░" * (30 - pct)
        print(f"\r   [{bar}] {int((i+1)/total_chunks * duracion_segundos)}s / {duracion_segundos}s", end="", flush=True)

    stream.stop_stream()
    stream.close()
    p.terminate()

    print(f"\n✅ Grabación finalizada. Guardando en {archivo_salida.name}...")
    with wave.open(str(archivo_salida), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(frames))

    return archivo_salida


async def clonar_y_sintetizar_elevenlabs(
    archivo_audio: Path,
    texto_a_decir: str,
    api_key: str,
    archivo_resultado: Path = AUDIO_DIR / "resultado_clonado.mp3"
) -> Path:
    """Clona la voz instantáneamente usando ElevenLabs Voice Cloning API."""
    headers = {"xi-api-key": api_key}
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Crear voz clonada instantánea
        print("\n⏳ 1/3 Subiendo muestra de audio y analizando timbre vocal...")
        with open(archivo_audio, "rb") as f:
            files = {"files": (archivo_audio.name, f, "audio/wav")}
            data = {"name": f"Clon_Amigo_{int(time.time())}", "description": "Voz clonada de prueba"}
            resp = await client.post("https://api.elevenlabs.io/v1/voices/add", headers=headers, data=data, files=files)

        if resp.status_code != 200:
            raise RuntimeError(f"Error creando clon de voz en ElevenLabs ({resp.status_code}): {resp.text}")

        voice_id = resp.json().get("voice_id")
        print(f"⚡ Voz clonada creada exitosamente (ID: {voice_id}).")

        try:
            # 2. Generar audio con la voz clonada
            print(f"⏳ 2/3 Sintetizando texto: '{texto_a_decir}'...")
            payload = {
                "text": texto_a_decir,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.50, "similarity_boost": 0.85}
            }
            tts_resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={**headers, "Content-Type": "application/json"},
                json=payload
            )

            if tts_resp.status_code != 200:
                raise RuntimeError(f"Error generando síntesis ({tts_resp.status_code}): {tts_resp.text}")

            with open(archivo_resultado, "wb") as f:
                f.write(tts_resp.content)

            print(f"✅ 3/3 Audio clonado generado y guardado en: {archivo_resultado.resolve()}")
            return archivo_resultado

        finally:
            # Limpiar la voz temporal si se desea
            pass


def reproducir_audio(archivo_audio: Path):
    """Reproduce el archivo de audio por los parlantes del sistema."""
    print(f"\n🔊 Reproduciendo voz clonada...")
    for cmd in [["pw-play", str(archivo_audio)], ["paplay", str(archivo_audio)], ["ffplay", "-nodisp", "-autoexit", str(archivo_audio)]]:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue
    print("ℹ️  Puedes reproducir el archivo directamente con cualquier reproductor.")


async def modo_cli():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║ ⚡ ATLAS VOICE LAB: CLONADOR RÁPIDO DE VOZ                   ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("\n🔑 Introduce tu API Key de ElevenLabs (obtenible gratis en elevenlabs.io):")
        api_key = input("   API Key: ").strip()
        if not api_key:
            print("❌ Se requiere una API Key de ElevenLabs para la prueba rápida.")
            return

    print("\n¿Cómo quieres proporcionar la voz de tu amigo?")
    print("  [1] Grabar 10 segundos desde el micrófono ahora mismo")
    print("  [2] Usar un archivo de audio existente (.wav, .mp3 o nota de voz)")
    opcion = input("\nSelecciona opción [1/2] (por defecto 1): ").strip() or "1"

    if opcion == "1":
        archivo_muestra = grabar_microfono(duracion_segundos=10)
    else:
        ruta = input("Ingresa la ruta completa del archivo de audio: ").strip()
        archivo_muestra = Path(ruta)
        if not archivo_muestra.exists():
            print(f"❌ El archivo {ruta} no existe.")
            return

    print("\n✍️  ¿Qué frase quieres que diga su voz clonada?")
    texto = input("Texto (ej: 'Hola Jonathan, ¿cómo estás? Soy tu clon digital'): ").strip()
    if not texto:
        texto = "Hola Jonathan, esta es mi voz clonada con inteligencia artificial. Suena increíblemente idéntica."

    try:
        audio_generado = await clonar_y_sintetizar_elevenlabs(archivo_muestra, texto, api_key)
        reproducir_audio(audio_generado)
        print(f"\n🎉 ¡Prueba completada con éxito! Archivo guardado en: {audio_generado}")
    except Exception as e:
        print(f"\n❌ Error durante la clonación: {e}")


async def modo_web(port: int = 7895):
    from aiohttp import web
    import mimetypes

    print(f"\n🌐 Iniciando interfaz web interactiva en: http://localhost:{port}")

    HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>⚡ Atlas Voice Lab - Clonador de Voz</title>
    <style>
        * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; display: flex; justify-content: center; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 2rem; max-width: 600px; width: 100%; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        h1 { color: #38bdf8; font-size: 1.5rem; margin-top: 0; }
        label { display: block; margin: 1rem 0 0.5rem; font-weight: bold; color: #94a3b8; }
        input[type="text"], input[type="password"], textarea { width: 100%; padding: 0.75rem; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 1rem; }
        input[type="file"] { margin-top: 0.5rem; }
        button { background: #0284c7; color: #fff; border: none; padding: 0.85rem 1.5rem; border-radius: 6px; font-size: 1rem; font-weight: bold; cursor: pointer; width: 100%; margin-top: 1.5rem; transition: background 0.2s; }
        button:hover { background: #0369a1; }
        .status { margin-top: 1rem; padding: 1rem; border-radius: 6px; background: #0f172a; font-family: monospace; display: none; }
        audio { width: 100%; margin-top: 1rem; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🎙️ Atlas Voice Lab: Clonador de Voz</h1>
        <p style="color:#94a3b8; font-size:0.9rem;">Sube un audio de 10-30s de tu amigo o grábalo, escribe un texto y escucha su clon de voz en segundos.</p>
        
        <form id="cloneForm">
            <label>1. API Key de ElevenLabs:</label>
            <input type="password" id="apiKey" placeholder="xi-api-key..." required />
            
            <label>2. Archivo de voz de muestra (.wav, .mp3, nota de voz):</label>
            <input type="file" id="audioFile" accept="audio/*" required />
            
            <label>3. Texto que dirá su voz clonada:</label>
            <textarea id="ttsText" rows="3" required>¡Hola! Esta es mi voz clonada en tiempo real. Increíble cómo suena.</textarea>
            
            <button type="submit" id="submitBtn">⚡ ¡Clonar Voz y Generar Audio!</button>
        </form>
        
        <div id="status" class="status"></div>
        <div id="audioPlayerContainer"></div>
    </div>

    <script>
        document.getElementById('cloneForm').onsubmit = async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const status = document.getElementById('status');
            const container = document.getElementById('audioPlayerContainer');
            
            btn.disabled = true;
            btn.innerText = "⏳ Clonando y generando audio...";
            status.style.display = "block";
            status.innerText = "Subiendo muestra a ElevenLabs...";
            container.innerHTML = "";
            
            const formData = new FormData();
            formData.append('apiKey', document.getElementById('apiKey').value);
            formData.append('audioFile', document.getElementById('audioFile').files[0]);
            formData.append('text', document.getElementById('ttsText').value);
            
            try {
                const res = await fetch('/api/clone', { method: 'POST', body: formData });
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.error || 'Error desconocido');
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                status.innerText = "✅ ¡Voz clonada generada con éxito!";
                container.innerHTML = `<audio controls autoplay src="${url}"></audio><p><a href="${url}" download="voz_clonada.mp3" style="color:#38bdf8;">⬇️ Descargar archivo MP3</a></p>`;
            } catch (err) {
                status.innerText = "❌ Error: " + err.message;
            } finally {
                btn.disabled = false;
                btn.innerText = "⚡ ¡Clonar Voz y Generar Audio!";
            }
        };
    </script>
</body>
</html>
"""

    async def handle_index(request):
        return web.Response(text=HTML_PAGE, content_type="text/html")

    async def handle_clone(request):
        reader = await request.multipart()
        api_key = None
        text = None
        file_bytes = None
        file_name = "audio.wav"

        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name == "apiKey":
                api_key = (await field.read()).decode("utf-8").strip()
            elif field.name == "text":
                text = (await field.read()).decode("utf-8").strip()
            elif field.name == "audioFile":
                file_name = field.filename or "sample.wav"
                file_bytes = await field.read()

        if not api_key or not text or not file_bytes:
            return web.json_response({"error": "Faltan parámetros requeridos."}, status=400)

        temp_input = AUDIO_DIR / f"upload_{int(time.time())}_{file_name}"
        with open(temp_input, "wb") as f:
            f.write(file_bytes)

        try:
            res_audio = await clonar_y_sintetizar_elevenlabs(temp_input, text, api_key)
            with open(res_audio, "rb") as f:
                audio_content = f.read()
            return web.Response(body=audio_content, content_type="audio/mpeg")
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/clone", handle_clone)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🚀 Servidor listo. Abre tu navegador en http://localhost:{port}")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clonador rápido de voz")
    parser.add_argument("--web", action="store_true", help="Iniciar servidor web interactivo")
    parser.add_argument("--cli", action="store_true", help="Iniciar asistente interactivo en terminal")
    parser.add_argument("--port", type=int, default=7895, help="Puerto para la interfaz web (default: 7895)")
    args = parser.parse_args()

    if args.web:
        asyncio.run(modo_web(args.port))
    else:
        asyncio.run(modo_cli())
