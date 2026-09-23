from typing import AsyncGenerator, List, Any, Optional, Callable
from google.genai import types
from src.providers.base import (
    ProviderSession, AudioChunk, TextChunk, UserTextChunk, ToolCallItem,
    ToolCallRequest, Interrupted, TurnComplete, ToolResponseItem,
    GoAway, ToolCallsCancelled, SessionReconnectRequested
)
from src.utils.logging import get_logger

logger = get_logger("providers.gemini")


class GeminiSession(ProviderSession):
    """Adapta la sesión nativa de Gemini Live API a la interfaz unificada ProviderSession."""

    def __init__(self, native_session, on_session_handle: Optional[Callable[[str], None]] = None,
                 strict_turn_end: bool = False):
        self._session = native_session
        # Callback para persistir el handle de session resumption en el proveedor
        self._on_session_handle = on_session_handle
        # Protocolo 3.8+: turno cierra SOLO con turn_complete (+ interaction_status
        # == IDLE si viene). En modo legacy se conserva el or generation_complete.
        self._strict_turn_end = strict_turn_end

    async def send_audio(self, data: bytes, sample_rate: int = 16000) -> None:
        await self._session.send_realtime_input(
            audio={"data": data, "mime_type": f"audio/pcm;rate={sample_rate}"}
        )

    async def send_video(self, data: bytes, mime_type: str = "image/jpeg") -> None:
        await self._session.send_realtime_input(
            video={"mime_type": mime_type, "data": data}
        )

    async def send_text(self, text: str, end_of_turn: bool = False) -> None:
        # API moderna (session.send() está deprecado y se eliminará del SDK)
        await self._session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part.from_text(text=text)]
            ),
            turn_complete=end_of_turn
        )

    async def end_turn(self) -> None:
        """
        Señala el fin del stream de audio del usuario.
        audio_stream_end=True es la vía correcta durante streaming de audio
        (send_client_content(turn_complete=True) desincroniza el protocolo realtime).
        """
        await self._session.send_realtime_input(audio_stream_end=True)

    async def send_tool_response(self, responses: List[Any]) -> None:
        formatted = []
        for r in responses:
            if isinstance(r, ToolResponseItem):
                formatted.append(types.FunctionResponse(name=r.name, id=r.id, response=r.response))
            elif isinstance(r, dict):
                formatted.append(types.FunctionResponse(name=r.get("name"), id=r.get("id"), response=r.get("response", {})))
            else:
                formatted.append(r)
        await self._session.send_tool_response(function_responses=formatted)

    async def receive(self) -> AsyncGenerator[Any, None]:
        """Recibe mensajes de Gemini y los transforma a eventos de streaming normalizados."""
        async for msg in self._session.receive():
            if msg is None:
                continue

            # --- Gestión de ciclo de vida de la sesión ---

            # Captura del handle de session resumption (permite reconectar sin perder contexto)
            sr_update = getattr(msg, "session_resumption_update", None)
            if sr_update is not None:
                new_handle = getattr(sr_update, "new_handle", None)
                if getattr(sr_update, "resumable", False) and new_handle:
                    if self._on_session_handle:
                        self._on_session_handle(new_handle)
                    logger.debug("Handle de session resumption actualizado.")

            # El servidor avisa que la conexión morirá pronto: reconexión proactiva
            go_away = getattr(msg, "go_away", None)
            if go_away is not None:
                time_left = getattr(go_away, "time_left", None)
                logger.info(f"Servidor envió GoAway (quedan {time_left}). Iniciando reconexión proactiva...")
                yield GoAway(time_left=str(time_left) if time_left else None)
                raise SessionReconnectRequested("go_away recibido: reconexión proactiva")

            # Cancelación de tool calls por parte del servidor (ej: barge-in del usuario)
            tool_cancellation = getattr(msg, "tool_call_cancellation", None)
            if tool_cancellation is not None:
                ids = list(getattr(tool_cancellation, "ids", []) or [])
                logger.info(f"Tool calls canceladas por el servidor: {ids}")
                yield ToolCallsCancelled(ids=ids)

            # Telemetría de consumo de tokens
            usage = getattr(msg, "usage_metadata", None)
            if usage is not None:
                logger.debug(
                    f"Uso de tokens: total={getattr(usage, 'total_token_count', '?')} "
                    f"(prompt={getattr(usage, 'prompt_token_count', '?')}, "
                    f"respuesta={getattr(usage, 'response_token_count', '?')})"
                )

            # --- Contenido del servidor ---

            sc = getattr(msg, "server_content", None)
            if sc is not None:
                if getattr(sc, "interrupted", False):
                    yield Interrupted()

                # Transcripción del habla del usuario (STT en tiempo real)
                input_tx = getattr(sc, "input_transcription", None)
                if input_tx and getattr(input_tx, "text", None):
                    yield UserTextChunk(text=input_tx.text)

                # Transcripción del habla de Atlas (enviada en streaming por Gemini Live API)
                output_tx = getattr(sc, "output_transcription", None)
                if output_tx and getattr(output_tx, "text", None):
                    yield TextChunk(text=output_tx.text)

                model_turn = getattr(sc, "model_turn", None)
                if model_turn is not None and not getattr(sc, "interrupted", False):
                    for part in getattr(model_turn, "parts", []):
                        if getattr(part, "inline_data", None):
                            yield AudioChunk(data=part.inline_data.data)
                        elif getattr(part, "text", None):
                            yield TextChunk(text=part.text)

                is_turn_complete = getattr(sc, "turn_complete", False)
                is_generation_complete = getattr(sc, "generation_complete", False)
                interaction_status = getattr(sc, "interaction_status", None)
                if self._strict_turn_end:
                    # Protocolo 3.8+ (guía de migración Live API + docstring del SDK):
                    # cada turno emite DOS cierres — generation_complete al terminar la
                    # generación y turn_complete al terminar el playback — porque el
                    # modelo "espera a que el audio termine de reproducirse". Y
                    # turn_complete "ya no equivale a sesión ociosa": la señal
                    # autoritativa es interaction_status == IDLE (siempre enviada junto
                    # a turn_complete). Antes, mapear AMBOS flags duplicaba el evento
                    # por turno: la ventana follow-up se abría dos veces y el segundo
                    # TurnComplete re-despertaba a Atlas tras un sueño deliberado.
                    if is_turn_complete and interaction_status in (None, types.InteractionStatus.IDLE):
                        yield TurnComplete()
                elif is_turn_complete or is_generation_complete:
                    # Protocolo legacy (modelos 2.x/3.1): conservar comportamiento previo.
                    yield TurnComplete()


            tool_call = getattr(msg, "tool_call", None)
            if tool_call is not None:
                function_calls = getattr(tool_call, "function_calls", []) or []
                calls = []
                for fc in function_calls:
                    args_dict = dict(fc.args) if getattr(fc, "args", None) else {}
                    calls.append(ToolCallItem(id=fc.id, name=fc.name, args=args_dict))
                if calls:
                    yield ToolCallRequest(calls=calls)
