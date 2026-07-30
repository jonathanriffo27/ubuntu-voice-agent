import asyncio
import os
import sys
import traceback
import struct
import pyaudio
from google import genai
from google.genai import types
import tools
from src.providers.gemini import GeminiProvider

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
    "ejecutar_comando_confirmado": tools.ejecutar_comando_confirmado,
    "buscar_en_internet": tools.buscar_en_internet,
    "imprimir_en_consola": tools.imprimir_en_consola,
    "abrir_aplicacion": tools.abrir_aplicacion,
    "guardar_nota": tools.guardar_nota,
    "borrar_nota": tools.borrar_nota,
    "guardar_perfil": tools.guardar_perfil
}

agent_tools = [{"function_declarations": [
    {"name": "obtener_estado_sistema", "description": "Obtiene la hora actual."},
    {"name": "proponer_comando", "description": "Propone un comando de terminal.", "parameters": {"type": "OBJECT", "properties": {"comando": {"type": "STRING", "description": "El comando bash exacto"}}, "required": ["comando"]}},
    {"name": "ejecutar_comando_confirmado", "description": "Ejecuta el último comando de terminal propuesto."},
    {"name": "buscar_en_internet", "description": "Realiza una búsqueda en internet y devuelve un resumen de los resultados.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "La consulta a buscar en internet"}}, "required": ["query"]}},
    {"name": "imprimir_en_consola", "description": "Imprime un texto, código, tabla o información detallada en la terminal para que el usuario pueda leerlo. Útil cuando la respuesta es muy larga o contiene formato que se pierde al hablar.", "parameters": {"type": "OBJECT", "properties": {"texto": {"type": "STRING", "description": "El texto a imprimir"}}, "required": ["texto"]}},
    {"name": "abrir_aplicacion", "description": "Abre una aplicación instalada en el sistema (ej. calculadora, terminal, code) o abre páginas web conocidas en el navegador (ej. gmail, youtube, whatsapp).", "parameters": {"type": "OBJECT", "properties": {"nombre": {"type": "STRING", "description": "El nombre de la aplicación o sitio web a abrir"}}, "required": ["nombre"]}},
    {"name": "guardar_nota", "description": "Guarda una nota general que recordarás en futuras sesiones. Para datos personales permanentes usa 'guardar_perfil'. Máximo 20 notas, las más antiguas rotan.", "parameters": {"type": "OBJECT", "properties": {"nota": {"type": "STRING", "description": "La nota a guardar (máx 200 caracteres)"}}, "required": ["nota"]}},
    {"name": "borrar_nota", "description": "Borra una nota general por su número. Úsalo cuando el usuario pida olvidar algo.", "parameters": {"type": "OBJECT", "properties": {"indice": {"type": "INTEGER", "description": "Número de la nota a borrar (1-indexado)"}}, "required": ["indice"]}},
    {"name": "guardar_perfil", "description": "Guarda un dato PERMANENTE del usuario (ej. nombre, ocupación, intereses). Estos datos NUNCA se borran automáticamente. Máx 10 campos.", "parameters": {"type": "OBJECT", "properties": {"campo": {"type": "STRING", "description": "Nombre del campo (ej. nombre, ocupacion, intereses)"}, "valor": {"type": "STRING", "description": "Valor del campo (máx 100 caracteres)"}}, "required": ["campo", "valor"]}}
]}]

SYS_PROMPT_BASE = """Eres Jarvis, asistente de Ubuntu. Sé conciso. Reglas importantes:
1. Cuando uses la herramienta 'buscar_en_internet' y recibas resultados, SIEMPRE basa tu respuesta en los datos obtenidos de la búsqueda. Los resultados de búsqueda son información ACTUAL y REAL de internet. Tu conocimiento interno puede estar desactualizado — los resultados de búsqueda son SIEMPRE más confiables y recientes.
2. NUNCA contradigas la información de una búsqueda web con tu conocimiento previo. Si los resultados dicen algo diferente a lo que "sabes", confía en los resultados de búsqueda.
3. IMPORTANTE (Consola): Siempre que des información relevante, código, listas o resúmenes largos, DEBES EJECUTAR LA HERRAMIENTA 'imprimir_en_consola' PRIMERO, y hazlo EXACTAMENTE UNA SOLA VEZ por turno. NUNCA digas "lo imprimí" si no has llamado realmente a la herramienta. Una vez ejecutada la herramienta una vez, haz un comentario por voz muy breve (ej. "Te dejé los detalles en pantalla"). No esperes a que te lo pidan.
4. IMPORTANTE: Cuando busques información sobre eventos 'recientes', 'últimos' o 'actuales' (ej. el último mundial), DEBES incluir explícitamente el año actual en tu consulta de búsqueda (ej. 'ganador mundial 2026'). Si no lo haces, los buscadores pueden devolver información desactualizada de años anteriores.
5. Si los resultados de la búsqueda NO contienen la información exacta que necesitas, intenta buscar de nuevo usando palabras clave más específicas. Si una herramienta falla técnicamente, no insistas y dile al usuario. Responde siempre en español.
6. UBICACIÓN: Tu usuario se encuentra en {ubicacion}. Cuando busques noticias, clima, eventos o información local, PRIORIZA resultados de {ubicacion}. Incluye el país/región en las consultas de búsqueda cuando sea relevante.
7. MEMORIA: Hay dos tipos de memoria:
   - PERFIL ('guardar_perfil'): Para datos PERMANENTES del usuario (nombre, ocupación, intereses). Nunca se borran automáticamente. Usa esto cuando el usuario te diga datos personales.
   - NOTAS ('guardar_nota'): Para información general y temporal. Las más antiguas rotan al llegar a 20. Guarda solo datos concisos."""

MAX_MEMORY_PROMPT_CHARS = 1500  # Límite de caracteres de memoria inyectados al prompt

def build_system_prompt():
    """Construye el prompt del sistema con ubicación, perfil y memoria."""
    ubicacion = tools.detect_user_location() or "ubicación desconocida"
    prompt = SYS_PROMPT_BASE.format(ubicacion=ubicacion)
    
    datos = tools.cargar_memoria()
    perfil = datos.get("perfil", {})
    notas = datos.get("notas", [])
    
    memoria_section = ""
    
    # Perfil: siempre se incluye completo (es pequeño y permanente)
    if perfil:
        memoria_section += "\n\n--- PERFIL DEL USUARIO ---\n"
        for campo, valor in perfil.items():
            memoria_section += f"- {campo}: {valor}\n"
    
    # Notas: se incluyen con límite de caracteres restantes
    if notas:
        memoria_section += "\n--- NOTAS ---\n"
        for i, nota in enumerate(notas, 1):
            linea = f"{i}. {nota}\n"
            if len(memoria_section) + len(linea) > MAX_MEMORY_PROMPT_CHARS:
                memoria_section += f"(... {len(notas) - i + 1} notas más omitidas por límite)\n"
                break
            memoria_section += linea
    
    if memoria_section:
        prompt += memoria_section + "--- FIN MEMORIA ---"
    
    return prompt, ubicacion, datos
jarvis_is_speaking = False
processing_tool = False
is_paused = False
silence_threshold = None  # Se calibra automáticamente al inicio
# Track consecutive identical tool calls to prevent infinite loops
_last_tool_call = {"name": None, "args": None, "count": 0}
MAX_TOOL_RETRIES = 2

def play_sound(sound_type):
    import subprocess
    sounds = {
        "pause": "/usr/share/sounds/freedesktop/stereo/device-removed.oga",
        "resume": "/usr/share/sounds/freedesktop/stereo/device-added.oga",
        "processing": "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga"
    }
    if sound_type in sounds:
        try:
            subprocess.Popen(["pw-play", sounds[sound_type]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

async def calibrate_microphone(input_stream, duration=2.0):
    """Escucha ruido ambiente y calcula el umbral de silencio automáticamente."""
    print("🎤 Calibrando micrófono... (no hables)")
    samples = []
    total_frames = int(AUDIO_IN_RATE / CHUNK_SIZE * duration)
    for _ in range(total_frames):
        data = await asyncio.to_thread(input_stream.read, CHUNK_SIZE, exception_on_overflow=False)
        shorts = struct.unpack('h' * (len(data) // 2), data)
        rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
        samples.append(rms)
    
    if not samples:
        print("⚠️ No se pudo calibrar, usando umbral por defecto.")
        return 12000
    
    avg_rms = sum(samples) / len(samples)
    # Umbral = 2.5x el ruido ambiente, con un piso mínimo de 800
    # para evitar falsos positivos en ambientes ultra-silenciosos
    threshold = max(800, int(avg_rms * 2.5))
    print(f"🎤 Calibración completa — Ruido ambiente: {int(avg_rms)} | Umbral: {threshold}")
    return threshold

async def listen_audio(input_stream, audio_queue_input, audio_queue_output, oww_model, np):
    global jarvis_is_speaking, processing_tool, is_paused, silence_threshold
    silence_frames = 0
    frames_per_second = AUDIO_IN_RATE / CHUNK_SIZE
    max_silence_seconds = 1.0
    user_spoke = False
    
    while True:
        try:
            if not input_stream.is_active():
                break
            
            data = await asyncio.to_thread(input_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            
            if is_paused:
                # Convertir a arreglo numpy y predecir
                audio_np = np.frombuffer(data, dtype=np.int16)
                prediction = oww_model.predict(audio_np)
                
                max_score = max(prediction.values()) if prediction else 0
                if max_score > 0.5:
                    is_paused = False
                    play_sound("resume")
                    print(f"\r[REANUDADO ▶️] - ¡'Hey Jarvis' detectado! Micrófono activado.      \n", end='', flush=True)
                
                await asyncio.sleep(0.001)
                continue
            
            # Suppress ALL audio input ONLY while processing a tool
            if not processing_tool:
                shorts = struct.unpack('h' * (len(data) // 2), data)
                rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
                
                if jarvis_is_speaking:
                    # Umbral de interrupción: exactamente el mismo que el umbral de voz normal.
                    barge_in_threshold = silence_threshold
                    if rms > barge_in_threshold:
                        print("\n[🎙️ Interrupción de voz detectada]", flush=True)
                        jarvis_is_speaking = False
                        
                        # Vaciar la cola de reproducción localmente para silenciar a Jarvis de inmediato
                        while not audio_queue_output.empty():
                            try:
                                audio_queue_output.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        
                        # NO enviar END_OF_TURN — dejar que el audio del usuario fluya
                        # para que el servidor detecte el barge-in nativamente
                        silence_frames = 0
                        user_spoke = True
                        # Enviar este fragmento de audio para que el servidor detecte la interrupción
                        await audio_queue_input.put(data)
                else:
                    await audio_queue_input.put(data)
                    
                    if rms < silence_threshold:
                        if user_spoke:
                            silence_frames += 1
                    else:
                        silence_frames = 0
                        user_spoke = True
                        
                    if user_spoke and silence_frames > (frames_per_second * max_silence_seconds):
                        await audio_queue_input.put("END_OF_TURN")
                        play_sound("processing")
                        silence_frames = 0
                        user_spoke = False
            else:
                # Reset silence counter while suppressed so we don't fire END_OF_TURN immediately when resuming
                silence_frames = 0
                user_spoke = False
                
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error micro: {e}")
            break

async def send_realtime(session, audio_queue_input):
    global processing_tool
    while True:
        try:
            data = await audio_queue_input.get()
            # Double-check: don't send anything to the session while processing a tool
            if processing_tool:
                continue
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
            # Si fue interrumpido mientras esperábamos, descartar este chunk
            if not jarvis_is_speaking:
                continue
            await asyncio.to_thread(output_stream.write, data)
            await asyncio.sleep(0.001)
        except asyncio.TimeoutError:
            jarvis_is_speaking = False
        except Exception as e:
            print(f"Error speaker: {e}")
            break

async def receive_and_route(session, audio_queue_output, audio_queue_input):
    global jarvis_is_speaking, processing_tool
    printed_jarvis_prefix = False
    executed_in_turn = set()
    try:
        while True:
            async for msg in session.receive():
                if msg is None:
                    continue
                sc = msg.server_content
                if sc is not None:
                    msg_interrupted = False
                    if sc.interrupted:
                        jarvis_is_speaking = False
                        msg_interrupted = True
                        printed_jarvis_prefix = False
                        while not audio_queue_output.empty():
                            try:
                                audio_queue_output.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                    if sc.model_turn is not None and not msg_interrupted:
                        jarvis_is_speaking = True
                        for part in sc.model_turn.parts:
                            if part.inline_data:
                                if jarvis_is_speaking:
                                    audio_queue_output.put_nowait(part.inline_data.data)
                            elif part.text:
                                if not printed_jarvis_prefix:
                                    sys.stdout.write("\n🤖 Jarvis: ")
                                    printed_jarvis_prefix = True
                                sys.stdout.write(part.text)
                                sys.stdout.flush()
                            await asyncio.sleep(0)
                    if getattr(sc, 'turn_complete', False):
                        jarvis_is_speaking = False
                        if printed_jarvis_prefix:
                            sys.stdout.write("\n")
                            printed_jarvis_prefix = False

                if msg.tool_call is not None:
                    # CRITICAL: Set flag BEFORE executing the tool to prevent
                    # audio/END_OF_TURN from being sent during tool execution
                    processing_tool = True
                    
                    responses = []
                    for fc in msg.tool_call.function_calls:
                        args_dict = dict(fc.args) if fc.args else {}
                        call_key = f"{fc.name}:{args_dict}"
                        
                        if _last_tool_call["name"] == call_key:
                            _last_tool_call["count"] += 1
                        else:
                            _last_tool_call["name"] = call_key
                            _last_tool_call["args"] = args_dict
                            _last_tool_call["count"] = 1
                            
                        # Si el modelo intenta llamar a la MISMA herramienta dos veces seguidas, ignoramos la segunda.
                        if _last_tool_call["count"] > 1 and fc.name != "obtener_estado_sistema":
                            responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response={"status": "success", "message": "Ignorado por ser duplicado consecutivo."}))
                            continue
                            
                        if fc.name != "obtener_estado_sistema":
                            print(f"\n🔧 Ejecutando herramienta: {fc.name}")
                        
                        if fc.name in TOOL_FUNCTIONS:
                            try:
                                # Convert the Protobuf args to a standard Python dictionary if needed
                                args_dict = dict(fc.args) if fc.args else {}
                                res = await asyncio.to_thread(TOOL_FUNCTIONS[fc.name], **args_dict)
                                # Ensure the response is properly formatted as a dict for FunctionResponse
                                if not isinstance(res, dict):
                                    res = {"result": str(res)}
                                # Log what we're actually sending to the model
                                if fc.name != "obtener_estado_sistema":
                                    print(f"📦 Herramienta {fc.name} completada")
                                responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response=res))
                            except Exception as e:
                                print(f"\n[ERROR EN TOOL {fc.name}]: {e}")
                                responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response={"error": str(e)}))
                        else:
                            print(f"⚠️ [TOOL NO ENCONTRADA] {fc.name}")
                    
                    if responses:
                        # Drain the input queue to discard any stale audio/END_OF_TURN
                        # that accumulated while the tool was executing
                        drained = 0
                        while not audio_queue_input.empty():
                            try:
                                audio_queue_input.get_nowait()
                                drained += 1
                            except asyncio.QueueEmpty:
                                break
                        if drained > 0:
                            print(f"🗑️ [DRAINED] {drained} mensajes de audio descartados de la cola")
                        
                        try:
                            await session.send_tool_response(function_responses=responses)
                        except Exception as e:
                            print(f"\n[ERROR ENVIANDO RESPUESTA DE TOOL]: {e}")
                            traceback.print_exc()
                    
                    # Resume audio input AFTER the tool response has been sent
                    processing_tool = False
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"\n[CRITICO recv]: {e}")
        raise
    finally:
        jarvis_is_speaking = False
        processing_tool = False

async def main():
    global silence_threshold
    # Suprimir mensajes molestos de ALSA y JACK en stderr
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(sys.stderr.fileno())
    sys.stderr.flush()
    os.dup2(devnull, sys.stderr.fileno())

    try:
        p = pyaudio.PyAudio()
    finally:
        os.dup2(old_stderr, sys.stderr.fileno())
        os.close(devnull)
        os.close(old_stderr)

    print("Cargando modelo de wake word (Hey Jarvis)...")
    try:
        import openwakeword
        from openwakeword.model import Model
        import numpy as np
        # Cargar solo el modelo de Jarvis para ahorrar memoria
        model_paths = [p for p in openwakeword.get_pretrained_model_paths() if 'hey_jarvis' in p]
        oww_model = Model(wakeword_model_paths=model_paths)
    except ImportError:
        print("❌ ERROR: openwakeword no está instalado. Ejecuta: pip install openwakeword")
        sys.exit(1)

    in_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_IN_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
    out_stream = p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_OUT_RATE, output=True, frames_per_buffer=CHUNK_SIZE)

    # Calibración automática del micrófono
    silence_threshold = await calibrate_microphone(in_stream)

    provider = GeminiProvider(model_name=MODEL)
    
    # Construir prompt dinámico con ubicación y memoria
    sys_prompt, ubicacion, datos = build_system_prompt()
    perfil = datos.get("perfil", {})
    notas = datos.get("notas", [])
    print(f"🌍 Ubicación detectada: {ubicacion}")
    if perfil:
        print(f"👤 Perfil cargado: {', '.join(f'{k}={v}' for k, v in perfil.items())}")
    if notas:
        print(f"💾 Notas cargadas: {len(notas)}")

    q_in = asyncio.Queue()
    q_out = asyncio.Queue()

    print(f"Conectando a {MODEL}...")
    try:
        async def input_watcher():
            global is_paused, silence_threshold
            import termios, tty
            loop = asyncio.get_running_loop()
            
            def read_char():
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    # cbreak mode: lee caracteres inmediatamente, pero preserva Ctrl+C
                    tty.setcbreak(fd)
                    return sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            while True:
                ch = await loop.run_in_executor(None, read_char)
                ch = ch.lower()
                if ch == ' ':
                    is_paused = not is_paused
                    play_sound("pause" if is_paused else "resume")
                    estado = "PAUSADO ⏸️" if is_paused else "REANUDADO ▶️"
                    print(f"\r[{estado}] - Micrófono {'desactivado' if is_paused else 'activado'}.\n", end='')
                elif ch == 'l':
                    silence_threshold += 500
                    print(f"\r[🎤 UMBRAL] Aumentado a: {silence_threshold} (Exige hablar más fuerte)\n", end='')
                elif ch == 'j':
                    silence_threshold = max(0, silence_threshold - 500)
                    print(f"\r[🎤 UMBRAL] Reducido a: {silence_threshold} (Más sensible al ruido)\n", end='')
                elif ch == '\n' or ch == '\r':
                    try:
                        await q_in.put("END_OF_TURN")
                        play_sound("processing")
                    except Exception:
                        pass

        async with provider.connect(system_prompt=sys_prompt, tools=agent_tools) as session:
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
                    tg.create_task(listen_audio(in_stream, q_in, q_out, oww_model, np))
                    tg.create_task(send_realtime(session, q_in))
                    tg.create_task(play_audio(out_stream, q_out))
                    tg.create_task(receive_and_route(session, q_out, q_in))
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
        try:
            if in_stream.is_active(): in_stream.stop_stream()
            if out_stream.is_active(): out_stream.stop_stream()
            in_stream.close()
            out_stream.close()
            p.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSaliendo de Jarvis...")
        os._exit(0)
