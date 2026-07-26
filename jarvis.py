import asyncio
import os
import sys
import traceback
import pyaudio
from google import genai
from google.genai import types
import tools

AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_IN_RATE = 16000
AUDIO_OUT_RATE = 24000
CHUNK_SIZE = 512

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ ERROR: Debes exportar GEMINI_API_KEY.")
    sys.exit(1)

MODEL = "gemini-3.1-flash-live-preview"

TOOL_FUNCTIONS = {
    "obtener_estado_sistema": tools.obtener_estado_sistema,
    "proponer_comando": tools.proponer_comando,
    "ejecutar_comando_confirmado": tools.ejecutar_comando_confirmado
}

agent_tools = [{"function_declarations": [
    {"name": "obtener_estado_sistema", "description": "Obtiene la hora actual."},
    {"name": "proponer_comando", "description": "Propone un comando de terminal.", "parameters": {"type": "OBJECT", "properties": {"comando": {"type": "STRING", "description": "El comando bash exacto"}}, "required": ["comando"]}},
    {"name": "ejecutar_comando_confirmado", "description": "Ejecuta el último comando de terminal propuesto."}
]}]

SYS_PROMPT = "Eres Jarvis, asistente de Ubuntu. Sé conciso."
jarvis_is_speaking = False

async def listen_audio(input_stream, audio_queue_input):
    global jarvis_is_speaking
    silence_threshold = 12208
    silence_frames = 0
    frames_per_second = AUDIO_IN_RATE / CHUNK_SIZE
    max_silence_seconds = 1.0
    
    while True:
        try:
            if not input_stream.is_active():
                break
            data = await asyncio.to_thread(input_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            
            if not jarvis_is_speaking:
                await audio_queue_input.put(data)
                
                import struct
                shorts = struct.unpack('h' * (len(data) // 2), data)
                rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
                
                if rms < silence_threshold:
                    silence_frames += 1
                else:
                    silence_frames = 0
                    
                if silence_frames > (frames_per_second * max_silence_seconds):
                    print(".", end="", flush=True) 
                    await audio_queue_input.put("END_OF_TURN")
                    silence_frames = - (frames_per_second * 2)
                
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error micro: {e}")
            break

async def send_realtime(session, audio_queue_input):
    while True:
        try:
            data = await audio_queue_input.get()
            if data == "END_OF_TURN":
                await session.send_client_content(turn_complete=True)
                continue
            await session.send_realtime_input(audio={"data": data, "mime_type": f"audio/pcm;rate={AUDIO_IN_RATE}"})
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"\n[CRÍTICO send_realtime]: {e}", flush=True)
            raise

async def play_audio(output_stream, audio_queue_output):
    global jarvis_is_speaking
    while True:
        try:
            data = await asyncio.wait_for(audio_queue_output.get(), timeout=1.5)
            await asyncio.to_thread(output_stream.write, data)
            await asyncio.sleep(0.001)
        except asyncio.TimeoutError:
            jarvis_is_speaking = False
        except Exception as e:
            print(f"Error speaker: {e}")
            break

async def receive_and_route(session, audio_queue_output):
    global jarvis_is_speaking
    try:
        while True:
            async for msg in session.receive():
                if msg is None:
                    continue
                sc = msg.server_content
                if sc is not None:
                    if sc.interrupted:
                        jarvis_is_speaking = False
                        while not audio_queue_output.empty():
                            try:
                                audio_queue_output.get_nowait()
                                audio_queue_output.task_done()
                            except asyncio.QueueEmpty:
                                break
                    if sc.model_turn is not None:
                        jarvis_is_speaking = True
                        for part in sc.model_turn.parts:
                            if part.inline_data:
                                pass
                                audio_queue_output.put_nowait(part.inline_data.data)
                            elif part.text:
                                sys.stdout.write(part.text)
                                sys.stdout.flush()
                            await asyncio.sleep(0)
                    if getattr(sc, 'turn_complete', False):
                        jarvis_is_speaking = False
                        pass

                if msg.tool_call is not None:
                    responses = []
                    for fc in msg.tool_call.function_calls:
                        if fc.name in TOOL_FUNCTIONS:
                            res = await asyncio.to_thread(TOOL_FUNCTIONS[fc.name], **(fc.args if fc.args else {}))
                            responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response=res))
                    if responses:
                        await session.send_tool_response(function_responses=responses)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"\n[CRITICO recv]: {e}")
        raise
    finally:
        jarvis_is_speaking = False

async def main():
    p = pyaudio.PyAudio()
    in_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_IN_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
    out_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_OUT_RATE, output=True, frames_per_buffer=CHUNK_SIZE)

    client = genai.Client()
    
    # La config MÁS restrictiva posible para Live API en 2.14
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede"))),
        system_instruction=types.Content(parts=[types.Part.from_text(text=SYS_PROMPT)]),
        tools=agent_tools
    )

    q_in = asyncio.Queue()
    q_out = asyncio.Queue()

    print(f"Conectando a {MODEL}...")
    try:
        async def input_watcher():
            loop = asyncio.get_running_loop()
            while True:
                await loop.run_in_executor(None, sys.stdin.readline)
                try:
                    await q_in.put("END_OF_TURN")
                except Exception:
                    pass

        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print("\n✅ Conexión establecida. Comienza a hablar.\n")
            
            # Enviar mensaje de texto inicial corregido
            # Enviar mensaje de inicialización correctamente con el nuevo SDK (sin warning)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                await session.send(
                    input="Iniciando sistema.",
                    end_of_turn=True
                )

            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(listen_audio(in_stream, q_in))
                    tg.create_task(send_realtime(session, q_in))
                    tg.create_task(play_audio(out_stream, q_out))
                    tg.create_task(receive_and_route(session, q_out))
                    tg.create_task(input_watcher())
            except ExceptionGroup as eg:
                for exc in eg.exceptions:
                    if not isinstance(exc, asyncio.CancelledError):
                        print(f"\n[CRASH]: {exc}")
                        os._exit(1)

    except KeyboardInterrupt:
        print("\nDeteniendo...")
    except Exception as e:
        traceback.print_exc()
    finally:
        if in_stream.is_active(): in_stream.stop_stream()
        if out_stream.is_active(): out_stream.stop_stream()
        in_stream.close()
        out_stream.close()
        p.terminate()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
