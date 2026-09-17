from contextlib import asynccontextmanager
from typing import List, AsyncIterator, Optional
from google import genai
from google.genai import types
from src.providers.base import BaseProvider, ProviderSession
from src.providers.gemini_session import GeminiSession
from src.tools.base import BaseTool
from src.utils.logging import get_logger

logger = get_logger("providers.gemini")


class GeminiProvider(BaseProvider):
    def __init__(
        self,
        model_name: str = "gemini-3.8-live",
        voice_name: str = "Aoede",
        server_vad: bool = False,
        affective_dialog: bool = False
    ):
        self.model_name = model_name
        self.voice_name = voice_name
        self.client = genai.Client()
        # gemini-3.8-live IGNORA audio_stream_end=True (medido empíricamente: el
        # turno de voz nunca se cierra y el modelo no responde). La vía viable es
        # el VAD del servidor (automatic activity detection): por eso en los
        # modelos 3.8 se fuerza server_vad aunque la config diga lo contrario.
        self.server_vad = server_vad or "gemini-3.8" in model_name
        if self.server_vad and "gemini-3.8" in model_name and not server_vad:
            logger.warning(
                f"{model_name} requiere VAD del servidor: se fuerza server_vad=True "
                "(audio_stream_end no cierra turnos en este modelo)."
            )
        self.affective_dialog = affective_dialog
        # Handle de session resumption: sobrevive a las reconexiones del WebSocket
        # para no perder el contexto conversacional (las sesiones mueren ~cada 15 min).
        self._session_handle: Optional[str] = None

    def _save_session_handle(self, handle: str) -> None:
        self._session_handle = handle

    @asynccontextmanager
    async def connect(self, system_prompt: str, tools: List[BaseTool]) -> AsyncIterator[ProviderSession]:
        """
        Retorna el manejador de conexión asíncrono para Gemini Live API.
        Convierte la lista agnóstica de BaseTool al formato de genai y entrega una ProviderSession.
        """
        gemini_tools = []
        if tools:
            declarations = []
            for tool in tools:
                decl = {
                    "name": tool.name,
                    "description": tool.description,
                }
                if tool.parameters:
                    decl["parameters"] = tool.parameters
                declarations.append(decl)
            gemini_tools = [{"function_declarations": declarations}]

        # Session Resumption: en la primera conexión solo se habilita; en las
        # siguientes se pasa el handle guardado para restaurar el contexto.
        # NOTA: transparent=True NO se usa: es exclusivo de Vertex AI (Enterprise)
        # y la Developer API rechaza la conexión si se envía.
        if self._session_handle:
            resumption = types.SessionResumptionConfig(handle=self._session_handle)
            logger.info("Reconectando con session resumption (contexto conversacional preservado).")
        else:
            resumption = types.SessionResumptionConfig()

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
            system_instruction=types.Content(parts=[types.Part.from_text(text=system_prompt)]),
            tools=gemini_tools,
            session_resumption=resumption,
            # Compresión de ventana de contexto para sesiones largas
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=100000,
                sliding_window=types.SlidingWindow(target_tokens=40000)
            ),
        )

        if self.affective_dialog:
            config.enable_affective_dialog = True

        if self.server_vad:
            # VAD en el servidor: detección de inicio/fin de habla y barge-in nativos.
            if "gemini-3.8" in self.model_name:
                # 3.8 Live (docs oficiales + medición propia): el modo soportado es
                # stream continuo con detección automática por defecto — al pausar
                # ~1s el servidor emite AudioStreamEnd y cierra el turno. Las
                # banderas detalladas (sensibilidad/activity_handling) NO se envían.
                config.realtime_input_config = types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(disabled=False),
                )
            else:
                config.realtime_input_config = types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                        silence_duration_ms=600,
                        prefix_padding_ms=300,
                    ),
                    activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
                    turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
                )

        async with self.client.aio.live.connect(model=self.model_name, config=config) as native_session:
            yield GeminiSession(native_session, on_session_handle=self._save_session_handle)
