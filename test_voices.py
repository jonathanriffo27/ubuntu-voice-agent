import asyncio
import os
import sys
from google import genai
from google.genai import types

async def test_voice(voice_name, text_to_say):
    print(f"\n--- Probando voz: {voice_name} ---")
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
                text="Solo debes repetir la siguiente frase, no agregues nada mas."
            )])
        )

        # Importamos pyaudio aqui para no romper si no está instalado
        import pyaudio
        p = pyaudio.PyAudio()
        
        # Formato de audio devuelto por Gemini
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 24000
        
        stream_out = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, output=True)

        async with client.aio.live.connect(model="gemini-3.1-flash-live-preview", config=config) as session:
            # Enviamos el texto
            await session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part.from_text(text=text_to_say)])],
                turn_complete=True
            )
            
            # Recibimos el audio
            print("Reproduciendo... (presiona Ctrl+C para saltar a la siguiente)")
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
        print("Fin de la reproducción.")
        
    except Exception as e:
        print(f"Error al probar {voice_name}: {e}")

async def main():
    voices = ["Aoede", "Puck", "Charon", "Kore", "Fenrir"]
    text = "Hola, mi nombre es {voz}. Esta es una prueba de mi voz usando la API de Gemini."
    
    print("Iniciando prueba de voces. Asegúrate de tener el volumen encendido.")
    
    for voice in voices:
        await test_voice(voice, text.format(voz=voice))
        await asyncio.sleep(1) # Pausa entre voces

if __name__ == "__main__":
    if "GEMINI_API_KEY" not in os.environ:
        print("Error: La variable de entorno GEMINI_API_KEY no está configurada.")
        sys.exit(1)
    
    asyncio.run(main())
