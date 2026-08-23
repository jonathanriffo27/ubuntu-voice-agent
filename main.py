import asyncio
import os
import sys
import traceback
import pyaudio
from google import genai
from google.genai import types
import tools

class LoggerTee:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")
        
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()
        
    def flush(self):
        self.terminal.flush()
        self.log.flush()
        
    def isatty(self):
        return self.terminal.isatty()
        
    def fileno(self):
        return self.terminal.fileno()

sys.stdout = LoggerTee("latest_session.log")
sys.stderr = sys.stdout
# Configuración de Audio (PyAudio)
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE = 16000 # Gemini prefiere 16kHz o 24kHz
CHUNK_SIZE = 512

# Instanciar el cliente. Asume que GEMINI_API_KEY está en las variables de entorno.
# Si no está, fallará aquí.
if not os.environ.get("GEMINI_API_KEY"):
    print("ERROR: Falta la variable de entorno GEMINI_API_KEY")
    sys.exit(1)

client = genai.Client()
MODEL = "gemini-2.0-flash-exp" # El modelo experimental que soporta Live API y tools

async def audio_worker(ws, p, input_stream):
    """Lee del micrófono y envía audio a Gemini en tiempo real."""
    print("🎤 Escuchando... (Presiona Ctrl+C para detener)")
    try:
        while True:
            # Lee el audio de forma asíncrona usando to_thread para no bloquear el event loop
            data = await asyncio.to_thread(input_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            await ws.send(input={"data": data, "mime_type": f"audio/pcm;rate={AUDIO_RATE}"})
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error en audio worker: {e}")

async def response_worker(ws, p, output_stream):
    """Escucha respuestas de Gemini (Audio, Texto, o Llamadas a Herramientas)."""
    try:
        async for msg in ws.receive():
            server_content = msg.server_content
            if not server_content:
                continue
            
            model_turn = server_content.model_turn
            if model_turn:
                for part in model_turn.parts:
                    # Si recibimos audio, lo reproducimos
                    if part.inline_data:
                        output_stream.write(part.inline_data.data)
                    # Si recibimos texto (a veces envía transcripciones o confirmaciones)
                    if part.text:
                        print(f"[Gemini]: {part.text}", end="")

            # Manejo de las herramientas (Function Calling)
            if server_content.turn_complete:
                print("\n[Turno de Gemini completado]")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error recibiendo respuesta: {e}")
        traceback.print_exc()

async def main():
    p = pyaudio.PyAudio()
    
    input_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                          rate=AUDIO_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
    
    output_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                           rate=AUDIO_RATE, output=True, frames_per_buffer=CHUNK_SIZE)

    # Configurar el sistema
    config = types.LiveConnectConfig(
        response_modalities=[types.LiveModality.AUDIO], # Queremos que nos responda con voz
        system_instruction=types.Content(parts=[types.Part.from_text(
            "Eres Jarvis, un asistente de ingeniería de software. Puedes controlar la computadora "
            "del usuario mediante herramientas. Eres directo, conciso y hablas en español."
        )]),
        # Aquí registraríamos las herramientas (Tools) si estuvieran totalmente 
        # soportadas por la API Live en el SDK actual. Vamos a iniciar la conexión primero.
    )

    print("Conectando a Gemini Live API...")
    try:
        # En la versión actual del SDK (genai), la conexión Live se hace mediante asyncio
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print("Conectado.")
            
            # Tareas concurrentes: leer microfono y escuchar respuestas
            send_task = asyncio.create_task(audio_worker(session, p, input_stream))
            recv_task = asyncio.create_task(response_worker(session, p, output_stream))
            
            await asyncio.gather(send_task, recv_task)

    except KeyboardInterrupt:
        print("\nCerrando...")
    except Exception as e:
        print(f"\nError de conexión: {e}")
        traceback.print_exc()
    finally:
        input_stream.stop_stream()
        input_stream.close()
        output_stream.stop_stream()
        output_stream.close()
        p.terminate()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSaliendo del programa.")
