#!/usr/bin/env python3
"""
🎙️ CLONADOR DE VOZ RÁPIDO (Atlas Voice Lab)
===========================================
Opciones gratuitas y premium para clonar voces en español.

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


def grabar_microfono(duracion_segundos: int = 10, archivo_salida: Path = AUDIO_DIR / "mi_voz.wav") -> Path:
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
    """Clona la voz usando ElevenLabs Voice Cloning API."""
    headers = {"xi-api-key": api_key}
    async with httpx.AsyncClient(timeout=60.0) as client:
        print("\n⏳ 1/3 Subiendo muestra de audio a ElevenLabs...")
        with open(archivo_audio, "rb") as f:
            files = {"files": (archivo_audio.name, f, "audio/wav")}
            data = {"name": f"Clon_{int(time.time())}", "description": "Voz clonada"}
            resp = await client.post("https://api.elevenlabs.io/v1/voices/add", headers=headers, data=data, files=files)

        if resp.status_code != 200:
            raise RuntimeError(f"Error en ElevenLabs ({resp.status_code}): {resp.text}")

        voice_id = resp.json().get("voice_id")
        print(f"⚡ Voz clonada creada (ID: {voice_id}).")

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
            raise RuntimeError(f"Error generando audio ({tts_resp.status_code}): {tts_resp.text}")

        with open(archivo_resultado, "wb") as f:
            f.write(tts_resp.content)

        print(f"✅ 3/3 Audio clonado generado en: {archivo_resultado.resolve()}")
        return archivo_resultado


async def clonar_y_sintetizar_fishaudio(
    archivo_audio: Path,
    texto_a_decir: str,
    api_key: str,
    archivo_resultado: Path = AUDIO_DIR / "resultado_fishaudio.mp3"
) -> Path:
    """Clona la voz usando Fish Audio API (Tier gratuito disponible)."""
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        print("\n⏳ Subiendo muestra a Fish Audio...")
        with open(archivo_audio, "rb") as f:
            audio_bytes = f.read()

        payload = {
            "text": texto_a_decir,
            "reference_audio": list(audio_bytes),
            "format": "mp3"
        }
        resp = await client.post("https://api.fish.audio/v1/tts", headers=headers, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Error en Fish Audio ({resp.status_code}): {resp.text}")

        with open(archivo_resultado, "wb") as f:
            f.write(resp.content)
        return archivo_resultado


def reproducir_audio(archivo_audio: Path):
    """Reproduce el archivo de audio por los parlantes del sistema."""
    print(f"\n🔊 Reproduciendo voz generada...")
    for cmd in [["pw-play", str(archivo_audio)], ["paplay", str(archivo_audio)], ["ffplay", "-nodisp", "-autoexit", str(archivo_audio)]]:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue
    print(f"ℹ️  Archivo listo en: {archivo_audio}")


async def modo_cli():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║ ⚡ ATLAS VOICE LAB: CLONADOR RÁPIDO DE VOZ                   ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print("\nOpciones de clonación disponibles:")
    print("  [1] 🌟 ElevenLabs (Clonación de máxima calidad)")
    print("  [2] 🐟 Fish Audio (Alternativa gratuita / Freemium)")
    print("  [3] 🆓 Ver las mejores plataformas 100% GRATIS online sin registro")

    op_motor = input("\nElige una opción [1/2/3] (default: 1): ").strip() or "1"

    if op_motor == "3":
        print("\n🌐 MEJORES HERRAMIENTAS 100% GRATUITAS PARA CLONAR VOZ EN ESPAÑOL:")
        print("  1. F5-TTS Web (HuggingFace ZeroGPU): https://huggingface.co/spaces/mrfakename/E2-F5-TTS")
        print("  2. MyShell Voice Lab (Gratis):        https://app.myshell.ai")
        print("  3. Fish Audio Web (Free Credits):     https://fish.audio")
        print("  4. TTSMaker Voice Clone:              https://ttsmaker.com")
        print("\n💡 Puedes grabar el audio de tu amigo con este script y subirlo a cualquiera de esas webs.")
        input("\nPresiona [Enter] para volver o grabar un audio de muestra...")

    print("\n¿Cómo quieres proporcionar la voz de tu amigo?")
    print("  [1] Grabar 10 segundos desde el micrófono ahora mismo")
    print("  [2] Usar un archivo de audio existente (.wav, .mp3 o nota de voz)")
    opcion = input("\nSelecciona opción [1/2] (por defecto 1): ").strip() or "1"

    if opcion == "1":
        archivo_muestra = grabar_microfono(duracion_segundos=10)
    else:
        ruta = input("Ingresa la ruta del archivo de audio: ").strip()
        archivo_muestra = Path(ruta)
        if not archivo_muestra.exists():
            print(f"❌ El archivo {ruta} no existe.")
            return

    print("\n✍️  ¿Qué frase quieres que diga su voz clonada?")
    texto = input("Texto: ").strip()
    if not texto:
        texto = "Hola Jonathan, esta es mi voz clonada con inteligencia artificial. Suena increíblemente idéntica."

    api_key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("FISH_API_KEY")
    if not api_key:
        api_key = input("\n🔑 Introduce tu API Key (ElevenLabs o Fish Audio): ").strip()

    if not api_key:
        print("\n⚠️ No se proporcionó API Key.")
        print(f"✅ Tu archivo de audio grabado quedó listo en: {archivo_muestra.resolve()}")
        print("Puedes subir este archivo directamente a https://huggingface.co/spaces/mrfakename/E2-F5-TTS o https://fish.audio para clonarlo gratis.")
        return

    try:
        if op_motor == "2":
            res = await clonar_y_sintetizar_fishaudio(archivo_muestra, texto, api_key)
        else:
            res = await clonar_y_sintetizar_elevenlabs(archivo_muestra, texto, api_key)
        reproducir_audio(res)
        print(f"\n🎉 ¡Prueba completada con éxito! Archivo: {res}")
    except Exception as e:
        print(f"\n❌ Error durante la clonación: {e}")


async def modo_web(port: int = 7895):
    from aiohttp import web

    print(f"\n🌐 Iniciando interfaz web interactiva en: http://localhost:{port}")

    HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>⚡ Atlas Voice Lab - Clonador de Voz</title>
    <style>
        * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; display: flex; justify-content: center; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 2rem; max-width: 650px; width: 100%; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        h1 { color: #38bdf8; font-size: 1.5rem; margin-top: 0; }
        .free-badge { background: #10b981; color: #000; font-size: 0.75rem; font-weight: bold; padding: 2px 8px; border-radius: 99px; margin-left: 8px; }
        label { display: block; margin: 1rem 0 0.5rem; font-weight: bold; color: #94a3b8; }
        input[type="text"], input[type="password"], textarea, select { width: 100%; padding: 0.75rem; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 1rem; }
        button { background: #0284c7; color: #fff; border: none; padding: 0.85rem 1.5rem; border-radius: 6px; font-size: 1rem; font-weight: bold; cursor: pointer; width: 100%; margin-top: 1.5rem; transition: background 0.2s; }
        button:hover { background: #0369a1; }
        .links-box { margin-top: 1.5rem; padding: 1rem; border-radius: 8px; background: #0f172a; border: 1px solid #334155; }
        .links-box a { color: #38bdf8; text-decoration: none; display: block; margin: 0.4rem 0; }
        .links-box a:hover { text-decoration: underline; }
        .status { margin-top: 1rem; padding: 1rem; border-radius: 6px; background: #0f172a; font-family: monospace; display: none; }
        audio { width: 100%; margin-top: 1rem; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🎙️ Atlas Voice Lab: Clonador de Voz</h1>
        <p style="color:#94a3b8; font-size:0.9rem;">Clona la voz de tu amigo subiendo un audio de 10 a 30 segundos.</p>
        
        <form id="cloneForm">
            <label>Proveedor de IA:</label>
            <select id="provider">
                <option value="elevenlabs">ElevenLabs (Máxima Calidad)</option>
                <option value="fishaudio">Fish Audio (Freemium / Free credits)</option>
            </select>

            <label>API Key del proveedor:</label>
            <input type="password" id="apiKey" placeholder="Pega tu API Key..." required />
            
            <label>Archivo de voz de muestra (.wav, .mp3 o nota de WhatsApp):</label>
            <input type="file" id="audioFile" accept="audio/*" required />
            
            <label>Texto que dirá su voz clonada:</label>
            <textarea id="ttsText" rows="3" required>¡Hola! Esta es mi voz clonada con inteligencia artificial. Suena exactamente igual.</textarea>
            
            <button type="submit" id="submitBtn">⚡ ¡Clonar Voz y Generar Audio!</button>
        </form>
        
        <div id="status" class="status"></div>
        <div id="audioPlayerContainer"></div>

        <div class="links-box">
            <h3 style="margin-top:0; font-size:1rem; color:#f8fafc;">🌐 ¿Quieres probar gratis sin API Key?</h3>
            <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:0.5rem;">Puedes grabar el audio de tu amigo y subirlo a estas webs 100% gratuitas:</p>
            <a href="https://fish.audio" target="_blank">🐟 Fish Audio (Generación en la nube gratuita)</a>
            <a href="https://app.myshell.ai" target="_blank">🐚 MyShell Voice Lab (Clonación instantánea gratis)</a>
            <a href="https://huggingface.co/spaces/mrfakename/E2-F5-TTS" target="_blank">🤗 F5-TTS Web en Hugging Face Spaces</a>
        </div>
    </div>

    <script>
        document.getElementById('cloneForm').onsubmit = async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const status = document.getElementById('status');
            const container = document.getElementById('audioPlayerContainer');
            
            btn.disabled = true;
            btn.innerText = "⏳ Clonando y sintetizando voz...";
            status.style.display = "block";
            status.innerText = "Subiendo archivo y procesando clonación...";
            container.innerHTML = "";
            
            const formData = new FormData();
            formData.append('provider', document.getElementById('provider').value);
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
                container.innerHTML = `<audio controls autoplay src="${url}"></audio><p><a href="${url}" download="voz_clonada.mp3" style="color:#38bdf8; font-weight:bold;">⬇️ Descargar archivo MP3</a></p>`;
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
        provider = "elevenlabs"
        api_key = None
        text = None
        file_bytes = None
        file_name = "audio.wav"

        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name == "provider":
                provider = (await field.read()).decode("utf-8").strip()
            elif field.name == "apiKey":
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
            if provider == "fishaudio":
                res_audio = await clonar_y_sintetizar_fishaudio(temp_input, text, api_key)
            else:
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
