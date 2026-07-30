import asyncio
import os
import sys
import traceback
import struct
import pyaudio
import warnings
from google.genai import types
from src.providers.base import BaseProvider
from src.brain.prompts import build_system_prompt
from src.tools.registry import ToolRegistry
from src.tools.base import ToolContext
from src.knowledge.manager import KnowledgeManager
from src.events.base import (
    ConversationContext, SessionStarted, SessionEnded, VoiceListeningStarted, 
    VoiceListeningStopped, SpeechRecognized, ToolStarted, ToolSucceeded, 
    ToolFailed, ResponseGenerated, ErrorOccurred
)
from src.events.bus import EventBus

AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_IN_RATE = 16000
AUDIO_OUT_RATE = 24000
CHUNK_SIZE = 512

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

class Assistant:
    def __init__(self, provider: BaseProvider, registry: ToolRegistry, config=None, knowledge_manager: KnowledgeManager = None, event_bus: EventBus = None):
        self.provider = provider
        self.registry = registry
        self.config = config
        self.knowledge_manager = knowledge_manager
        self.event_bus = event_bus or EventBus()
        self.conversation_context = ConversationContext()
        
        self.jarvis_is_speaking = False
        self.processing_tool = False
        self.is_paused = False
        self.silence_threshold = None
        self._last_tool_call = {"name": None, "args": None, "count": 0}
        
        self.p = None
        self.in_stream = None
        self.out_stream = None
        self.oww_model = None
        self.np = None

    async def calibrate_microphone(self, duration=2.0):
        print("🎤 Calibrando micrófono... (no hables)")
        samples = []
        total_frames = int(AUDIO_IN_RATE / CHUNK_SIZE * duration)
        for _ in range(total_frames):
            data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            shorts = struct.unpack('h' * (len(data) // 2), data)
            rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
            samples.append(rms)
        
        if not samples:
            print("⚠️ No se pudo calibrar, usando umbral por defecto.")
            return 12000
        
        avg_rms = sum(samples) / len(samples)
        threshold = max(800, int(avg_rms * 2.5))
        print(f"🎤 Calibración completa — Ruido ambiente: {int(avg_rms)} | Umbral: {threshold}")
        return threshold

    async def listen_audio(self, audio_queue_input, audio_queue_output):
        silence_frames = 0
        frames_per_second = AUDIO_IN_RATE / CHUNK_SIZE
        max_silence_seconds = 1.0
        user_spoke = False
        
        while True:
            try:
                if not self.in_stream.is_active():
                    break
                
                data = await asyncio.to_thread(self.in_stream.read, CHUNK_SIZE, exception_on_overflow=False)
                
                if self.is_paused:
                    audio_np = self.np.frombuffer(data, dtype=self.np.int16)
                    prediction = self.oww_model.predict(audio_np)
                    max_score = max(prediction.values()) if prediction else 0
                    if max_score > 0.5:
                        self.is_paused = False
                        play_sound("resume")
                        print(f"\r[REANUDADO ▶️] - ¡'Hey Atlas' detectado! Micrófono activado.      \n", end='', flush=True)
                    await asyncio.sleep(0.001)
                    continue
                
                if not self.processing_tool:
                    shorts = struct.unpack('h' * (len(data) // 2), data)
                    rms = sum(abs(s) for s in shorts) / len(shorts) if shorts else 0
                    
                    if self.jarvis_is_speaking:
                        barge_in_threshold = self.silence_threshold
                        if rms > barge_in_threshold:
                            print("\n[🎙️ Interrupción de voz detectada]", flush=True)
                            self.jarvis_is_speaking = False
                            
                            while not audio_queue_output.empty():
                                try:
                                    audio_queue_output.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                            
                            silence_frames = 0
                            user_spoke = True
                            await audio_queue_input.put(data)
                    else:
                        await audio_queue_input.put(data)
                        
                        if rms < self.silence_threshold:
                            if user_spoke:
                                silence_frames += 1
                        else:
                            silence_frames = 0
                            if not user_spoke:
                                self.event_bus.publish(VoiceListeningStarted(self.conversation_context))
                            user_spoke = True
                            
                        if user_spoke and silence_frames > (frames_per_second * max_silence_seconds):
                            await audio_queue_input.put("END_OF_TURN")
                            self.event_bus.publish(VoiceListeningStopped(self.conversation_context))
                            # En lugar de texto, notificamos que se terminó de escuchar
                            self.event_bus.publish(SpeechRecognized(self.conversation_context, text="[Audio enviado]"))
                            play_sound("processing")
                            silence_frames = 0
                            user_spoke = False
                else:
                    silence_frames = 0
                    user_spoke = False
                    
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error micro: {e}")
                break

    async def send_realtime(self, session, audio_queue_input):
        while True:
            try:
                data = await audio_queue_input.get()
                if self.processing_tool:
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

    async def play_audio(self, audio_queue_output):
        while True:
            try:
                data = await asyncio.wait_for(audio_queue_output.get(), timeout=1.5)
                if not self.jarvis_is_speaking:
                    continue
                await asyncio.to_thread(self.out_stream.write, data)
                await asyncio.sleep(0.001)
            except asyncio.TimeoutError:
                self.jarvis_is_speaking = False
            except Exception as e:
                print(f"Error speaker: {e}")
                break

    async def receive_and_route(self, session, audio_queue_output, audio_queue_input):
        printed_prefix = False
        try:
            while True:
                async for msg in session.receive():
                    if msg is None:
                        continue
                    sc = msg.server_content
                    if sc is not None:
                        msg_interrupted = False
                        if sc.interrupted:
                            self.jarvis_is_speaking = False
                            msg_interrupted = True
                            printed_prefix = False
                            while not audio_queue_output.empty():
                                try:
                                    audio_queue_output.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                        if sc.model_turn is not None and not msg_interrupted:
                            self.jarvis_is_speaking = True
                            for part in sc.model_turn.parts:
                                if part.inline_data:
                                    if self.jarvis_is_speaking:
                                        audio_queue_output.put_nowait(part.inline_data.data)
                                elif part.text:
                                    if not printed_prefix:
                                        printed_prefix = True
                                    self.event_bus.publish(ResponseGenerated(self.conversation_context, text=part.text))
                                await asyncio.sleep(0)
                        if getattr(sc, 'turn_complete', False):
                            self.jarvis_is_speaking = False
                            if printed_prefix:
                                sys.stdout.write("\n")
                                printed_prefix = False

                    if msg.tool_call is not None:
                        self.processing_tool = True
                        responses = []
                        for fc in msg.tool_call.function_calls:
                            args_dict = dict(fc.args) if fc.args else {}
                            call_key = f"{fc.name}:{args_dict}"
                            
                            if self._last_tool_call["name"] == call_key:
                                self._last_tool_call["count"] += 1
                            else:
                                self._last_tool_call["name"] = call_key
                                self._last_tool_call["args"] = args_dict
                                self._last_tool_call["count"] = 1
                                
                            if self._last_tool_call["count"] > 1 and fc.name != "obtener_estado_sistema":
                                responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response={"status": "success", "message": "Ignorado por duplicado."}))
                                continue
                                
                            if fc.name != "obtener_estado_sistema":
                                self.event_bus.publish(ToolStarted(self.conversation_context, tool_name=fc.name, arguments=args_dict))
                            
                            tool = self.registry.get_tool(fc.name)
                            if tool:
                                try:
                                    context = ToolContext(
                                        config=self.config,
                                        event_bus=self.event_bus,
                                        conversation_context=self.conversation_context
                                    )
                                    tool_result = await tool.execute(context, **args_dict)
                                    
                                    # Formatear la respuesta para Gemini
                                    res_dict = {"result": tool_result.content}
                                    
                                    # Si la herramienta devuelve metadata (ej. imagen de la pantalla)
                                    if tool_result.metadata:
                                        if "inline_data" in tool_result.metadata:
                                            inline = tool_result.metadata["inline_data"]
                                            try:
                                                import base64
                                                data_bytes = base64.b64decode(inline["data"])
                                                # En Gemini Live, las imágenes se envían como realtime_input de video
                                                await session.send_realtime_input(
                                                    video={"mime_type": inline["mime_type"], "data": data_bytes}
                                                )
                                                res_dict["status"] = "Imagen adjuntada exitosamente al flujo de video."
                                            except Exception as ve:
                                                res_dict["status"] = f"Error inyectando imagen: {ve}"
                                        else:
                                            res_dict.update(tool_result.metadata)
                                    
                                    if fc.name != "obtener_estado_sistema":
                                        self.event_bus.publish(ToolSucceeded(self.conversation_context, tool_name=fc.name, result=res_dict))
                                    
                                    responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response=res_dict))
                                except Exception as e:
                                    self.event_bus.publish(ToolFailed(self.conversation_context, tool_name=fc.name, error=str(e)))
                                    responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response={"error": str(e)}))
                            else:
                                self.event_bus.publish(ErrorOccurred(self.conversation_context, error=f"Tool no encontrada: {fc.name}", source="Assistant"))
                        
                        if responses:
                            drained = 0
                            while not audio_queue_input.empty():
                                try:
                                    audio_queue_input.get_nowait()
                                    drained += 1
                                except asyncio.QueueEmpty:
                                    break
                            if drained > 0:
                                print(f"🗑️ [DRAINED] {drained} mensajes descartados")
                            
                            try:
                                await session.send_tool_response(function_responses=responses)
                            except Exception as e:
                                print(f"\n[ERROR ENVIANDO RESPUESTA DE TOOL]: {e}")
                        
                        self.processing_tool = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.event_bus.publish(ErrorOccurred(self.conversation_context, error=str(e), source="Assistant: receive_and_route"))
            raise
        finally:
            self.jarvis_is_speaking = False
            self.processing_tool = False

    async def input_watcher(self, q_in):
        import termios, tty
        loop = asyncio.get_running_loop()
        
        def read_char():
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                return sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        while True:
            ch = await loop.run_in_executor(None, read_char)
            ch = ch.lower()
            if ch == ' ':
                self.is_paused = not self.is_paused
                play_sound("pause" if self.is_paused else "resume")
                estado = "PAUSADO ⏸️" if self.is_paused else "REANUDADO ▶️"
                print(f"\r[{estado}] - Micrófono {'desactivado' if self.is_paused else 'activado'}.\n", end='')
            elif ch == 'l':
                self.silence_threshold += 500
                print(f"\r[🎤 UMBRAL] Aumentado a: {self.silence_threshold}\n", end='')
            elif ch == 'j':
                self.silence_threshold = max(0, self.silence_threshold - 500)
                print(f"\r[🎤 UMBRAL] Reducido a: {self.silence_threshold}\n", end='')
            elif ch == '\n' or ch == '\r':
                try:
                    await q_in.put("END_OF_TURN")
                    play_sound("processing")
                except Exception:
                    pass

    async def run_async(self):
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(sys.stderr.fileno())
        sys.stderr.flush()
        os.dup2(devnull, sys.stderr.fileno())

        try:
            self.p = pyaudio.PyAudio()
        finally:
            os.dup2(old_stderr, sys.stderr.fileno())
            os.close(devnull)
            os.close(old_stderr)

        print("Cargando modelo de wake word (Hey Atlas)...")
        try:
            import openwakeword
            from openwakeword.model import Model
            import numpy as np
            self.np = np
            model_paths = [p for p in openwakeword.get_pretrained_model_paths() if 'hey_jarvis' in p]
            self.oww_model = Model(wakeword_model_paths=model_paths)
        except ImportError:
            print("❌ ERROR: openwakeword no está instalado.")
            sys.exit(1)

        self.in_stream = self.p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_IN_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
        self.out_stream = self.p.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_OUT_RATE, output=True, frames_per_buffer=CHUNK_SIZE)

        self.silence_threshold = await self.calibrate_microphone()

        sys_prompt, ubicacion = build_system_prompt(self.knowledge_manager)
        perfil = self.knowledge_manager.get_profile() if self.knowledge_manager else {}
        notas = self.knowledge_manager.get_notes() if self.knowledge_manager else []
        print(f"🌍 Ubicación detectada: {ubicacion}")
        if perfil:
            print(f"👤 Perfil cargado: {', '.join(f'{k}={v}' for k, v in perfil.items())}")
        if notas:
            print(f"💾 Notas cargadas: {len(notas)}")

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        print("Conectando al proveedor...")
        try:
            async with self.provider.connect(system_prompt=sys_prompt, tools=self.registry.get_all_tools()) as session:
                self.event_bus.publish(SessionStarted(self.conversation_context))
                
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    await session.send(input="Iniciando sistema.", end_of_turn=True)

                try:
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self.listen_audio(q_in, q_out))
                        tg.create_task(self.send_realtime(session, q_in))
                        tg.create_task(self.play_audio(q_out))
                        tg.create_task(self.receive_and_route(session, q_out, q_in))
                        tg.create_task(self.input_watcher(q_in))
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
            self.cleanup()

    def cleanup(self):
        try:
            if self.in_stream and self.in_stream.is_active(): self.in_stream.stop_stream()
            if self.out_stream and self.out_stream.is_active(): self.out_stream.stop_stream()
            if self.in_stream: self.in_stream.close()
            if self.out_stream: self.out_stream.close()
            if self.p: self.p.terminate()
        except Exception:
            pass

    def run(self):
        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            self.event_bus.publish(SessionEnded(self.conversation_context))
            os._exit(0)
