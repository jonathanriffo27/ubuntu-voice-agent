import asyncio
import os
import sys
from google import genai
from google.genai import types

async def test_voice_dialogue(voice_name, prompt):
    print(f"\n{'='*40}")
    print(f"--- Probando voz (Test Largo): {voice_name} ---")
    print(f"{'='*40}")
    try:
        client = genai.Client()
        
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
            system_instruction=types.Content(parts=[types.Part.from_text(
                text="Eres un asistente de programación muy útil, paciente y profesional. Actúa como si estuvieras ayudando a un desarrollador senior a hacer debug de un problema complejo de asincronía en Python."
            )])
        )

        import pyaudio
        p = pyaudio.PyAudio()
        
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 24000
        
        stream_out = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, output=True)

        async with client.aio.live.connect(model="gemini-3.1-flash-live-preview", config=config) as session:
            print(f"Usuario: {prompt}")
            await session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                turn_complete=True
            )
            
            print(f"\n{voice_name} está respondiendo... (presiona Ctrl+C para saltar a la siguiente)")
            async for response in session.receive():
                server_content = response.server_content
                if server_content is not None:
                    model_turn = server_content.model_turn
                    if model_turn is not None:
                        for part in model_turn.parts:
                            if part.inline_data:
                                stream_out.write(part.inline_data.data)
                    
                    if server_content.turn_complete:
                        break
                        
        stream_out.stop_stream()
        stream_out.close()
        p.terminate()
        print("\n[Fin de la respuesta]")
        
    except Exception as e:
        print(f"Error al probar {voice_name}: {e}")

async def main():
    voices = ["Aoede", "Kore"]
    
    # Prompt realista y largo simulando un problema de programación
    prompt = """
    Hola. Necesito ayuda con un problema de asincronía en Python usando asyncio. 
    Tengo un script que hace múltiples peticiones HTTP concurrentes usando aiohttp, 
    pero parece que se están bloqueando en algún punto y el rendimiento es casi igual 
    que si fuera secuencial. ¿Podrías explicarme cuáles son los errores más comunes 
    que causan que asyncio se comporte de forma sincrona, y cómo debería estructurar 
    mis tareas usando asyncio.gather para evitar cuellos de botella?
    """
    
    print("Iniciando prueba de voces larga. Asegúrate de tener el volumen encendido.")
    
    for voice in voices:
        await test_voice_dialogue(voice, prompt.strip())
        await asyncio.sleep(2) # Pausa entre voces

if __name__ == "__main__":
    if "GEMINI_API_KEY" not in os.environ:
        print("Error: La variable de entorno GEMINI_API_KEY no está configurada.")
        sys.exit(1)
    
    asyncio.run(main())
